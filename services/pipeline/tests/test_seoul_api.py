import json
import logging
import traceback
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

import httpx
import pytest

from pipeline.cli import main
from pipeline.config import ConfigError, SeoulNewsSettings
from pipeline.sources.seoul_api import SeoulSourceError, collect_one, parse_notice

FIXTURE = Path(__file__).parent / "fixtures/seoul_one.xml"


def _xml(field: str, value: str | None) -> bytes:
    root = ElementTree.fromstring(FIXTURE.read_bytes())
    node = root.find(field)
    assert node is not None
    node.text = value
    return ElementTree.tostring(root, encoding="utf-8")


def test_preserves_ids_full_body_and_source_metadata() -> None:
    raw = parse_notice(FIXTURE.read_bytes())
    assert raw.source_board == "25" and raw.post_sn == "000123"
    assert raw.title == "테스트 서울시 공지"
    assert raw.registered_on == "2026-10-02 17:36:54"
    assert raw.modified_on == "2026-10-02 18:08:41"
    assert raw.department == "테스트 부서"
    assert raw.body_html.startswith("<p>전체 본문 &amp; 안내</p>")
    assert "a=1&amp;b=2" in raw.body_html
    assert raw.excerpt_html == "<p>짧은 미리보기</p>"
    assert raw.body_html != raw.excerpt_html
    assert not hasattr(raw, "url") and not hasattr(raw, "license_type")
    assert not hasattr(raw, "manager_phone")
    with pytest.raises(FrozenInstanceError):
        raw.post_sn = "123"


def test_large_body_is_not_truncated_or_replaced_with_excerpt() -> None:
    body = "<p>" + "본문 안내 " * 5000 + "</p>"
    raw = parse_notice(_xml("row/POST_CONTENT", body))
    assert raw.body_html == body
    assert len(raw.body_html) > 25000
    assert raw.excerpt_html == "<p>짧은 미리보기</p>"


@pytest.mark.parametrize("field", [
    "MANAGER_DEPT", "MODIFY_DATE", "POST_CONTENT", "POST_EXCERPT", "THUMB_URI",
])
def test_empty_optional_field_becomes_none(field: str) -> None:
    raw = parse_notice(_xml("row/" + field, " \n"))
    target = {
        "MANAGER_DEPT": "department", "MODIFY_DATE": "modified_on",
        "POST_CONTENT": "body_html", "POST_EXCERPT": "excerpt_html",
        "THUMB_URI": "thumbnail_url",
    }[field]
    assert getattr(raw, target) is None
    if field == "POST_CONTENT":
        assert raw.excerpt_html is not None  # No fallback to truncated excerpt.


@pytest.mark.parametrize("field", [
    "BLOG_ID", "BLOG_NAME", "POST_ID", "POST_TITLE", "PUBLISH_DATE", "POST_STATUS",
])
def test_blank_required_fields_rejected(field: str) -> None:
    with pytest.raises(SeoulSourceError):
        parse_notice(_xml("row/" + field, " "))


@pytest.mark.parametrize("field,value", [
    ("BLOG_ID", "99"), ("POST_ID", "1e10"), ("POST_ID", "１２３"),
    ("POST_STATUS", "draft"), ("POST_STATUS", "private"),
])
def test_invalid_identifiers_and_unpublished_rows_rejected(field: str, value: str) -> None:
    with pytest.raises(SeoulSourceError):
        parse_notice(_xml("row/" + field, value))


@pytest.mark.parametrize("field", ["BLOG_ID", "POST_CONTENT"])
def test_missing_duplicate_or_nested_field_rejected(field: str) -> None:
    for change in ("remove", "duplicate", "nested"):
        root = ElementTree.fromstring(FIXTURE.read_bytes())
        row = root.find("row")
        node = row.find(field)
        if change == "remove":
            row.remove(node)
        elif change == "duplicate":
            ElementTree.SubElement(row, field).text = "extra"
        else:
            ElementTree.SubElement(node, "unexpected").text = "extra"
        with pytest.raises(SeoulSourceError):
            parse_notice(ElementTree.tostring(root))


@pytest.mark.parametrize("value", ["0", "-1", "abc", "1.0", None])
def test_invalid_total_count_rejected(value: str | None) -> None:
    with pytest.raises(SeoulSourceError):
        parse_notice(_xml("list_total_count", value))


@pytest.mark.parametrize("code,retryable", [
    ("INFO-100", False), ("INFO-200", False), ("ERROR-500", True),
    ("ERROR-600", True), ("private-key", False),
])
def test_api_errors_with_http_200_are_safe(code: str, retryable: bool) -> None:
    content = f"<RESULT><CODE>{code}</CODE><MESSAGE>private-key</MESSAGE></RESULT>".encode()
    with pytest.raises(SeoulSourceError) as error:
        parse_notice(content)
    assert error.value.retryable is retryable
    assert "private-key" not in str(error.value)


@pytest.mark.parametrize("content", [
    b"not xml", b"\xff", b"<wrong/>", b"<SeoulNewsList/>",
    b'<!DOCTYPE SeoulNewsList [<!ENTITY x "secret">]><SeoulNewsList/>',
])
def test_malformed_response_rejected(content: bytes) -> None:
    with pytest.raises(SeoulSourceError):
        parse_notice(content)


