"""Run storage regressions against the actual migrated PostgreSQL table."""

import os
from datetime import UTC, date, datetime

import psycopg
import pytest
from psycopg.rows import dict_row
from test_summary_storage import GENERATED_AT, _metadata, _record, _summary

from pipeline.storage.summaries import (
    record_summary_failure,
    save_notice_summary,
    save_prepared_summary,
)
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.transform.prepared_summary import PreparedSummaryResult
from pipeline.transform.summary_cards import build_summary_cards
from pipeline.transform.summary_schema import MediaSource, NoticeSummary


@pytest.fixture
def summary_db():
    database_url = os.getenv("PIPELINE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PIPELINE_TEST_DATABASE_URL is required for summary DB integration")
    conn = psycopg.connect(database_url)
    if conn.info.host != "127.0.0.1" or not conn.info.dbname.startswith("pipeline_schema_test_"):
        conn.close()
        pytest.fail("Use a disposable loopback pipeline_schema_test_* database")
    try:
        conn.execute(
            """
            insert into notices (id, category, source_board, post_sn, title, registered_on, url)
            overriding system value
            values (42, 'nowon', '1001', 'summary-storage-regression', '요약 저장 검증',
                    '2026-10-03', 'https://www.nowon.kr/test/summary-storage-regression')
            """
        )
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _stored(conn: psycopg.Connection) -> dict:
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute("select * from notice_summaries where notice_id = 42")
        row = cursor.fetchone()
    assert row is not None
    return row


def _failure(
    conn: psycopg.Connection, *, attempt_increment: int = 1, source_hash: str = "ab" * 32
) -> None:
    assert (
        record_summary_failure(
            conn,
            42,
            _metadata(
                source_hash=source_hash,
                model="different-model",
                prompt_version="different-prompt",
                attachment_status="unread",
            ),
            reason_code="api_timeout",
            attempt_increment=attempt_increment,
        )
        == 42
    )


@pytest.mark.parametrize("attempt_increment", [0, 1])
def test_success_then_timeout_preserves_result_status_deadline_and_all_summary_columns(
    summary_db: psycopg.Connection, attempt_increment: int
) -> None:
    save_notice_summary(summary_db, _record())
    summary_db.execute(
        "update notice_summaries set updated_at = %s where notice_id = 42",
        (datetime(2001, 1, 1, tzinfo=UTC),),
    )
    before = _stored(summary_db)
    _failure(summary_db, attempt_increment=attempt_increment)
    after = _stored(summary_db)
    assert after["status"] == "summarized"
    assert after["result"] == before["result"]
    assert after["deadline_on"] == date(2026, 10, 20)
    assert after["last_error_code"] == "api_timeout"
    assert after["attempt_count"] == before["attempt_count"] + attempt_increment
    assert after["updated_at"] > before["updated_at"]
    changed_columns = {"last_error_code", "attempt_count", "updated_at"}
    assert {key: value for key, value in after.items() if key not in changed_columns} == {
        key: value for key, value in before.items() if key not in changed_columns
    }


@pytest.mark.parametrize("existing_status", ["pending", "needs_review", "failed"])
def test_failure_preserves_every_existing_row_state(
    summary_db: psycopg.Connection, existing_status: str
) -> None:
    changes = {"result": _summary(uncertainties=["원문 확인 필요"])} if (
        existing_status == "needs_review"
    ) else {}
    save_notice_summary(summary_db, _record(existing_status, **changes))
    before = _stored(summary_db)
    _failure(summary_db)
    after = _stored(summary_db)
    changed_columns = {"last_error_code", "attempt_count", "updated_at"}
    assert {key: value for key, value in after.items() if key not in changed_columns} == {
        key: value for key, value in before.items() if key not in changed_columns
    }
    assert after["last_error_code"] == "api_timeout"
    assert after["attempt_count"] == before["attempt_count"] + 1


def test_first_failure_inserts_failed_row_without_summary_data(
    summary_db: psycopg.Connection,
) -> None:
    _failure(summary_db, source_hash="cd" * 32)
    row = _stored(summary_db)
    assert row["status"] == "failed"
    assert row["result"] is None
    assert row["category"] is None
    assert row["category_code"] is None
    assert row["deadline_on"] is None
    assert row["generated_at"] is None
    assert row["attempt_count"] == 1
    assert row["last_error_code"] == "api_timeout"
    assert row["source_hash"] == "cd" * 32
    assert row["model"] == "different-model"
    assert row["prompt_version"] == "different-prompt"
    assert row["attachment_status"] == "unread"


