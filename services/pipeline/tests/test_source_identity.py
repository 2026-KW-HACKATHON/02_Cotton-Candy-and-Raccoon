"""#18 source-scoped identity and UUID-less file regression tests."""

import os
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from hashlib import sha256
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg
import pytest

from pipeline.attachments.dong_html import extract_dong_files
from pipeline.attachments.nowon_html import extract_page_files
from pipeline.models import FileRecord, NoticeRecord, RawNotice
from pipeline.storage.notice_bundle import save_notice_with_files


def _notice(board: str = "25") -> NoticeRecord:
    return NoticeRecord(
        category="seoul", source_board=board, dong_group=None, is_pinned=False,
        post_sn="00" + uuid4().hex, title="테스트", department=None,
        registered_on=date(2026, 10, 3), url="https://news.seoul.go.kr/env/archives/test",
        body_html="<p>본문</p>", license_type="KOGL-4",
    )


def _file(notice: NoticeRecord, *, kind: str = "inline_image") -> FileRecord:
    return FileRecord(
        category=notice.category, source_board=notice.source_board,
        post_sn=notice.post_sn, kind=kind, file_sn=None, file_id=None,
        file_name=None, url="https://news.seoul.go.kr/env/files/안내.png",
    )


@pytest.mark.parametrize("board", ["21", "22", "23", "24", "25", "26", "27", "30"])
def test_seoul_board_contract(board: str) -> None:
    notice = _notice(board)
    assert notice.source_board == _file(notice).source_board == board
    assert notice.post_sn.startswith("00")


@pytest.mark.parametrize("board", [None, 25, "", " 25", "99", "1001"])
def test_invalid_board_rejected(board: object) -> None:
    with pytest.raises((TypeError, ValueError), match="source_board"):
        replace(_notice(), source_board=board)


def test_source_board_is_required() -> None:
    with pytest.raises(TypeError, match="source_board"):
        NoticeRecord(category="nowon")


@pytest.mark.parametrize("field_name", ["file_id", "file_sn"])
@pytest.mark.parametrize("value", ["", " ", " id ", 123])
def test_optional_identifier_rejects_invalid_values(field_name: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError), match=field_name):
        replace(_file(_notice()), **{field_name: value})


def test_derived_key_uses_real_id_or_exact_utf8_url() -> None:
    file = _file(_notice())
    assert file.file_id is None and file.file_sn is None
    assert file.file_key == "url:" + sha256(file.url.encode("utf-8")).hexdigest()
    assert replace(file, file_id="real-uuid").file_key == "id:real-uuid"
    assert replace(file, url=file.url + "?v=2").file_key != file.file_key
    assert replace(file, kind="attachment").file_key == file.file_key


def test_same_post_different_board_cannot_share_files() -> None:
    notice = _notice()
    wrong_file = replace(_file(notice), source_board="30")
    conn = MagicMock()
    with pytest.raises(ValueError, match="일치"):
        save_notice_with_files(conn, notice, [wrong_file])
    conn.transaction.assert_not_called()


def test_editor_image_without_identifiers_normalizes_and_deduplicates() -> None:
    notice = RawNotice(
        category="dong", source_board="1042", dong_group="wolgye1", is_pinned=False,
        post_sn="00123", title="공지", department=None, registered_on="2026-10-03",
        url="https://www.nowon.kr/dong/user/bbs/BD_selectBbs.do?q_bbscttSn=00123",
        body_html='<img src="/webcontent/crosseditor/images/poster.jpg">'
        '<img src="http://www.nowon.kr:80/webcontent/crosseditor/images/poster.jpg#preview">'
        '<img src="/resources/decorative.png">', license_type=None,
    )
    page = '<tr><th>첨부파일</th><td>첨부파일이 없습니다.</td></tr>'
    files = extract_dong_files(notice, page)
    assert len(files) == 1
    assert files[0].source_board == "1042"
    assert files[0].file_id is None and files[0].file_sn is None
    assert files[0].url == "https://www.nowon.kr/webcontent/crosseditor/images/poster.jpg"
    assert files[0].file_key.startswith("url:")
    external = replace(notice, body_html='<img src="https://other.example/poster.jpg">')
    assert extract_dong_files(external, page) == []


