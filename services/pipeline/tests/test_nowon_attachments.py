import json
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from pipeline.attachments.nowon_html import AttachmentError, extract_files, extract_page_files
from pipeline.cli import main
from pipeline.models import RawNotice


def notice(html: str | None, *, category: str = "nowon") -> RawNotice:
    return RawNotice(
        category=category,
        dong_group=None,
        is_pinned=False,
        post_sn="001234",
        title="Sample",
        department=None,
        registered_on="2026-09-23",
        url=(
            "https://www.nowon.kr/www/user/bbs/BD_selectBbs.do"
            "?q_bbsCode=1001&q_estnColumn1=11&q_bbscttSn=001234"
        ),
        body_html=html,
        license_type="KOGL-4",
    )


def test_repeated_inline_image_is_one_file() -> None:
    html = (
        '<img src="/component/file/ND_fileDownload.do?q_fileSn=001&amp;q_fileId=id-A">'
        '<img src="/component/file/ND_fileDownload.do?q_fileSn=001&amp;q_fileId=id-A">'
    )
    files = extract_files(notice(html))
    assert len(files) == 1
    assert files[0].category == "nowon"
    assert files[0].post_sn == "001234"
    assert files[0].kind == "inline_image"
    assert files[0].file_sn == "001"
    assert files[0].file_id == "id-A"
    assert files[0].file_name is None
    assert files[0].url == (
        "https://www.nowon.kr/component/file/ND_fileDownload.do?q_fileSn=001&q_fileId=id-A"
    )


@pytest.mark.parametrize(
    "reference",
    [
        "/component/file/ND_fileDownload.do?q_fileSn=1&amp;q_fileId=image-a",
        "http://www.nowon.kr:80/component/file/ND_fileDownload.do?q_fileSn=1&amp;q_fileId=image-a",
        "//www.nowon.kr:80/component/file/ND_fileDownload.do?q_fileSn=1&amp;q_fileId=image-a",
    ],
)
def test_api_http_link_produces_https_inline_image(reference: str) -> None:
    raw = replace(
        notice(f'<img src="{reference}">'),
        url="http://www.nowon.kr:80/www/user/bbs/BD_selectBbs.do"
        "?q_bbsCode=1001&q_estnColumn1=11&q_bbscttSn=001234",
    )
    files = extract_files(raw)
    assert len(files) == 1
    assert files[0].url == (
        "https://www.nowon.kr/component/file/ND_fileDownload.do?q_fileSn=1&q_fileId=image-a"
    )


def test_relative_and_absolute_nowon_image_references_deduplicate() -> None:
    raw = replace(
        notice(
            '<img src="/file?q_fileSn=1&amp;q_fileId=image-a">'
            '<img src="http://www.nowon.kr:80/file?q_fileSn=1&amp;q_fileId=image-a">'
        ),
        url="http://www.nowon.kr:80/www/user/bbs/BD_selectBbs.do?q_bbsCode=1001&q_bbscttSn=001234",
    )
    files = extract_files(raw)
    assert len(files) == 1
    assert files[0].url == "https://www.nowon.kr/file?q_fileSn=1&q_fileId=image-a"


def test_external_http_file_url_is_not_rewritten() -> None:
    url = "http://files.example.org/file?q_fileSn=1&q_fileId=image-a"
    files = extract_files(notice(f'<img src="{url}">'))
    assert len(files) == 1
    assert files[0].url == url


@pytest.mark.parametrize("anchor_first", [True, False])
def test_attachment_and_inline_image_keep_both_roles(anchor_first: bool) -> None:
    image = '<img src="/file?q_fileSn=01&amp;q_fileId=abc">'
    link = '<a href="/file?q_fileSn=01&amp;q_fileId=abc" download="report.pdf">보고서</a>'
    html = link + image if anchor_first else image + link
    files = extract_files(notice(html))
    assert len(files) == 2
    assert {file.kind for file in files} == {"attachment", "inline_image"}
    assert {file.file_id for file in files} == {"abc"}
    attachment = next(file for file in files if file.kind == "attachment")
    assert attachment.file_name == "report.pdf"
    assert attachment.url == "https://www.nowon.kr/file?q_fileSn=01&q_fileId=abc"


def test_anchor_text_is_file_name_when_download_attribute_missing() -> None:
    files = extract_files(
        notice('<a href="../file?q_fileSn=2&amp;q_fileId=xyz"><span>공지</span> 자료.pdf</a>')
    )
    assert files[0].file_name == "공지 자료.pdf"
    assert files[0].url == "https://www.nowon.kr/www/user/file?q_fileSn=2&q_fileId=xyz"


