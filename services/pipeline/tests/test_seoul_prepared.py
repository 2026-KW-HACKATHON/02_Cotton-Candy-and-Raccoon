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
from pipeline.collect_seoul import prepare_notice, prepare_one
from pipeline.config import SeoulNewsSettings
from pipeline.models import RawSeoulNotice
from pipeline.sources.seoul_api import BOARD_SLUGS, notice_url, parse_notice
from pipeline.transform.seoul import SeoulTransformError, transform_seoul_notice

FIXTURES = Path(__file__).parent / "fixtures"
HTML = (
    '<p>전체 본문 &amp; 안내</p><a href="/env/files/안내.pdf?a=1&amp;b=2">PDF</a>'
    '<a href="/env/files/poster.jpg">첨부</a><img src="/env/files/poster.jpg">'
    '<img src="/env/files/poster.jpg"><a href="/env/files/안내.pdf?a=1&amp;b=2">PDF</a>'
)
SETTINGS = SeoulNewsSettings("sample", 5.0, 20.0)


def raw() -> RawSeoulNotice:
    return parse_notice((FIXTURES / "seoul_one.xml").read_bytes())


def test_prepare_contract_preserves_body_ids_dates_and_roles() -> None:
    page = transform_seoul_notice(replace(raw(), body_html=HTML))
    notice = page
    files = extract_files(page)
    assert notice.category == "seoul" and notice.source_board == "25"
    assert notice.post_sn == "000123"
    assert notice.dong_group is None and notice.is_pinned is False
    assert notice.registered_on == date(2026, 10, 2)
    assert notice.license_type is None
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


@pytest.mark.parametrize("value", ["2026-02-30 12:00:00", "2026-10-02", "2026-10-02 25:00:00"])
def test_invalid_dates_rejected(value: str) -> None:
    with pytest.raises(SeoulTransformError):
        transform_seoul_notice(replace(raw(), registered_on=value))


def test_empty_body_never_uses_excerpt_or_thumbnail() -> None:
    for body in (None, "<p>&nbsp;</p>"):
        record, files = prepare_notice(replace(raw(), body_html=body))
        assert record.body_html is None and files == ()
    body = '<img src="/env/files/poster.jpg">'
    assert transform_seoul_notice(replace(raw(), body_html=body)).body_html == body


def test_large_body_and_roles_are_preserved_without_requests() -> None:
    body = "<p>" + "본문" * 20000 + "</p>" + HTML
    with patch("httpx.Client", side_effect=AssertionError("No page/file requests")):
        record, files = prepare_notice(replace(raw(), body_html=body))
    assert record.body_html == body and len(files) == 3


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
        transform_seoul_notice(replace(raw(), body_html=HTML)),
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


def test_prepare_and_cli_never_connect_to_db(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests = []

    def api(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, content=(FIXTURES / "seoul_one.xml").read_bytes())

    client = httpx.Client(transport=httpx.MockTransport(api))
    with patch("httpx.Client", return_value=client):
        record, files = prepare_one(SETTINGS)
    assert requests == ["/sample/xml/SeoulNewsList/1/1/"]
    assert record.body_html == raw().body_html
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
    assert output["attachment_count"] == 1 and output["inline_image_count"] == 1


def test_prepared_cli_errors_do_not_store(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["inspect-prepared", "--source", "seoul"]) == 2
    capsys.readouterr()
    monkeypatch.setenv("SEOUL_NEWS_API_KEY", "sample")
    with (
        patch("pipeline.cli.prepare_one", side_effect=SeoulTransformError("날짜 오류")),
        patch(
            "pipeline.cli.psycopg.connect",
            side_effect=AssertionError("must not connect"),
        ),
    ):
        assert main(["inspect-prepared", "--source", "seoul"]) == 1
    assert "날짜 오류" in capsys.readouterr().err


def test_malformed_download_url_is_safe_error() -> None:
    page = replace(
        transform_seoul_notice(replace(raw(), body_html=HTML)),
        body_html='<a href="https://[/x.pdf">파일</a>',
    )
    with pytest.raises(SeoulAttachmentError):
        extract_files(page)


def test_observed_theme_tag_icon_inside_body_is_not_a_file() -> None:
    page = replace(
        transform_seoul_notice(replace(raw(), body_html=HTML)),
        body_html=(
            '<img src="//news.seoul.go.kr/wp-content/themes/seoul/images/common/icon_tag.gif">'
            '<a href="/env/files/notice.pdf">PDF</a>'
        ),
    )
    files = extract_files(page)
    assert len(files) == 1 and files[0].kind == "attachment"


@pytest.mark.parametrize(
    "filename",
    [
        "nLc-logo-culture.png",
        "nLc-top-facebook.png",
        "nLc-top-instargram.png",
        "nLc-top-blog.png",
    ],
)
@pytest.mark.parametrize(
    "prefix",
    [
        "https://culture.seoul.go.kr",
        "http://culture.seoul.go.kr",
        "//culture.seoul.go.kr",
        "https://culture.seoul.go.kr:443",
    ],
)
def test_known_culture_decoration_excluded_without_changing_body(
    filename: str,
    prefix: str,
) -> None:
    url = f"{prefix}/_ui/images/main/cnl-common/{filename}?v=1#preview"
    body = f'<img src="{url}"><img src="/culture/files/poster.jpg">'
    with patch("httpx.Client", side_effect=AssertionError("No network requests")):
        record, files = prepare_notice(replace(raw(), body_html=body))
    assert record.body_html == body
    assert len(files) == 1 and files[0].url.endswith("/culture/files/poster.jpg")


@pytest.mark.parametrize(
    "url",
    [
        "https://culture.seoul.go.kr/_ui/images/main/cnl-common/nLc-title-exhibition_2026.png",
        "https://culture.seoul.go.kr/_ui/images/main/cnl-common/poster.png",
        "https://culture.seoul.go.kr/cmmn/file/imageSrc.do?fileStreCours=a&streFileNm=b",
        "https://culture.seoul.go.kr/uploads/nLc-logo-culture.png",
        "https://example.com/_ui/images/main/cnl-common/nLc-logo-culture.png",
        "https://culture.seoul.go.kr.example.com/_ui/images/main/cnl-common/nLc-logo-culture.png",
    ],
)
def test_unknown_banner_poster_or_other_host_is_not_discarded(url: str) -> None:
    _, files = prepare_notice(replace(raw(), body_html=f'<img src="{url}" width="1" alt="logo">'))
    assert len(files) == 1 and files[0].kind == "inline_image"
    assert files[0].url == url


def test_decoration_filter_does_not_remove_explicit_attachment_role() -> None:
    url = "https://culture.seoul.go.kr/_ui/images/main/cnl-common/nLc-logo-culture.png"
    body = f'<a href="{url}">첨부</a><img src="{url}">'
    _, files = prepare_notice(replace(raw(), body_html=body))
    assert len(files) == 1 and files[0].kind == "attachment"
