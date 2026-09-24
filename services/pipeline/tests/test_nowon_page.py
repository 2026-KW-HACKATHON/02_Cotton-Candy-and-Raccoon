import json
from urllib.parse import urlsplit

import httpx
import pytest

from pipeline.attachments.nowon_html import (
    AttachmentError,
    extract_files,
    extract_page_files,
    merge_files,
)
from pipeline.cli import main
from pipeline.config import NowonSettings
from pipeline.models import RawNotice
from pipeline.sources.nowon_page import NowonPageError, fetch_notice_page

PAGE_URL = (
    "https://www.nowon.kr/www/user/bbs/BD_selectBbs.do"
    "?q_bbsCode=1001&q_estnColumn1=11&q_bbscttSn=001234"
)
PAGE_HTML = '''<html><table><tr><th scope="row">첨부파일</th><td>
<ul class="file-list"><li><a title="다운로드" href=
"/component/file/ND_fileDownload.do?q_fileSn=01&amp;q_fileId=abc">보고서.pdf</a>
</li></ul></td></tr></table></html>'''
EMPTY_PAGE_HTML = "<table><tr><th>첨부파일</th><td>첨부파일이 없습니다.</td></tr></table>"


def notice(body_html: str | None = None, *, url: str | None = None) -> RawNotice:
    return RawNotice(
        category="nowon", dong_group=None, is_pinned=False,
        post_sn="001234", title="Notice", department=None,
        registered_on="2026-09-23", url=url or PAGE_URL.replace(
            "https://www.nowon.kr", "http://www.nowon.kr:80"),
        body_html=body_html, license_type="KOGL-4",
    )


def settings() -> NowonSettings:
    return NowonSettings("sample", 2.0, 7.0)


def test_page_attachment_is_found_outside_description() -> None:
    source = notice()
    assert extract_files(source) == []
    files = extract_page_files(source, PAGE_HTML, PAGE_URL)
    assert len(files) == 1
    assert files[0].category == "nowon"
    assert files[0].post_sn == "001234"
    assert files[0].kind == "attachment"
    assert files[0].file_sn == "01"
    assert files[0].file_id == "abc"
    assert files[0].file_name == "보고서.pdf"
    assert files[0].url == (
        "https://www.nowon.kr/component/file/ND_fileDownload.do?q_fileSn=01&q_fileId=abc"
    )


def test_explicit_empty_attachment_row_is_valid() -> None:
    assert extract_page_files(notice(), EMPTY_PAGE_HTML, PAGE_URL) == []


@pytest.mark.parametrize("html", [
    "<html><title>Sign in</title></html>",
    "<tr><th>첨부파일</th><td></td></tr>",
    EMPTY_PAGE_HTML + EMPTY_PAGE_HTML,
    '<tr><th>첨부파일</th><td><ul class="file-list">'
    '<li><a href="/file">PDF</a></li></ul></td></tr>',
])
def test_incomplete_page_cannot_mean_no_attachments(html: str) -> None:
    with pytest.raises(AttachmentError):
        extract_page_files(notice(), html, PAGE_URL)


def test_duplicate_page_and_body_image_favors_page_attachment() -> None:
    source = notice('<img src="/file?q_fileSn=01&amp;q_fileId=abc">')
    merged = merge_files(
        extract_files(source), extract_page_files(source, PAGE_HTML, PAGE_URL),
    )
    assert len(merged) == 1
    assert merged[0].kind == "attachment"
    assert merged[0].file_name == "보고서.pdf"


def test_cross_source_identifier_conflict_fails() -> None:
    source = notice('<img src="/file?q_fileSn=01&amp;q_fileId=different">')
    with pytest.raises(AttachmentError, match="file_sn"):
        merge_files(extract_files(source), extract_page_files(source, PAGE_HTML, PAGE_URL))


