from dataclasses import replace

import pytest

from pipeline.attachments.dong_html import extract_dong_files
from pipeline.attachments.nowon_html import AttachmentError
from pipeline.models import RawNotice


@pytest.fixture
def notice() -> RawNotice:
    return RawNotice(
        source_board="1042",
        category="dong", dong_group="wolgye1", is_pinned=False,
        post_sn="001234", title="공지", department="월계1동 행정민원팀",
        registered_on="2026-09-28",
        url=("https://www.nowon.kr/dong/user/bbs/BD_selectBbs.do"
             "?q_bbsCode=1042&q_bbscttSn=001234&q_deptCode=1047"),
        body_html=(
            '<img src="/component/file/ND_fileDownload.do?'
            'q_fileSn=1&amp;q_fileId=image-a">'
        ),
        license_type="KOGL-1",
    )


def _page(file_links: str) -> str:
    return (
        '<table class="table-article"><tr><th>첨부파일</th><td>'
        f'{file_links}</td></tr></table>'
    )


def _link(file_sn: str, file_id: str, name: str = "안내.pdf") -> str:
    return (
        '<a href="/component/file/ND_fileDownload.do?'
        f'q_fileSn={file_sn}&amp;q_fileId={file_id}">{name}</a>'
    )


def test_body_image_and_separate_pdf_are_distinct_files(notice: RawNotice) -> None:
    files = extract_dong_files(notice, _page(
        '<ul class="file-list"><li>' + _link("2", "file-b") + '</li></ul>',
    ))
    assert {(file.kind, file.file_sn, file.file_id) for file in files} == {
        ("inline_image", "1", "image-a"), ("attachment", "2", "file-b"),
    }
    assert all(file.url.startswith("https://www.nowon.kr/") for file in files)
    assert next(file for file in files if file.kind == "attachment").file_name == "안내.pdf"


def test_duplicate_body_image_is_stored_once(notice: RawNotice) -> None:
    source = replace(notice, body_html=notice.body_html * 2)
    files = extract_dong_files(source, _page("첨부파일이 없습니다."))
    assert [(file.file_id, file.kind) for file in files] == [("image-a", "inline_image")]


def test_same_file_can_be_attachment_and_image(notice: RawNotice) -> None:
    files = extract_dong_files(notice, _page(
        '<ul class="file-list"><li>' + _link("1", "image-a", "그림.jpg") + '</li></ul>',
    ))
    assert {(file.file_id, file.kind) for file in files} == {
        ("image-a", "inline_image"), ("image-a", "attachment"),
    }


def test_same_file_sn_with_different_file_ids_is_allowed(notice: RawNotice) -> None:
    files = extract_dong_files(notice, _page(
        '<ul class="file-list"><li>' + _link("2", "first") + '</li><li>'
        + _link("2", "second") + '</li></ul>',
    ))
    assert {file.file_id for file in files} == {"image-a", "first", "second"}


def test_conflicting_same_file_id_is_rejected(notice: RawNotice) -> None:
    source = replace(notice, body_html=(
        notice.body_html
        + '<img src="/component/file/ND_fileDownload.do?q_fileSn=9&amp;q_fileId=image-a">'
    ))
    with pytest.raises(AttachmentError, match="서로 다른 URL"):
        extract_dong_files(source, _page("첨부파일이 없습니다."))


@pytest.mark.parametrize("page_html", [
    "<html></html>",
    _page(""),
    _page("<ul class='file-list'><li>파일 표시만 있음</li></ul>"),
])
def test_missing_attachment_area_or_unknown_list_is_rejected(
    notice: RawNotice, page_html: str,
) -> None:
    with pytest.raises(AttachmentError):
        extract_dong_files(notice, page_html)


def test_decorative_image_without_identity_is_not_a_file(notice: RawNotice) -> None:
    source = replace(notice, body_html='<img src="/resources/decorative.png">')
    assert extract_dong_files(source, _page("첨부파일이 없습니다.")) == []
