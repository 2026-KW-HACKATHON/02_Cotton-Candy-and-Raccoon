import json
import logging
from urllib.parse import quote

import httpx
import pytest

from pipeline.cli import main
from pipeline.config import ConfigError, NowonSettings
from pipeline.sources.nowon_api import NowonSourceError, collect_one, parse_notice

# Synthetic response matching the documented XML fields; no copied personal data.
XML = b'''<NowonNewsNoticeList><list_total_count>1</list_total_count>
<RESULT><CODE>INFO-000</CODE><MESSAGE>OK</MESSAGE></RESULT><row>
<ID>001234</ID><TITLE>Notice</TITLE><LINK>https://example.com/notice/001234</LINK>
<PUBDATE>2026-09-23</PUBDATE><DEPARTMENT>Team</DEPARTMENT><MANAGER/>
<DESCRIPTION>&lt;p&gt;A &amp;amp; B&lt;/p&gt;</DESCRIPTION>
</row></NowonNewsNoticeList>'''


def settings() -> NowonSettings:
    return NowonSettings('test/key+private', 2.0, 7.0)


def test_source_settings_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('NOWON_NOTICE_API_KEY', ' sample ')
    monkeypatch.setenv('DATABASE_URL', 'not-a-database')
    actual = NowonSettings.from_env()
    assert actual.nowon_notice_api_key == 'sample'
    assert actual.http_connect_timeout_seconds == 5.0
    assert actual.http_read_timeout_seconds == 20.0
    assert 'sample' not in repr(actual)


@pytest.mark.parametrize('key', ['', '   '])
def test_source_key_has_no_legacy_fallback(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    monkeypatch.setenv('SEOUL_API_KEY', 'legacy-key')
    monkeypatch.setenv('NOWON_NOTICE_API_KEY', key)
    with pytest.raises(ConfigError, match='NOWON_NOTICE_API_KEY'):
        NowonSettings.from_env()


@pytest.mark.parametrize('name', ['HTTP_CONNECT_TIMEOUT_SECONDS', 'HTTP_READ_TIMEOUT_SECONDS'])
@pytest.mark.parametrize('value', ['', '0', '-1', 'nan', 'inf', 'invalid'])
def test_source_timeout_validation(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str,
) -> None:
    monkeypatch.setenv('NOWON_NOTICE_API_KEY', 'sample')
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=name):
        NowonSettings.from_env()


def test_xml_preserves_identifier_and_html() -> None:
    notice = parse_notice(XML)
    assert notice.post_sn == '001234'
    assert notice.body_html == '<p>A &amp; B</p>'
    assert notice.registered_on == '2026-09-23'
    assert notice.department == 'Team'
    assert notice.category == 'nowon'
    assert notice.dong_group is None
    assert notice.is_pinned is False
    assert notice.license_type == 'KOGL-4'


def test_empty_optional_fields() -> None:
    notice = parse_notice(XML.replace(b'Team', b' ').replace(
        b'&lt;p&gt;A &amp;amp; B&lt;/p&gt;', b''))
    assert notice.department is None
    assert notice.body_html is None


@pytest.mark.parametrize('content', [
    b'{"ID":2.0260923172156128E16}', b'<html/>', b'<broken', b'\xff',
    b'<!DOCTYPE a><NowonNewsNoticeList/>',
    XML.replace(b'<ID>001234</ID>', b''),
    XML.replace(b'<ID>001234</ID>', b'<ID> </ID>'),
    XML.replace(b'<ID>001234</ID>', b'<ID>1</ID><ID>2</ID>'),
    XML.replace(b'<ID>001234</ID>', b'<ID><nested/></ID>'),
    XML.replace(b'<DEPARTMENT>Team</DEPARTMENT>', b''),
    XML.replace(b'<row>', b'<other>').replace(b'</row>', b'</other>'),
    XML.replace(b'</row>', b'</row><row/>'),
    XML.replace(b'<CODE>INFO-000</CODE>', b''),
])
def test_malformed_responses_fail_safely(content: bytes) -> None:
    with pytest.raises(NowonSourceError):
        parse_notice(content)


