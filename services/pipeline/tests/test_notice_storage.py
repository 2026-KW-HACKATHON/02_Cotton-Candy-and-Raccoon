"""Notice storage tests; integration cases require PIPELINE_TEST_DATABASE_URL."""

import os
from dataclasses import replace
from datetime import UTC, date, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import psycopg
import pytest

from pipeline.models import NoticeRecord
from pipeline.storage.notices import save_notice


@pytest.fixture
def record() -> NoticeRecord:
    post_sn = "00" + uuid4().hex
    return NoticeRecord(
        source_board="1001",
        category="nowon", dong_group=None, is_pinned=False, post_sn=post_sn,
        title="안내", department="교육지원과", registered_on=date(2026, 9, 25),
        url=f"https://www.nowon.kr/notice?q_bbscttSn={post_sn}",
        body_html="<p>원문</p>",
        license_type="KOGL-4",
    )


@pytest.fixture
def db_conn():
    database_url = os.getenv("PIPELINE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PIPELINE_TEST_DATABASE_URL 미설정: PostgreSQL 통합 테스트 미실행")
    conn = psycopg.connect(database_url)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_save_notice_binds_all_values_and_returns_id(record: NoticeRecord) -> None:
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (42,)

    assert save_notice(conn, record) == 42

    sql, values = cursor.execute.call_args.args
    assert "on conflict (category, source_board, post_sn) do update" in sql
    assert "returning id" in sql
    assert values[1] == record.source_board
    assert values[4] == record.post_sn
    assert values[7] == date(2026, 9, 25)
    assert conn.commit.call_count == 0
    assert conn.rollback.call_count == 0
    assert conn.close.call_count == 0


def test_save_notice_propagates_db_errors_without_committing(record: NoticeRecord) -> None:
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.execute.side_effect = psycopg.IntegrityError("test failure")
    with pytest.raises(psycopg.IntegrityError, match="test failure"):
        save_notice(conn, record)
    conn.commit.assert_not_called()
    conn.rollback.assert_not_called()


def test_save_notice_rejects_missing_returned_id(record: NoticeRecord) -> None:
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    with pytest.raises(RuntimeError, match="ID"):
        save_notice(conn, record)


def test_insert_repeat_department_visibility_and_timestamps(db_conn, record: NoticeRecord) -> None:
    notice_id = save_notice(db_conn, record)
    with db_conn.cursor() as cursor:
        cursor.execute(
            "select category, post_sn, title, body_html, is_modified, is_visible, "
            "created_at, updated_at from notices where id = %s",
            (notice_id,),
        )
        initial = cursor.fetchone()
        assert initial[:6] == ("nowon", record.post_sn, "안내", "<p>원문</p>", False, True)
        cursor.execute(
            "update notices set is_visible = false, updated_at = '2000-01-01' where id = %s",
            (notice_id,),
        )

    assert save_notice(db_conn, record) == notice_id
    with db_conn.cursor() as cursor:
        cursor.execute(
            "select count(*), is_modified, is_visible, created_at, updated_at "
            "from notices where id = %s group by is_modified, is_visible, "
            "created_at, updated_at",
            (notice_id,),
        )
        count, modified, visible, created, updated = cursor.fetchone()
    assert (count, modified, visible) == (1, False, True)
    assert created == initial[6]
    assert updated > datetime(2000, 1, 1, tzinfo=UTC)

    assert save_notice(db_conn, replace(record, department="새 부서")) == notice_id
    with db_conn.cursor() as cursor:
        cursor.execute("select department, is_modified from notices where id = %s", (notice_id,))
        assert cursor.fetchone() == ("새 부서", False)


@pytest.mark.parametrize(
    "changes",
    [
        {"title": "수정된 제목"},
        {"body_html": None},
        {"registered_on": date(2026, 9, 26)},
        {"url": "https://www.nowon.kr/changed"},
        {"license_type": "KOGL-3"},
    ],
)
def test_five_fields_mark_modified_and_true_stays_true(
    db_conn,
    record: NoticeRecord,
    changes: dict[str, object],
) -> None:
    notice_id = save_notice(db_conn, record)
    assert save_notice(db_conn, replace(record, **changes)) == notice_id
    with db_conn.cursor() as cursor:
        cursor.execute("select is_modified from notices where id = %s", (notice_id,))
        assert cursor.fetchone() == (True,)
    save_notice(db_conn, record)
    with db_conn.cursor() as cursor:
        cursor.execute("select is_modified from notices where id = %s", (notice_id,))
        assert cursor.fetchone() == (True,)


def test_caller_rollback_removes_uncommitted_notice(db_conn, record: NoticeRecord) -> None:
    notice_id = save_notice(db_conn, record)
    with db_conn.cursor() as cursor:
        cursor.execute("select id from notices where id = %s", (notice_id,))
        assert cursor.fetchone() == (notice_id,)
    db_conn.rollback()
    with db_conn.cursor() as cursor:
        cursor.execute(
            "select id from notices where category = %s and post_sn = %s",
            (record.category, record.post_sn),
        )
        assert cursor.fetchone() is None


def test_nowon_and_dong_with_same_post_id_have_separate_board_keys(db_conn, record) -> None:
    nowon_id = save_notice(db_conn, record)
    dong = replace(
        record, category="dong", source_board="1042", dong_group="wolgye1", license_type=None,
    )
    dong_id = save_notice(db_conn, dong)
    assert nowon_id != dong_id
    assert save_notice(db_conn, record) == nowon_id
    assert save_notice(db_conn, dong) == dong_id
    rows = db_conn.execute(
        "select category, source_board, post_sn from notices where post_sn = %s order by category",
        (record.post_sn,),
    ).fetchall()
    assert rows == [("dong", "1042", record.post_sn), ("nowon", "1001", record.post_sn)]