def test_direct_attachment_without_uuid_keeps_metadata() -> None:
    notice = RawNotice(
        category="nowon", source_board="1001", dong_group=None, is_pinned=False,
        post_sn="00123", title="공지", department=None, registered_on="2026-10-03",
        url="https://www.nowon.kr/www/user/bbs/BD_selectBbs.do?q_bbscttSn=00123",
        body_html=None, license_type="KOGL-4",
    )
    page = '<tr><th>첨부파일</th><td><ul class="file-list"><li>' \
        '<a href="/files/report.pdf">안내.pdf</a></li></ul></td></tr>'
    files = extract_page_files(notice, page, notice.url)
    assert len(files) == 1
    assert files[0].file_id is None and files[0].file_sn is None
    assert files[0].file_name == "안내.pdf"
    assert files[0].kind == "attachment"


@pytest.fixture
def db() -> Iterator[psycopg.Connection]:
    dsn = os.environ.get("PIPELINE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("PIPELINE_TEST_DATABASE_URL is required")
    with psycopg.connect(dsn) as conn:
        conn.execute("select 1")
        try:
            yield conn
        finally:
            conn.rollback()


def test_db_board_scoped_upsert(db: psycopg.Connection) -> None:
    first = _notice()
    second = replace(first, source_board="30", title="다른 분야")
    first_id = save_notice_with_files(db, first, [])
    second_id = save_notice_with_files(db, second, [])
    assert first_id != second_id
    assert save_notice_with_files(db, first, []) == first_id
    assert db.execute(
        "select source_board,title,is_modified from notices where id=ANY(%s) "
        "order by source_board", ([first_id, second_id],),
    ).fetchall() == [("25", "테스트", False), ("30", "다른 분야", False)]


def test_db_idless_file_roles_repeat_and_changes(db: psycopg.Connection) -> None:
    notice = _notice()
    image = _file(notice)
    attachment = replace(image, kind="attachment", file_name="안내.png")
    notice_id = save_notice_with_files(db, notice, [image, attachment, image])
    sql = "select id,file_key,kind,file_sn,file_id from notice_files " \
          "where notice_id=%s order by kind"
    before = db.execute(sql, (notice_id,)).fetchall()
    assert len(before) == 2
    assert all(row[1] == image.file_key and row[3:] == (None, None) for row in before)
    assert save_notice_with_files(db, notice, [attachment, image]) == notice_id
    assert db.execute(sql, (notice_id,)).fetchall() == before
    assert db.execute("select is_modified from notices where id=%s", (notice_id,)).fetchone() \
        == (False,)
    assert save_notice_with_files(db, notice, [image]) == notice_id
    assert db.execute("select is_modified from notices where id=%s", (notice_id,)).fetchone() \
        == (True,)
    assert save_notice_with_files(db, notice, [image]) == notice_id
    assert db.execute("select is_modified from notices where id=%s", (notice_id,)).fetchone() \
        == (True,)


def test_real_check_failure_rolls_back_notice_and_files(db: psycopg.Connection) -> None:
    notice = _notice()
    image = _file(notice)
    notice_id = save_notice_with_files(db, notice, [image])
    before = db.execute(
        "select id,file_key from notice_files where notice_id=%s", (notice_id,),
    ).fetchall()

    def invalid_insert(conn: psycopg.Connection, key: int, file: FileRecord) -> None:
        conn.execute(
            "insert into notice_files(notice_id,kind,url,file_key) "
            "values (%s,%s,%s,'wrong-key')", (key, file.kind, file.url),
        )

    with patch("pipeline.storage.notice_bundle._insert_file", side_effect=invalid_insert):
        with pytest.raises(psycopg.errors.CheckViolation):
            save_notice_with_files(
                db, replace(notice, title="수정 제목"), [replace(image, url=image.url + "?v=2")],
            )
    assert db.execute("select title,is_modified from notices where id=%s", (notice_id,)) \
        .fetchone() == (notice.title, False)
    assert db.execute(
        "select id,file_key from notice_files where notice_id=%s", (notice_id,),
    ).fetchall() == before
