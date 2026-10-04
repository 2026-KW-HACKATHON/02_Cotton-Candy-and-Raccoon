import json
from dataclasses import replace
from datetime import date
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from pipeline.attachments.seoul_html import SeoulAttachmentError, extract_files, normalize_file_url
from pipeline.cli import main
from pipeline.collect_seoul import prepare_one
from pipeline.config import SeoulNewsSettings
from pipeline.models import RawSeoulNotice
from pipeline.sources.seoul_api import parse_notice
from pipeline.sources.seoul_page import (
    BOARD_SLUGS,
    SeoulPageError,
    fetch_notice_page,
    notice_url,
    parse_page,
)
from pipeline.transform.seoul import SeoulTransformError, transform_seoul_notice

FIXTURES = Path(__file__).parent / "fixtures"
HTML = (FIXTURES / "seoul_page.html").read_text(encoding="utf-8")
SETTINGS = SeoulNewsSettings("sample", 5.0, 20.0)


def raw() -> RawSeoulNotice:
    return parse_notice((FIXTURES / "seoul_one.xml").read_bytes())


def test_prepare_contract_preserves_body_ids_dates_and_roles() -> None:
    page = parse_page(raw(), HTML)
    notice = transform_seoul_notice(raw(), page)
    files = extract_files(page)
    assert notice.category == "seoul" and notice.source_board == "25"
    assert notice.post_sn == "000123"
    assert notice.dong_group is None and notice.is_pinned is False
    assert notice.registered_on == date(2026, 10, 2)
    assert notice.license_type == "KOGL-4"
    assert notice.url == "https://news.seoul.go.kr/env/archives/000123"
    assert notice.body_html == page.body_html
    assert "전체 본문 &amp; 안내" in notice.body_html
    assert "공유 버튼" not in notice.body_html and "frmRating" not in notice.body_html
    assert len(files) == 3
    assert [f.kind for f in files].count("attachment") == 2
    assert [f.kind for f in files].count("inline_image") == 1
    pdf = next(f for f in files if f.file_name == "안내.pdf")
    assert pdf.url.endswith("안내.pdf?a=1&b=2")
    for f in files:
        assert (f.category, f.source_board, f.post_sn) == ("seoul", "25", "000123")
        assert f.file_sn is None and f.file_id is None
        assert f.file_key == "url:" + sha256(f.url.encode("utf-8")).hexdigest()
        assert f.url.startswith("https://") and "#" not in f.url
    poster = [f for f in files if f.file_name == "poster.jpg"]
    assert len(poster) == 2 and poster[0].file_key == poster[1].file_key


@pytest.mark.parametrize("board,slug", list(BOARD_SLUGS.items()))
def test_all_eight_board_urls(board: str, slug: str) -> None:
    assert notice_url(replace(raw(), source_board=board)) == (
        f"https://news.seoul.go.kr/{slug}/archives/000123"
    )


def test_article_without_rating_uses_exact_canonical_and_og_url() -> None:
    html = (FIXTURES / "seoul_page_without_rating.html").read_text(encoding="utf-8")
    notice = replace(raw(), source_board="24")
    page = parse_page(notice, html)
    assert "식별 폼 없는 정상 공지 본문" in page.body_html
    assert page.post_sn == "000123" and page.source_board == "24"


@pytest.mark.parametrize(
    "change",
    [
        "missing_og",
        "wrong_og",
        "duplicate_og",
        "wrong_canonical",
        "missing_body",
        "wrong_form",
        "partial_form",
        "duplicate_form",
    ],
)
def test_rating_fallback_does_not_accept_ambiguous_or_conflicting_identity(change: str) -> None:
    html = (FIXTURES / "seoul_page_without_rating.html").read_text(encoding="utf-8")
    og = '<meta property="og:url" content="https://news.seoul.go.kr/economy/archives/000123">'
    form = (
        '<form id="frmRating"><input name="blog_id" value="24">'
        '<input name="post_id" value="000123"></form>'
    )
    if change == "missing_og":
        html = html.replace(og, "")
    elif change == "wrong_og":
        html = html.replace(og, og.replace("000123", "999"))
    elif change == "duplicate_og":
        html = html.replace(og, og + og)
    elif change == "wrong_canonical":
        html = html.replace(
            'rel="canonical" href="https://news.seoul.go.kr/economy/archives/000123"',
            'rel="canonical" href="https://news.seoul.go.kr/env/archives/000123"',
        )
    elif change == "missing_body":
        html = html.replace('id="post_content"', 'id="other"')
    elif change == "wrong_form":
        html = html.replace("</body>", form.replace('value="24"', 'value="25"') + "</body>")
    elif change == "partial_form":
        html = html.replace("</body>", '<form id="frmRating"></form></body>')
    else:
        html = html.replace("</body>", form + form + "</body>")
    with pytest.raises(SeoulPageError) as caught:
        parse_page(replace(raw(), source_board="24"), html)
    assert caught.value.reason_code == (
        "page_body_invalid" if change == "missing_body" else "page_identity_mismatch"
    )