def test_wrong_row_count_and_duplicate_result_rejected() -> None:
    for change in ("empty", "extra", "result"):
        root = ElementTree.fromstring(FIXTURE.read_bytes())
        if change == "empty":
            root.remove(root.find("row"))
        else:
            ElementTree.SubElement(root, "row" if change == "extra" else "RESULT")
        with pytest.raises(SeoulSourceError):
            parse_notice(ElementTree.tostring(root))


def test_request_index_timeouts_and_key_redaction(caplog: pytest.LogCaptureFixture) -> None:
    key = "private/key+value"
    settings = SeoulNewsSettings(key, 2.0, 7.0)

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.endswith(b"/xml/SeoulNewsList/1/1/")
        assert b"private%2Fkey%2Bvalue" in request.url.raw_path
        assert request.extensions["timeout"]["connect"] == 2.0
        assert request.extensions["timeout"]["read"] == 7.0
        return httpx.Response(200, content=FIXTURE.read_bytes())

    with caplog.at_level(logging.INFO, logger="httpx"):
        raw = collect_one(settings, transport=httpx.MockTransport(respond))
    assert raw.post_sn == "000123"
    assert "private" not in caplog.text and "[REDACTED]" in caplog.text


@pytest.mark.parametrize("status,retryable", [(302, False), (401, False), (429, True), (503, True)])
def test_http_status_and_no_redirects(status: int, retryable: bool) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://other.example/private-key"})

    with pytest.raises(SeoulSourceError) as error:
        collect_one(SeoulNewsSettings("private-key", 1, 2),
                    transport=httpx.MockTransport(respond))
    assert len(calls) == 1 and error.value.retryable is retryable
    assert "private-key" not in str(error.value)


@pytest.mark.parametrize("exception", [httpx.ReadTimeout, httpx.ConnectError])
def test_request_errors_do_not_leak_or_retry(exception: type[httpx.RequestError]) -> None:
    calls = []
    key = "private-" + "key"
    settings = SeoulNewsSettings(key, 1, 2)

    def fail(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise exception(key, request=request)

    with pytest.raises(SeoulSourceError) as error:
        collect_one(settings, transport=httpx.MockTransport(fail))
    assert error.value.retryable and len(calls) == 1
    assert key not in "".join(traceback.format_exception(error.value))


def test_seoul_settings_are_independent_and_secret_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="SEOUL_NEWS_API_KEY"):
        SeoulNewsSettings.from_env()
    monkeypatch.setenv("SEOUL_API_KEY", "legacy-key")
    monkeypatch.setenv("NOWON_NOTICE_API_KEY", "nowon-key")
    with pytest.raises(ConfigError, match="SEOUL_NEWS_API_KEY"):
        SeoulNewsSettings.from_env()
    monkeypatch.setenv("SEOUL_NEWS_API_KEY", " private/key ")
    settings = SeoulNewsSettings.from_env()
    assert settings.seoul_news_api_key == "private/key"
    assert "private" not in repr(settings)
    assert settings.redact("private/key private%2Fkey") == "[REDACTED] [REDACTED]"
    monkeypatch.setenv("HTTP_READ_TIMEOUT_SECONDS", "nan")
    with pytest.raises(ConfigError):
        SeoulNewsSettings.from_env()


def test_inspection_cli_never_connects_to_db(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SEOUL_NEWS_API_KEY", "sample")
    raw = parse_notice(FIXTURE.read_bytes())
    with (
        patch("pipeline.cli.collect_one_seoul", return_value=raw),
        patch("pipeline.cli.psycopg.connect") as connect,
    ):
        assert main(["inspect-one", "--source", "seoul"]) == 0
    connect.assert_not_called()
    output = json.loads(capsys.readouterr().out)
    assert output["stored"] is False
    assert output["body_html_length"] == len(raw.body_html)
    assert output["source_board"] == "25" and output["post_sn"] == "000123"


def test_inspection_cli_settings_and_source_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["inspect-one", "--source", "seoul"]) == 2
    monkeypatch.setenv("SEOUL_NEWS_API_KEY", "private-key")
    with patch("pipeline.cli.collect_one_seoul", side_effect=SeoulSourceError("API 오류")):
        assert main(["inspect-one", "--source", "seoul"]) == 1
    assert "private-key" not in capsys.readouterr().err


def test_seoul_config_cli_needs_no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEOUL_NEWS_API_KEY", "sample")
    assert main(["check-config", "--source", "seoul"]) == 0


def test_nowon_inspection_does_not_save(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from pipeline.models import RawNotice

    monkeypatch.setenv("NOWON_NOTICE_API_KEY", "sample")
    raw = RawNotice(
        category="nowon", source_board="1001", post_sn="00123", dong_group=None,
        is_pinned=False, title="공지", registered_on="2026-10-03", department=None,
        url="https://www.nowon.kr/test", body_html="<p>본문</p>", license_type="KOGL-4",
    )
    with patch("pipeline.cli.collect_one", return_value=raw), \
            patch("pipeline.cli.psycopg.connect") as connect:
        assert main(["inspect-one", "--source", "nowon"]) == 0
    connect.assert_not_called()
    assert json.loads(capsys.readouterr().out)["stored"] is False
