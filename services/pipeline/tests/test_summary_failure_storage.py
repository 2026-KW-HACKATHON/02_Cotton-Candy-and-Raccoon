"""Run failure UPSERT regressions against a session-local PostgreSQL table."""

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
from pipeline.transform.prepared_summary import PreparedSummaryResult
from pipeline.transform.summary_schema import MediaSource, NoticeSummary


@pytest.fixture
def summary_db():
    database_url = os.getenv("PIPELINE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PIPELINE_TEST_DATABASE_URL is required for summary DB integration")
    # A temporary table shadows any real table only in this connection. No
    # migration or committed application data is required or changed.
    conn = psycopg.connect(database_url)
    try:
        conn.execute(
            """
            create temporary table notice_summaries (
                notice_id bigint primary key,
                status text not null,
                result jsonb,
                category text,
                deadline_on date,
                attachment_status text not null,
                source_hash text not null,
                model text not null,
                prompt_version text not null,
                attempt_count integer not null default 0,
                last_error_code text,
                generated_at timestamptz,
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now()
            ) on commit drop
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


def _failure(conn: psycopg.Connection, *, attempt_increment: int = 1) -> None:
    assert record_summary_failure(
        conn,
        42,
        _metadata(
            source_hash="cd" * 32,
            model="different-model",
            prompt_version="different-prompt",
            attachment_status="unread",
        ),
        reason_code="api_timeout",
        attempt_increment=attempt_increment,
    ) == 42


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
    save_notice_summary(summary_db, _record(existing_status))
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
    _failure(summary_db)
    row = _stored(summary_db)
    assert row["status"] == "failed"
    assert row["result"] is None
    assert row["category"] is None
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
def test_file_only_prepared_summary_stores_review_with_no_public_content(
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
    assert row["result"] is None
    assert row["category"] is None
    assert row["generated_at"] == GENERATED_AT
    assert row["attempt_count"] == (2 if replace_success else 1)
    # Unverified content survives only in the pipeline caller's in-memory data.
    assert stored.result.summary.model_dump(mode="json") == result.summary.model_dump(mode="json")
