"""Atomic notice/file storage; real DB cases need PIPELINE_TEST_DATABASE_URL."""

import os
from dataclasses import replace
from datetime import date
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg
import pytest

from pipeline.models import FileRecord, NoticeRecord
from pipeline.storage.notice_bundle import save_notice_with_files


@pytest.fixture
def notice() -> NoticeRecord:
    post_sn = "00" + uuid4().hex
    return NoticeRecord(
        category="nowon",
        dong_group=None,
        is_pinned=False,
        post_sn=post_sn,
        title="원래 제목",
        department="원래 부서",
        registered_on=date(2026, 9, 25),
        url=f"https://www.nowon.kr/notice?q_bbscttSn={post_sn}",
        body_html="<p>원래 본문</p>",
        license_type="KOGL-4",
    )


@pytest.fixture
def first_file(notice: NoticeRecord) -> FileRecord:
    return FileRecord(
        category=notice.category,
        post_sn=notice.post_sn,
        kind="attachment",
        file_sn="10",
        file_id="file-a",
        file_name="안내.pdf",
        url="https://www.nowon.kr/file?q_fileSn=10&q_fileId=file-a",
    )


@pytest.fixture
def second_file(notice: NoticeRecord) -> FileRecord:
    return FileRecord(
        category=notice.category,
        post_sn=notice.post_sn,
        kind="inline_image",
        file_sn="10",
        file_id="file-b",
        file_name=None,
        url="https://www.nowon.kr/file?q_fileSn=10&q_fileId=file-b",
    )


@pytest.fixture
def db_conn():
    database_url = os.getenv("PIPELINE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PIPELINE_TEST_DATABASE_URL 미설정: PostgreSQL 통합 테스트 미실행")
    conn = psycopg.connect(database_url)
    # Keep an outer transaction open: save_notice_with_files uses a savepoint,
    # and fixture teardown can roll back every row created by this test.
    conn.execute("select 1")
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_rejects_wrong_parent_before_writing(
    notice: NoticeRecord,
    first_file: FileRecord,
) -> None:
    conn = MagicMock()
    with pytest.raises(ValueError, match="게시물 번호"):
        save_notice_with_files(conn, notice, [replace(first_file, post_sn="other")])
    conn.transaction.assert_not_called()


def test_rejects_conflicting_duplicate_before_writing(
    notice: NoticeRecord,
    first_file: FileRecord,
) -> None:
    conn = MagicMock()
    with pytest.raises(ValueError, match="충돌"):
        save_notice_with_files(
            conn,
            notice,
            [
                first_file,
                replace(first_file, file_sn="20"),
            ],
        )
    conn.transaction.assert_not_called()


def test_rejects_missing_file_list(notice: NoticeRecord) -> None:
    conn = MagicMock()
    with pytest.raises(ValueError, match="완료"):
        save_notice_with_files(conn, notice, None)
    conn.transaction.assert_not_called()


def test_new_notice_first_files_do_not_mark_modified(
    notice: NoticeRecord,
    first_file: FileRecord,
) -> None:
    conn = MagicMock()
    with (
        patch("pipeline.storage.notice_bundle.insert_notice_if_absent", return_value=42),
        patch("pipeline.storage.notice_bundle._stored_files", return_value={}),
        patch("pipeline.storage.notice_bundle._insert_file") as insert_file,
    ):
        assert save_notice_with_files(conn, notice, [first_file, first_file]) == 42
    insert_file.assert_called_once_with(conn, 42, first_file)
    cursor = conn.cursor.return_value.__enter__.return_value
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert any(sql.startswith("delete from notice_files") for sql in statements)
    assert not any(sql.startswith("update notices set is_modified") for sql in statements)
    conn.commit.assert_not_called()