def test_page_files_with_same_sn_and_different_ids_fail() -> None:
    html = '''<tr><th>첨부파일</th><td><ul class="file-list">
    <li><a href="/file?q_fileSn=9&amp;q_fileId=first">one.pdf</a></li>
    <li><a href="/file?q_fileSn=9&amp;q_fileId=second">two.hwp</a></li>
    </ul></td></tr>'''
    with pytest.raises(AttachmentError, match="file_sn") as caught:
        extract_page_files(notice(), html, PAGE_URL)
    assert "first" not in str(caught.value)
    assert "second" not in str(caught.value)


def test_fetch_page_uses_validated_https_url_once() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == PAGE_URL
        assert request.extensions["timeout"]["connect"] == 2.0
        assert request.extensions["timeout"]["read"] == 7.0
        return httpx.Response(
            200, text=PAGE_HTML, headers={"Content-Type": "text/html;charset=UTF-8"},
        )

    url, html = fetch_notice_page(notice(), settings(), transport=httpx.MockTransport(handler))
    assert url == PAGE_URL
    assert html == PAGE_HTML
    assert len(requests) == 1


@pytest.mark.parametrize("bad_url", [
    "https://other.example/www/user/bbs/BD_selectBbs.do?q_bbsCode=1001&q_bbscttSn=001234",
    "https://www.nowon.kr.evil.example/www/user/bbs/BD_selectBbs.do"
    "?q_bbsCode=1001&q_bbscttSn=001234",
    PAGE_URL.replace("001234", "different"),
    PAGE_URL.replace("q_bbsCode=1001", "q_bbsCode=1003"),
    PAGE_URL.replace("https://", "https://user:pass@"),
    "http://[invalid",
])
def test_bad_api_link_is_rejected_before_network(bad_url: str) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"unexpected request to {urlsplit(str(request.url)).hostname}")

    with pytest.raises(NowonPageError):
        fetch_notice_page(
            notice(url=bad_url), settings(), transport=httpx.MockTransport(unexpected),
        )


@pytest.mark.parametrize("status,retryable", [(302, False), (404, False), (429, True), (503, True)])
def test_page_http_failure_does_not_redirect_or_retry(status: int, retryable: bool) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://other.example/"})

    with pytest.raises(NowonPageError) as caught:
        fetch_notice_page(notice(), settings(), transport=httpx.MockTransport(handler))
    assert caught.value.retryable is retryable
    assert len(calls) == 1


def test_non_html_response_fails() -> None:
    with pytest.raises(NowonPageError, match="HTML"):
        fetch_notice_page(notice(), settings(), transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=b"pdf", headers={"Content-Type": "application/pdf"},
            ),
        ))


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError])
def test_page_network_error_is_safe_and_retryable(error_type: type[httpx.RequestError]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type(f"private {request.url}", request=request)

    with pytest.raises(NowonPageError) as caught:
        fetch_notice_page(notice(), settings(), transport=httpx.MockTransport(handler))
    assert caught.value.retryable is True
    assert "private" not in str(caught.value)
    assert caught.value.__suppress_context__ is True


def test_cli_includes_original_page_attachments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("NOWON_NOTICE_API_KEY", "sample")
    monkeypatch.setattr("pipeline.cli.collect_one", lambda config: notice())
    monkeypatch.setattr("pipeline.cli.fetch_notice_page", lambda source, config: (
        PAGE_URL, PAGE_HTML,
    ))
    assert main(["collect-one", "--source", "nowon"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["attachment_count"] == 1
    assert "보고서.pdf" not in output.out


def test_cli_page_failure_has_no_partial_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("NOWON_NOTICE_API_KEY", "sample")
    monkeypatch.setattr("pipeline.cli.collect_one", lambda config: notice())

    def fail(source: RawNotice, config: NowonSettings) -> tuple[str, str]:
        raise NowonPageError("원문 페이지 연결 실패", retryable=True)

    monkeypatch.setattr("pipeline.cli.fetch_notice_page", fail)
    assert main(["collect-one", "--source", "nowon"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "원문 페이지 연결 실패" in output.err
    assert "다시 실행" in output.err