@pytest.mark.parametrize('code,retryable', [
    ('INFO-100', False), ('INFO-200', False), ('ERROR-500', True),
    ('ERROR-600', True), ('untrusted-secret', False),
])
@pytest.mark.parametrize('wrapped', [False, True])
def test_result_errors(code: str, retryable: bool, wrapped: bool) -> None:
    content = f'<RESULT><CODE>{code}</CODE><MESSAGE>untrusted-secret</MESSAGE></RESULT>'
    if wrapped:
        content = f'<NowonNewsNoticeList>{content}</NowonNewsNoticeList>'
    with pytest.raises(NowonSourceError) as caught:
        parse_notice(content.encode())
    assert caught.value.retryable is retryable
    assert 'untrusted-secret' not in str(caught.value)


def test_request_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.raw_path == b'/test%2Fkey%2Bprivate/xml/NowonNewsNoticeList/1/1/'
        assert request.extensions['timeout']['connect'] == 2.0
        assert request.extensions['timeout']['read'] == 7.0
        return httpx.Response(200, content=XML)

    logger = logging.getLogger('httpx')
    before = list(logger.filters)
    with caplog.at_level(logging.INFO, logger='httpx'):
        notice = collect_one(settings(), transport=httpx.MockTransport(handler))
    assert notice.post_sn == '001234'
    assert len(requests) == 1
    assert logger.filters == before
    assert '[REDACTED]' in caplog.text
    assert settings().nowon_notice_api_key not in caplog.text
    assert quote(settings().nowon_notice_api_key, safe='') not in caplog.text


@pytest.mark.parametrize('status,retryable', [
    (301, False), (400, False), (401, False), (403, False), (404, False),
    (429, True), (500, True), (502, True), (503, True), (504, True),
])
def test_http_failures_do_not_retry_or_redirect(status: int, retryable: bool) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, headers={'Location': 'https://example.com'},
                              text='test/key+private')

    with pytest.raises(NowonSourceError) as caught:
        collect_one(settings(), transport=httpx.MockTransport(handler))
    assert caught.value.retryable is retryable
    assert str(status) in str(caught.value)
    assert 'private' not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    'error_type', [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError],
)
def test_network_failure_is_safe(error_type: type[httpx.RequestError]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type(f'failed {request.url}', request=request)

    with pytest.raises(NowonSourceError) as caught:
        collect_one(settings(), transport=httpx.MockTransport(handler))
    assert caught.value.retryable is True
    assert 'private' not in str(caught.value)
    assert caught.value.__suppress_context__ is True


def test_cli_source_settings_and_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv('NOWON_NOTICE_API_KEY', 'sample')
    assert main(['check-config', '--source', 'nowon']) == 0
    capsys.readouterr()
    monkeypatch.setattr('pipeline.cli.collect_one', lambda config: parse_notice(XML))
    monkeypatch.setattr('pipeline.cli.fetch_notice_page', lambda notice, config: (
        notice.url, '<tr><th>첨부파일</th><td>첨부파일이 없습니다.</td></tr>',
    ))
    assert main(['collect-one', '--source', 'nowon']) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result['post_sn'] == '001234'
    assert result['body_html_length'] == len('<p>A &amp; B</p>')
    assert 'body_html' not in result
    assert output.err == ''


def test_cli_missing_key_does_not_collect(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def unexpected(config: NowonSettings) -> None:
        pytest.fail('must not request without source key')

    monkeypatch.setattr('pipeline.cli.collect_one', unexpected)
    assert main(['collect-one', '--source', 'nowon']) == 2
    assert 'NOWON_NOTICE_API_KEY' in capsys.readouterr().err


def test_cli_safe_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv('NOWON_NOTICE_API_KEY', 'test/key+private')

    def fail(config: NowonSettings) -> None:
        raise NowonSourceError('failed test%2Fkey%2Bprivate', retryable=True)

    monkeypatch.setattr('pipeline.cli.collect_one', fail)
    assert main(['collect-one', '--source', 'nowon']) == 1
    output = capsys.readouterr()
    assert output.out == ''
    assert '[REDACTED]' in output.err
    assert 'private' not in output.err
