"""Guard hostile completion orders in a real disposable PostgreSQL database."""

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from threading import Event
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from pipeline.storage.summaries import (
    SummaryExecutionSuperseded,
    begin_summary_execution,
    record_summary_failure,
    save_notice_summary,
)
from pipeline.storage.summary_metadata import compute_source_hash
from pipeline.storage.summary_record import SummaryMetadata, SummaryRecord
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.summary_job import StoredSummarySuperseded, summarize_and_save_prepared_notice
from pipeline.transform.gemini_client import GeminiRequestError
from pipeline.transform.gemini_prompt import SUMMARY_PROMPT_VERSION
from pipeline.transform.prepared_summary import PreparedSummaryResult
from pipeline.transform.summary_schema import CardSummaries, DateEntry, Evidence, NoticeSummary

OLD_BODY = "지원 사업 신청. 신청 마감: 2026-10-20."
NEW_BODY = "지원 사업 신청. 신청 마감: 2026-11-30."


@pytest.fixture
def audit_db() -> Iterator[psycopg.Connection]:
    dsn = os.environ.get("AUDIT_TEST_DATABASE_URL") or os.environ.get("PIPELINE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("A disposable AUDIT_TEST_DATABASE_URL is required")
    conn = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5)
    if conn.info.host != "127.0.0.1" or not conn.info.dbname.startswith("pipeline_schema_test_"):
        conn.close()
        pytest.fail("Use a disposable loopback pipeline_schema_test_* database")
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _notice(conn: psycopg.Connection) -> int:
    return conn.execute(
        "insert into notices(category,source_board,post_sn,title,registered_on,url,body_html) "
        "values ('nowon','1001',%s,'지원 사업 신청',current_date,"
        "'https://www.nowon.kr/audit',%s) returning id",
        ("execution-audit-" + uuid4().hex, OLD_BODY),
    ).fetchone()["id"]


def _metadata(body: str, *, generation: str = "v4") -> SummaryMetadata:
    return SummaryMetadata(
        source_hash=compute_source_hash(body),
        model="gemini-audit-" + generation,
        prompt_version="notice-summary-" + generation,
        attachment_status="none",
    )


def _summary(deadline: str) -> NoticeSummary:
    return NoticeSummary(
        category="application",
        category_code=27,
        summary="지원 사업 신청",
        publisher=None,
        applicable_area=None,
        audience=None,
        audience_scope="unknown",
        action=None,
        action_requirement="unknown",
        location=None,
        dates=[
            DateEntry(
                kind="application", label=None, text=None, start_date=None,
                end_date=deadline, start_time=None, end_time=None,
            )
        ],
        status="open",
        status_detail=None,
        notice_update="new",
        changed_details=None,
        notes=[],
        topics=[],
        uncertainties=[],
        evidence=[
            Evidence(field="summary", excerpt="지원 사업 신청", verification="text_matched"),
            Evidence(field="category_code", excerpt="지원 사업 신청", verification="text_matched"),
            Evidence(
                field="dates", excerpt="신청 마감: " + deadline, verification="text_matched"
            ),
        ],
        card_summaries=CardSummaries(
            audience=None, deadline=deadline + "까지 신청", action=None, notes=None
        ),
    )


def _completed(
    notice_id: int,
    body: str,
    deadline: str,
    *,
    generation: str = "v4",
    execution_token: int | None = None,
) -> SummaryRecord:
    return SummaryRecord(
        notice_id=notice_id,
        status="summarized",
        metadata=_metadata(body, generation=generation),
        result=_summary(deadline),
        deadline_on=date.fromisoformat(deadline),
        generated_at=datetime.now(UTC),
        execution_token=execution_token,
    )


def _row(conn: psycopg.Connection, notice_id: int) -> dict:
    return conn.execute(
        "select * from notice_summaries where notice_id=%s", (notice_id,)
    ).fetchone()


def test_same_source_failure_keeps_cards_and_metadata_across_model_prompt_changes(
    audit_db: psycopg.Connection,
) -> None:
    notice_id = _notice(audit_db)
    save_notice_summary(audit_db, _completed(notice_id, OLD_BODY, "2026-10-20", generation="v3"))
    before = _row(audit_db, notice_id)
    record_summary_failure(
        audit_db, notice_id, _metadata(OLD_BODY), reason_code="api_timeout"
    )
    after = _row(audit_db, notice_id)
    changed = {"last_error_code", "attempt_count", "updated_at"}
    assert {key: value for key, value in after.items() if key not in changed} == {
        key: value for key, value in before.items() if key not in changed
    }
    assert after["attempt_count"] == 2
    assert after["last_error_code"] == "api_timeout"
    assert after["card_summaries"] == after["result"]["card_summaries"]