def test_same_file_sn_with_different_file_ids_is_allowed() -> None:
    html = (
        '<img src="/file?q_fileSn=7&amp;q_fileId=first">'
        '<a href="/file?q_fileSn=7&amp;q_fileId=second">PDF</a>'
    )
    files = extract_files(notice(html))
    assert {(file.file_sn, file.file_id, file.kind) for file in files} == {
        ("7", "first", "inline_image"),
        ("7", "second", "attachment"),
    }


def test_same_file_id_and_kind_with_different_urls_is_error() -> None:
    html = (
        '<img src="/file?q_fileSn=7&amp;q_fileId=same">'
        '<img src="/file?q_fileSn=8&amp;q_fileId=same">'
    )
    with pytest.raises(AttachmentError, match="file_key") as caught:
        extract_files(notice(html))
    assert "same" not in str(caught.value)


def test_decorative_images_and_unidentified_links_are_excluded() -> None:
    files = extract_files(
        notice(
            '<img src="/logo.png"><img src="data:image/png;base64,AAAA">'
            '<a href="https://other.example/info">안내</a>'
            '<a href="javascript:void(0)">버튼</a>'
        )
    )
    assert files == []


@pytest.mark.parametrize(
    "url",
    [
        "/file?q_fileSn=1",
        "/file?q_fileId=abc",
        "/file?q_fileSn=&q_fileId=abc",
        "/file?q_fileSn=1&q_fileId=",
        "/file?q_fileSn=1&q_fileSn=2&q_fileId=abc",
        "/file?q_fileSn=1&q_fileId=abc&q_fileId=other",
    ],
)
def test_partial_or_ambiguous_identifier_is_error(url: str) -> None:
    with pytest.raises(AttachmentError):
        extract_files(notice(f'<img src="{url.replace("&", "&amp;")}">'))


@pytest.mark.parametrize("page", [False, True])
@pytest.mark.parametrize(
    "query",
    [
        "q_fileSn=%201&q_fileId=abc",
        "q_fileSn=1%20&q_fileId=abc",
        "q_fileSn=1&q_fileId=%20abc",
        "q_fileSn=1&q_fileId=abc%20",
    ],
)
def test_padded_identifier_uses_attachment_error(query: str, page: bool) -> None:
    reference = f"/file?{query.replace('&', '&amp;')}"
    raw = notice(f'<img src="{reference}">')
    with pytest.raises(AttachmentError) as caught:
        if page:
            html = (
                '<tr><th>첨부파일</th><td><ul class="file-list">'
                f'<li><a href="{reference}">report.pdf</a></li></ul></td></tr>'
            )
            extract_page_files(raw, html, raw.url)
        else:
            extract_files(raw)
    assert caught.value.code == "invalid_file_identity"
    assert "abc" not in str(caught.value)


def test_invalid_url_is_safe_error() -> None:
    with pytest.raises(AttachmentError, match="URL") as caught:
        extract_files(notice('<img src="http://[invalid?q_fileSn=1&amp;q_fileId=abc">'))
    assert "invalid" not in str(caught.value)


def test_empty_body_and_wrong_source() -> None:
    assert extract_files(notice(None)) == []
    with pytest.raises(ValueError, match="노원구"):
        extract_files(notice(None, category="dong"))


def test_cli_reports_counts_without_exposing_file_metadata(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mock_collect_db: MagicMock,
) -> None:
    monkeypatch.setenv("NOWON_NOTICE_API_KEY", "sample")
    source = replace(
        notice(
            '<img src="/file?q_fileSn=1&amp;q_fileId=abc">'
            '<a href="/file?q_fileSn=2&amp;q_fileId=def">private.pdf</a>'
        ),
        url=("https://www.nowon.kr/www/user/bbs/BD_selectBbs.do?q_bbsCode=1001&q_bbscttSn=001234"),
    )
    monkeypatch.setattr("pipeline.cli.collect_one", lambda settings: source)
    monkeypatch.setattr(
        "pipeline.cli.fetch_notice_page",
        lambda notice, settings: (
            notice.url,
            "<tr><th>첨부파일</th><td>첨부파일이 없습니다.</td></tr>",
        ),
    )
    assert main(["collect-one", "--source", "nowon"]) == 0
    output = capsys.readouterr()
    summary = json.loads(output.out)
    assert summary["attachment_count"] == 1
    assert summary["inline_image_count"] == 1
    assert "private.pdf" not in output.out
    assert "q_fileId" not in output.out


def test_cli_identifier_conflict_fails_without_partial_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mock_collect_db: MagicMock,
) -> None:
    monkeypatch.setenv("NOWON_NOTICE_API_KEY", "sample")
    source = notice(
        '<img src="/file?q_fileSn=1&amp;q_fileId=abc"><img src="/file?q_fileSn=2&amp;q_fileId=abc">'
    )
    monkeypatch.setattr("pipeline.cli.collect_one", lambda settings: source)
    assert main(["collect-one", "--source", "nowon"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "file_key" in output.err
    assert "abc" not in output.err