def test_success_after_failure_replaces_summary_columns_and_clears_failure_code(
    summary_db: psycopg.Connection,
) -> None:
    _failure(summary_db)
    save_notice_summary(summary_db, _record())
    row = _stored(summary_db)
    assert row["status"] == "summarized"
    assert row["result"] == _record().result.model_dump(mode="json")
    assert row["deadline_on"] == date(2026, 10, 20)
    assert row["generated_at"] == GENERATED_AT
    assert row["source_hash"] == "ab" * 32
    assert row["last_error_code"] is None
    assert row["attempt_count"] == 2


@pytest.mark.parametrize(
    ("source_type", "replace_success"),
    [("document", False), ("image", False), ("document", True)],
    ids=["first-pdf", "first-image", "success-then-pdf-review"],
)
def test_file_only_prepared_summary_preserves_content_through_database_and_public_cards(
    summary_db: psycopg.Connection, source_type: str, replace_success: bool
) -> None:
    if replace_success:
        save_notice_summary(summary_db, _record())
        assert _stored(summary_db)["result"] is not None
    data = _summary().model_dump(mode="json")
    for evidence in data["evidence"]:
        evidence.update(
            source_type=source_type,
            source_id="media_1",
            page=1 if source_type == "document" else None,
            verification="file_reference_only",
        )
    result = PreparedSummaryResult(
        notice_id=42,
        summary=NoticeSummary.model_validate(data),
        warnings=(),
        media_sources=(MediaSource("media_1", source_type),),
    )
    stored = save_prepared_summary(
        summary_db,
        result,
        _metadata(),
        deadline_on=date(2026, 10, 20),
        generated_at=GENERATED_AT,
    )
    row = _stored(summary_db)
    assert stored.status == row["status"] == "needs_review"
    assert stored.deadline_on is row["deadline_on"] is None
    assert row["result"] == result.summary.model_dump(mode="json")
    assert row["category"] == "application"
    assert row["category_code"] == 27
    assert row["generated_at"] == GENERATED_AT
    assert row["attempt_count"] == (2 if replace_success else 1)
    # The persisted result supplies the app's cards, with a separate display warning.
    assert stored.result.summary.model_dump(mode="json") == result.summary.model_dump(mode="json")
    view = build_notice_summary_view(
        status=row["status"], result=row["result"], attachment_status=row["attachment_status"]
    )
    expected = build_summary_cards(result.summary)
    assert view.message == "원문 확인 요함"
    assert view.content.headline.text == f"{expected.headline.text} (원문 확인 요함)"
    assert view.content.headline.value == expected.headline.value
    assert view.content.cards == expected.cards
    assert view.content.metadata == expected.metadata


def test_changed_source_failure_withholds_stale_summary_and_retries_to_new_source(
    summary_db: psycopg.Connection,
) -> None:
    save_notice_summary(summary_db, _record())
    before = _stored(summary_db)
    _failure(summary_db, source_hash="cd" * 32)
    after = _stored(summary_db)
    assert after["status"] == "needs_review"
    assert all(after[key] is None for key in ("result", "category", "category_code", "deadline_on"))
    for key in ("source_hash", "model", "prompt_version", "attachment_status", "generated_at"):
        assert after[key] == before[key]
    assert after["last_error_code"] == "api_timeout"
    assert after["attempt_count"] == 2
    assert summary_db.execute(
        "select notice_id from notice_summaries "
        "where notice_id=42 and (status in ('pending','failed') or last_error_code is not null)"
    ).fetchone() == (42,)
    # Repeated failures keep the same safe public state and original successful hash.
    _failure(summary_db, source_hash="cd" * 32)
    assert _stored(summary_db)["source_hash"] == "ab" * 32
    assert _stored(summary_db)["result"] is None
    save_notice_summary(summary_db, _record(metadata=_metadata(source_hash="cd" * 32)))
    recovered = _stored(summary_db)
    assert recovered["status"] == "summarized"
    assert recovered["category_code"] == 27
    assert recovered["source_hash"] == "cd" * 32
    assert recovered["last_error_code"] is None
    assert recovered["attempt_count"] == 4


@pytest.mark.parametrize("source_changed", [False, True])
def test_pending_execution_does_not_erase_previous_summary_or_successful_hash(
    summary_db: psycopg.Connection, source_changed: bool
) -> None:
    save_notice_summary(summary_db, _record())
    before = _stored(summary_db)
    current_hash = "cd" * 32 if source_changed else "ab" * 32
    save_notice_summary(
        summary_db, _record("pending", metadata=_metadata(source_hash=current_hash))
    )
    pending = _stored(summary_db)
    assert pending["status"] == "summarized"
    assert pending["result"] == before["result"]
    assert pending["source_hash"] == "ab" * 32
    _failure(summary_db, source_hash=current_hash, attempt_increment=0)
    finished = _stored(summary_db)
    assert finished["attempt_count"] == 2
    assert finished["status"] == ("needs_review" if source_changed else "summarized")
    assert finished["result"] == (None if source_changed else before["result"])