def test_changed_source_failure_clears_cards_and_sorting_deadline_for_app(
    audit_db: psycopg.Connection,
) -> None:
    notice_id = _notice(audit_db)
    save_notice_summary(audit_db, _completed(notice_id, OLD_BODY, "2026-10-20"))
    before = _row(audit_db, notice_id)
    record_summary_failure(
        audit_db, notice_id, _metadata(NEW_BODY), reason_code="api_timeout"
    )
    row = _row(audit_db, notice_id)
    assert row["status"] == "needs_review"
    assert all(row[key] is None for key in (
        "result", "category", "category_code", "deadline_on", "card_summaries"
    ))
    for key in ("source_hash", "model", "prompt_version", "attachment_status", "generated_at"):
        assert row[key] == before[key]
    audit_db.execute("set local role anon")
    public = audit_db.execute(
        "select status,result,card_summaries,deadline_on,attachment_status "
        "from notice_summaries where notice_id=%s", (notice_id,)
    ).fetchone()
    view = build_notice_summary_view(
        status=public["status"], result=public["result"],
        attachment_status=public["attachment_status"],
    )
    assert view.status == "needs_review" and view.message == "원문 확인 요함"
    assert view.content is None


@pytest.mark.parametrize("late_outcome", ["success", "failure"])
def test_late_old_source_completion_does_not_destroy_current_source_success(
    audit_db: psycopg.Connection,
    late_outcome: str,
) -> None:
    notice_id = _notice(audit_db)
    # A has captured the old source. B captures and completes the new source
    # while A is still in Gemini; A then completes after B.
    old_metadata = _metadata(OLD_BODY)
    old_token = begin_summary_execution(audit_db, notice_id)
    audit_db.execute(
        "update notices set body_html=%s,content_updated_at=clock_timestamp() where id=%s",
        (NEW_BODY, notice_id),
    )
    current_token = begin_summary_execution(audit_db, notice_id)
    save_notice_summary(
        audit_db, _completed(notice_id, NEW_BODY, "2026-11-30", execution_token=current_token)
    )
    current = _row(audit_db, notice_id)
    with pytest.raises(SummaryExecutionSuperseded, match="summary_execution_superseded"):
        if late_outcome == "success":
            save_notice_summary(
                audit_db, _completed(notice_id, OLD_BODY, "2026-10-20", execution_token=old_token)
            )
        else:
            record_summary_failure(
                audit_db, notice_id, old_metadata, reason_code="api_timeout",
                execution_token=old_token,
            )
    after = _row(audit_db, notice_id)
    assert after["result"] == current["result"], (
        f"Late old-source {late_outcome} destroyed newer source content: "
        f"status={after['status']}, deadline={after['deadline_on']}, "
        f"cards={after['card_summaries']}"
    )
    assert after["status"] == "summarized"
    assert after["deadline_on"] == date(2026, 11, 30)
    assert after["card_summaries"] == current["card_summaries"]
    assert after == current