@pytest.mark.parametrize(
    "status,reason",
    [
        (302, "page_redirect"),
        (404, "page_not_found"),
        (410, "page_not_found"),
        (403, "page_http_403"),
        (429, "rate_limited"),
        (503, "page_http_503"),
    ],
)
def test_http_error_reason_is_specific_without_following_redirects(
    status: int, reason: str
) -> None:
    requests = []

    def response(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, headers={"location": "http://127.0.0.1/private"})

    with pytest.raises(SeoulPageError) as caught:
        fetch_notice_page(raw(), SETTINGS, transport=httpx.MockTransport(response))
    assert caught.value.reason_code == reason
    assert len(requests) == 1


@pytest.mark.parametrize(
    "failure,reason",
    [(httpx.ReadTimeout, "page_timeout"), (httpx.ConnectError, "page_connection_failed")],
)
def test_network_error_reason_is_specific(failure: type[httpx.RequestError], reason: str) -> None:
    def response(request: httpx.Request) -> httpx.Response:
        raise failure("test", request=request)

    with pytest.raises(SeoulPageError) as caught:
        fetch_notice_page(raw(), SETTINGS, transport=httpx.MockTransport(response))
    assert caught.value.reason_code == reason and caught.value.retryable


@pytest.mark.parametrize(
    "before,after",
    [
        ("archives/000123", "archives/999"),
        ('name="blog_id" value="25"', 'name="blog_id" value="24"'),
        ('name="post_id" value="000123"', 'name="post_id" value="123"'),
        ('id="post_content"', 'id="not_content"'),
        ("wp본문시작", "본문시작"),
        ("wp본문끝", "본문끝"),
        ('id="frmRating"', 'id="wrong_rating"'),
    ],
)
def test_wrong_page_identity_or_body_boundary_rejected(before: str, after: str) -> None:
    with pytest.raises(SeoulPageError):
        parse_page(raw(), HTML.replace(before, after))


@pytest.mark.parametrize("kind", [1, 2, 3, 4])
def test_each_license_is_read_from_article_not_hardcoded(kind: int) -> None:
    assert parse_page(raw(), HTML.replace("licenseType4", f"licenseType{kind}")).license_type == (
        f"KOGL-{kind}"
    )


def test_missing_license_is_none_and_conflicting_unknown_license_is_error() -> None:
    assert parse_page(raw(), HTML.replace("www.kogl.or.kr", "example.com")).license_type is None
    with pytest.raises(SeoulPageError):
        parse_page(raw(), HTML.replace("licenseType4", "licenseType9"))
    with pytest.raises(SeoulPageError):
        parse_page(
            raw(),
            HTML.replace(
                'rel="license">',
                'rel="license"></a><a href="//www.kogl.or.kr/info/licenseType1.do">',
            ),
        )


@pytest.mark.parametrize("value", ["2026-02-30 12:00:00", "2026-10-02", "2026-10-02 25:00:00"])
def test_invalid_dates_rejected(value: str) -> None:
    with pytest.raises(SeoulTransformError):
        transform_seoul_notice(replace(raw(), registered_on=value), parse_page(raw(), HTML))