def test_same_files_in_different_order_are_not_replaced(
    notice: NoticeRecord,
    first_file: FileRecord,
    second_file: FileRecord,
) -> None:
    conn = MagicMock()
    stored = {
        (first_file.file_key, first_file.kind): (
            first_file.file_sn,
            first_file.file_name,
            first_file.url,
        ),
        (second_file.file_key, second_file.kind): (
            second_file.file_sn,
            second_file.file_name,
            second_file.url,
        ),
    }
    with (
        patch("pipeline.storage.notice_bundle.insert_notice_if_absent", return_value=None),
        patch("pipeline.storage.notice_bundle.save_notice", return_value=42),
        patch("pipeline.storage.notice_bundle._stored_files", return_value=stored),
        patch("pipeline.storage.notice_bundle._insert_file") as insert_file,
    ):
        assert save_notice_with_files(conn, notice, [second_file, first_file]) == 42
    insert_file.assert_not_called()
    conn.cursor.assert_not_called()


def test_existing_file_change_replaces_rows_and_marks_modified(
    notice: NoticeRecord,
    first_file: FileRecord,
) -> None:
    conn = MagicMock()
    stored = {
        (first_file.file_key, first_file.kind): (
            first_file.file_sn,
            "이전 이름.pdf",
            first_file.url,
        )
    }
    with (
        patch("pipeline.storage.notice_bundle.insert_notice_if_absent", return_value=None),
        patch("pipeline.storage.notice_bundle.save_notice", return_value=42),
        patch("pipeline.storage.notice_bundle._stored_files", return_value=stored),
        patch("pipeline.storage.notice_bundle._insert_file") as insert_file,
    ):
        assert save_notice_with_files(conn, notice, [first_file]) == 42
    insert_file.assert_called_once_with(conn, 42, first_file)
    cursor = conn.cursor.return_value.__enter__.return_value
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert any(sql.startswith("delete from notice_files") for sql in statements)
    assert any(sql.startswith("update notices set is_modified") for sql in statements)


def _file_rows(conn: psycopg.Connection, notice_id: int) -> list[tuple]:
    with conn.cursor() as cursor:
        cursor.execute(
            "select id, kind, file_sn, file_id, file_name, url "
            "from notice_files where notice_id = %s order by file_id, kind",
            (notice_id,),
        )
        return cursor.fetchall()


def test_new_repeat_and_order_only_preserve_rows(
    db_conn,
    notice: NoticeRecord,
    first_file: FileRecord,
    second_file: FileRecord,
) -> None:
    notice_id = save_notice_with_files(db_conn, notice, [first_file, second_file])
    before = _file_rows(db_conn, notice_id)
    assert [(row[1], row[3]) for row in before] == [
        ("attachment", "file-a"),
        ("inline_image", "file-b"),
    ]
    with db_conn.cursor() as cursor:
        cursor.execute("select post_sn, is_modified from notices where id = %s", (notice_id,))
        assert cursor.fetchone() == (notice.post_sn, False)

    assert save_notice_with_files(db_conn, notice, [second_file, first_file]) == notice_id
    assert _file_rows(db_conn, notice_id) == before
    with db_conn.cursor() as cursor:
        cursor.execute("select is_modified from notices where id = %s", (notice_id,))
        assert cursor.fetchone() == (False,)


@pytest.mark.parametrize("change", ["add", "delete", "replace", "metadata", "empty"])
def test_existing_file_changes_mark_modified(
    db_conn,
    notice: NoticeRecord,
    first_file: FileRecord,
    second_file: FileRecord,
    change: str,
) -> None:
    initial = [first_file, second_file]
    notice_id = save_notice_with_files(db_conn, notice, initial)
    replacement = replace(
        first_file,
        file_id="file-c",
        file_sn="10",
        url="https://www.nowon.kr/file?q_fileSn=10&q_fileId=file-c",
    )
    changed = {
        "add": [first_file, second_file, replacement],
        "delete": [first_file],
        "replace": [replacement, second_file],
        "metadata": [replace(first_file, file_name="새 이름.pdf"), second_file],
        "empty": [],
    }[change]
    assert save_notice_with_files(db_conn, notice, changed) == notice_id
    assert {(row[3], row[1], row[2], row[4], row[5]) for row in _file_rows(db_conn, notice_id)} == {
        (file.file_id, file.kind, file.file_sn, file.file_name, file.url) for file in changed
    }
    with db_conn.cursor() as cursor:
        cursor.execute("select is_modified from notices where id = %s", (notice_id,))
        assert cursor.fetchone() == (True,)