def test_late_old_prompt_completion_does_not_revert_newer_prompt_metadata(
    audit_db: psycopg.Connection,
) -> None:
    notice_id = _notice(audit_db)
    # The source text is identical, but a newer prompt/model generation has
    # completed before an older in-flight generation returns.
    old_token = begin_summary_execution(audit_db, notice_id)
    current_token = begin_summary_execution(audit_db, notice_id)
    save_notice_summary(
        audit_db, _completed(notice_id, OLD_BODY, "2026-10-20", generation="v4",
                            execution_token=current_token)
    )
    current = _row(audit_db, notice_id)
    with pytest.raises(SummaryExecutionSuperseded):
        save_notice_summary(
            audit_db, _completed(notice_id, OLD_BODY, "2026-10-20", generation="v3",
                                execution_token=old_token)
        )
    after = _row(audit_db, notice_id)
    assert (after["model"], after["prompt_version"]) == (
        current["model"], current["prompt_version"]
    ), "A late older execution reverted successful model/prompt metadata"


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_started_pending_then_one_job_execution_is_counted_once(
    audit_db: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    notice_id = _notice(audit_db)
    metadata = SummaryMetadata(
        source_hash=compute_source_hash(OLD_BODY),
        model="gemini-audit-v4",
        prompt_version=SUMMARY_PROMPT_VERSION,
        attachment_status="none",
    )
    save_notice_summary(
        audit_db, SummaryRecord(notice_id=notice_id, status="pending", metadata=metadata)
    )
    prepared = SimpleNamespace(notice_id=notice_id, warnings=())
    result = PreparedSummaryResult(
        notice_id=notice_id, summary=_summary("2026-10-20"), warnings=(), media_sources=()
    )

    def fake_summary(*args, **kwargs):
        if outcome == "failure":
            raise GeminiRequestError("api_timeout")
        return result

    monkeypatch.setattr("pipeline.summary_job.summarize_prepared_notice", fake_summary)
    summarize_and_save_prepared_notice(
        audit_db, prepared, metadata, api_key="unused-audit-key", attempt_increment=0
    )
    assert _row(audit_db, notice_id)["attempt_count"] == 1, (
        "One execution was counted at both pending start and terminal job save; "
        "the job has no attempt_increment=0 completion option"
    )


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_job_returns_superseded_without_publishing_or_recording_api_failure(
    audit_db: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    notice_id = _notice(audit_db)
    prepared = SimpleNamespace(notice_id=notice_id, warnings=())
    metadata = SummaryMetadata(
        source_hash=compute_source_hash(OLD_BODY), model="gemini-audit-v4",
        prompt_version=SUMMARY_PROMPT_VERSION, attachment_status="none",
    )

    def fake_summary(*args, **kwargs):
        latest = begin_summary_execution(audit_db, notice_id)
        save_notice_summary(
            audit_db, _completed(notice_id, NEW_BODY, "2026-11-30", execution_token=latest)
        )
        if outcome == "failure":
            raise GeminiRequestError("api_timeout")
        return PreparedSummaryResult(notice_id, _summary("2026-10-20"), (), ())

    monkeypatch.setattr("pipeline.summary_job.summarize_prepared_notice", fake_summary)
    outcome_value = summarize_and_save_prepared_notice(
        audit_db, prepared, metadata, api_key="unused-audit-key"
    )
    assert isinstance(outcome_value, StoredSummarySuperseded)
    assert outcome_value.status == "superseded"
    assert outcome_value.result is None
    assert outcome_value.reason_code == "summary_execution_superseded"
    current = _row(audit_db, notice_id)
    assert current["last_error_code"] is None and current["attempt_count"] == 1
    assert current["deadline_on"] == date(2026, 11, 30)


@pytest.mark.parametrize("status", ["pending", "failed", "summarized"])
def test_superseded_execution_cannot_insert_a_new_summary_row(
    audit_db: psycopg.Connection,
    status: str,
) -> None:
    notice_id = _notice(audit_db)
    old_token = begin_summary_execution(audit_db, notice_id)
    begin_summary_execution(audit_db, notice_id)
    with pytest.raises(SummaryExecutionSuperseded):
        if status == "summarized":
            save_notice_summary(
                audit_db, _completed(notice_id, OLD_BODY, "2026-10-20", execution_token=old_token)
            )
        else:
            save_notice_summary(audit_db, SummaryRecord(
                notice_id=notice_id, status=status, metadata=_metadata(OLD_BODY),
                last_error_code="api_timeout" if status == "failed" else None,
                execution_token=old_token,
            ))
    assert audit_db.execute(
        "select notice_id from notice_summaries where notice_id=%s", (notice_id,)
    ).fetchone() is None


def test_blocked_begin_allocates_token_after_lock_instead_of_reusing_insert_candidate(
    audit_db: psycopg.Connection,
) -> None:
    notice_id = _notice(audit_db)
    audit_db.commit()
    ready = Event()
    worker_pid: list[int] = []

    def overlapping_begin() -> int:
        with psycopg.connect(audit_db.info.dsn, connect_timeout=5) as conn:
            worker_pid.append(conn.info.backend_pid)
            ready.set()
            return begin_summary_execution(conn, notice_id)

    try:
        first = begin_summary_execution(audit_db, notice_id)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(overlapping_begin)
            try:
                assert ready.wait(5)
                until = monotonic() + 5
                waiting = False
                while monotonic() < until:
                    audit_db.execute("select pg_stat_clear_snapshot()")
                    waiting = audit_db.execute(
                        "select wait_event_type='Lock' as waiting "
                        "from pg_stat_activity where pid=%s",
                        (worker_pid[0],),
                    ).fetchone()["waiting"]
                    if waiting:
                        break
                    Event().wait(0.02)
                assert waiting, "The overlapping registration did not reach its row lock"
                latest_inside_lock = begin_summary_execution(audit_db, notice_id)
                assert latest_inside_lock > first
                audit_db.commit()
            finally:
                audit_db.rollback()
            newest = future.result(timeout=10)
        assert newest > latest_inside_lock
        assert audit_db.execute(
            "select execution_token from notice_summary_executions where notice_id=%s", (notice_id,)
        ).fetchone()["execution_token"] == newest
    finally:
        audit_db.rollback()
        audit_db.execute("delete from notices where id=%s", (notice_id,))
        audit_db.commit()