def test_transform_checks_parent_key_empty_body_and_image_only_body() -> None:
    page = parse_page(raw(), HTML)
    with pytest.raises(SeoulTransformError):
        transform_seoul_notice(raw(), replace(page, source_board="24"))
    assert transform_seoul_notice(raw(), replace(page, body_html="<p>&nbsp;</p>")).body_html is None
    body = '<img src="/env/files/poster.jpg">'
    assert transform_seoul_notice(raw(), replace(page, body_html=body)).body_html == body


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///tmp/a",
        "https://u:p@example.com/a",
        "https://news.seoul.go.kr:9999/a",
        "data:image/png;base64,eA==",
    ],
)
def test_bad_file_urls_rejected(url: str) -> None:
    with pytest.raises(SeoulAttachmentError):
        normalize_file_url(notice_url(raw()), url)


def test_file_metadata_does_not_upgrade_external_http_or_invent_wp_image_uuid() -> None:
    page = replace(
        parse_page(raw(), HTML),
        body_html=(
            '<img class="wp-image-123" src="http://example.com/p.jpg">'
            '<a href="/env/endpoint" download>다운로드</a>'
        ),
    )
    with pytest.raises(SeoulAttachmentError):
        extract_files(page)
    item = extract_files(replace(page, body_html=page.body_html.split("<a")[0]))[0]
    assert item.url == "http://example.com/p.jpg"
    assert item.file_id is None


@pytest.mark.parametrize("status", [302, 404, 429, 503])
def test_http_failure_is_not_empty_success(status: int) -> None:
    def response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"location": "https://example.com/"})

    with pytest.raises(SeoulPageError) as caught:
        fetch_notice_page(raw(), SETTINGS, transport=httpx.MockTransport(response))
    assert caught.value.retryable == (status in (429, 503))


def test_timeout_and_non_html_rejected() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("unsafe network details", request=request)

    with pytest.raises(SeoulPageError) as caught:
        fetch_notice_page(raw(), SETTINGS, transport=httpx.MockTransport(timeout))
    assert caught.value.retryable and "unsafe" not in str(caught.value)
    with pytest.raises(SeoulPageError):
        fetch_notice_page(
            raw(),
            SETTINGS,
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, text=HTML, headers={"content-type": "application/json"}
                ),
            ),
        )


def test_prepare_and_cli_never_connect_to_db(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests = []

    def api(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, content=(FIXTURES / "seoul_one.xml").read_bytes())

    def page(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    record, files = prepare_one(
        SETTINGS, api_transport=httpx.MockTransport(api), page_transport=httpx.MockTransport(page)
    )
    assert requests == ["/sample/xml/SeoulNewsList/1/1/", "/env/archives/000123"]
    monkeypatch.setenv("SEOUL_NEWS_API_KEY", "sample")
    with (
        patch("pipeline.cli.prepare_one", return_value=(record, files)),
        patch(
            "pipeline.cli.psycopg.connect",
            side_effect=AssertionError("must not connect"),
        ),
    ):
        assert main(["inspect-prepared", "--source", "seoul"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["stored"] is False and output["registered_on"] == "2026-10-02"
    assert output["attachment_count"] == 2 and output["inline_image_count"] == 1


def test_prepared_cli_errors_do_not_store(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["inspect-prepared", "--source", "seoul"]) == 2
    capsys.readouterr()
    monkeypatch.setenv("SEOUL_NEWS_API_KEY", "sample")
    with (
        patch("pipeline.cli.prepare_one", side_effect=SeoulPageError("원문 누락")),
        patch(
            "pipeline.cli.psycopg.connect",
            side_effect=AssertionError("must not connect"),
        ),
    ):
        assert main(["inspect-prepared", "--source", "seoul"]) == 1
    assert "원문 누락" in capsys.readouterr().err


def test_malformed_download_url_is_safe_error() -> None:
    page = replace(parse_page(raw(), HTML), body_html='<a href="https://[/x.pdf">파일</a>')
    with pytest.raises(SeoulAttachmentError):
        extract_files(page)


def test_observed_theme_tag_icon_inside_body_is_not_a_file() -> None:
    page = replace(
        parse_page(raw(), HTML),
        body_html=(
            '<img src="//news.seoul.go.kr/wp-content/themes/seoul/images/common/icon_tag.gif">'
            '<a href="/env/files/notice.pdf">PDF</a>'
        ),
    )
    files = extract_files(page)
    assert len(files) == 1 and files[0].kind == "attachment"