def test_existing_empty_list_then_file_addition_marks_modified(
    db_conn,
    notice: NoticeRecord,
    first_file: FileRecord,
) -> None:
    notice_id = save_notice_with_files(db_conn, notice, [])
    assert save_notice_with_files(db_conn, notice, [first_file]) == notice_id
    with db_conn.cursor() as cursor:
        cursor.execute("select is_modified from notices where id = %s", (notice_id,))
        assert cursor.fetchone() == (True,)


def test_file_insert_failure_rolls_back_notice_and_file_changes(
    db_conn,
    notice: NoticeRecord,
    first_file: FileRecord,
    second_file: FileRecord,
) -> None:
    notice_id = save_notice_with_files(db_conn, notice, [first_file])
    before = _file_rows(db_conn, notice_id)
    with patch(
        "pipeline.storage.notice_bundle._insert_file",
        side_effect=psycopg.IntegrityError("injected file failure"),
    ):
        with pytest.raises(psycopg.IntegrityError, match="injected file failure"):
            save_notice_with_files(
                db_conn,
                replace(notice, title="변경된 제목"),
                [second_file],
            )
    assert _file_rows(db_conn, notice_id) == before
    with db_conn.cursor() as cursor:
        cursor.execute("select title, is_modified from notices where id = %s", (notice_id,))
        assert cursor.fetchone() == ("원래 제목", False)


def test_new_notice_is_rolled_back_when_file_insert_fails(
    db_conn,
    notice: NoticeRecord,
    first_file: FileRecord,
) -> None:
    with patch(
        "pipeline.storage.notice_bundle._insert_file",
        side_effect=psycopg.IntegrityError("injected file failure"),
    ):
        with pytest.raises(psycopg.IntegrityError, match="injected file failure"):
            save_notice_with_files(db_conn, notice, [first_file])
    with db_conn.cursor() as cursor:
        cursor.execute(
            "select id from notices where category = %s and post_sn = %s",
            (notice.category, notice.post_sn),
        )
        assert cursor.fetchone() is None


def test_files_without_ids_use_distinct_url_keys_and_preserve_nulls(
    db_conn,
    notice,
    first_file,
) -> None:
    first = replace(first_file, file_sn=None, file_id=None, url="https://www.nowon.kr/안내.pdf")
    second = replace(first, url="https://www.nowon.kr/다른안내.pdf")
    image = replace(first, kind="inline_image", file_name=None)
    notice_id = save_notice_with_files(db_conn, notice, [first, second, image])
    rows = db_conn.execute(
        "select file_key, kind, file_sn, file_id, url from notice_files "
        "where notice_id = %s order by file_key, kind",
        (notice_id,),
    ).fetchall()
    assert set(rows) == {
        (file.file_key, file.kind, None, None, file.url) for file in (first, second, image)
    }
    before = _file_rows(db_conn, notice_id)
    assert save_notice_with_files(db_conn, notice, [image, second, first, first]) == notice_id
    assert _file_rows(db_conn, notice_id) == before
    assert db_conn.execute(
        "select is_modified from notices where id = %s",
        (notice_id,),
    ).fetchone() == (False,)


def test_real_file_id_and_missing_group_are_stored_without_synthetic_id(
    db_conn,
    notice,
    first_file,
) -> None:
    file = replace(first_file, file_sn=None)
    notice_id = save_notice_with_files(db_conn, notice, [file])
    assert db_conn.execute(
        "select file_sn, file_id, file_key from notice_files where notice_id = %s",
        (notice_id,),
    ).fetchone() == (None, first_file.file_id, "id:" + first_file.file_id)
