"""A failed correction cannot replace a usable summary for the current source."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from test_gemini_multimodal import PreparedInput
from test_guarded_failure_preservation import (
    _metadata,
    _publish,
)
from test_guarded_failure_preservation import (
    preserved_notice as preserved_notice,
)
from test_summary_execution_storage import NEW_BODY, OLD_BODY, _completed, _row
from test_summary_grounding_retry import FACT, _notice, _response

from pipeline.storage.summaries import (
    StoredPreparedSummary,
    SummaryExecutionSuperseded,
    begin_summary_execution,
    record_summary_failure,
    save_prepared_summary,
)
from pipeline.storage.summary_metadata import build_summary_metadata
from pipeline.storage.summary_record import SummaryRecordError
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.summary_job import summarize_and_save_prepared_notice
from pipeline.transform import summarize as summarize_module
from pipeline.transform.gemini_client import GeminiRequestError
from pipeline.transform.gemini_prompt import SUMMARY_PROMPT_VERSION
from pipeline.transform.notice_input import render_notice_input
from pipeline.transform.prepared_summary import PreparedSummaryResult

CANDIDATE_TIME = datetime(2026, 12, 1, tzinfo=UTC)


def _candidate(notice_id, *, deadline="2026-10-20", reason="api_timeout"):
    completed = _completed(notice_id, OLD_BODY, deadline)
    summary = completed.result.model_copy(deep=True)
    summary.summary = "재생성된 지원 사업 신청 안내"
    summary.card_summaries.deadline = deadline + "까지 신청해요."
    return PreparedSummaryResult(
        notice_id, summary, (), correction_failure_code=reason,
    )


def _save(conn, candidate, metadata, *, token=None):
    return save_prepared_summary(
        conn, candidate, metadata, deadline_on=date(2026, 10, 20),
        generated_at=CANDIDATE_TIME, execution_token=token,
    )


@pytest.mark.parametrize("existing_status", ["summarized", "needs_review"])
@pytest.mark.parametrize("partial_input", [False, True])
@pytest.mark.parametrize("writer", ["postgres", "service_role"])
def test_guarded_correction_failure_preserves_existing_public_content_and_actual_row_metadata(
    preserved_notice, existing_status, partial_input, writer,
):
    conn, notice_id = preserved_notice
    _publish(conn, notice_id)
    if existing_status == "needs_review":
        conn.execute(
            "update notice_summaries set status='needs_review',deadline_on=null where notice_id=%s",
            (notice_id,),
        )
    before = _row(conn, notice_id)
    if writer == "service_role":
        conn.execute("set role service_role")
    token = begin_summary_execution(conn, notice_id)
    candidate = _candidate(notice_id)
    stored = _save(conn, candidate, _metadata(readable=not partial_input), token=token)
    after = _row(conn, notice_id)
    changed = {"last_error_code", "attempt_count", "updated_at"}
    assert {key: value for key, value in after.items() if key not in changed} == {
        key: value for key, value in before.items() if key not in changed
    }
    assert after["last_error_code"] == "api_timeout"
    assert after["attempt_count"] == before["attempt_count"] + 1
    assert stored.status == before["status"]
    assert stored.deadline_on == before["deadline_on"]
    assert stored.generated_at == before["generated_at"] != CANDIDATE_TIME
    assert stored.result.summary.summary == candidate.summary.summary
    assert stored.result.summary.summary != after["result"]["summary"]
    assert stored.result.correction_failure_code == "api_timeout"
    conn.execute("set role anon")
    public = conn.execute(
        "select status,result,card_summaries,attachment_status,deadline_on "
        "from notice_summaries where notice_id=%s", (notice_id,),
    ).fetchone()
    view = build_notice_summary_view(
        status=public["status"], result=public["result"],
        attachment_status=public["attachment_status"],
    )
    assert view.content is not None
    assert view.content.headline.value == before["result"]["summary"]
    assert public["card_summaries"] == before["card_summaries"]
    assert public["deadline_on"] == before["deadline_on"]


@pytest.mark.parametrize("previous", ["missing", "failed", "invalidated"])
def test_first_usable_fallback_is_retained_for_review_when_no_public_result_exists(
    preserved_notice, previous,
):
    conn, notice_id = preserved_notice
    metadata = _metadata(readable=True)
    if previous == "failed":
        record_summary_failure(
            conn, notice_id, metadata, reason_code="api_connection_error",
            execution_token=begin_summary_execution(conn, notice_id),
        )
    elif previous == "invalidated":
        _publish(conn, notice_id)
        conn.execute("update notices set body_html=%s where id=%s", (NEW_BODY, notice_id))
        assert _row(conn, notice_id)["result"] is None
        metadata = build_summary_metadata(
            body_text=NEW_BODY, attachment_texts=(), total_file_count=1, read_file_count=0,
            model="gemini-candidate-test", prompt_version="notice-summary-test",
        )
    candidate = _candidate(notice_id, deadline="2026-11-30" if previous == "invalidated"
                           else "2026-10-20")
    # Fully verified, uncertainty-free input still marks a failed correction.
    assert candidate.summary.uncertainties == []
    stored = _save(conn, candidate, metadata, token=begin_summary_execution(conn, notice_id))
    row = _row(conn, notice_id)
    assert stored.status == row["status"] == "needs_review"
    assert stored.deadline_on is row["deadline_on"] is None
    assert stored.generated_at == row["generated_at"] == CANDIDATE_TIME
    assert row["result"] == candidate.summary.model_dump(mode="json")
    assert row["card_summaries"] == row["result"]["card_summaries"]
    assert row["last_error_code"] == "api_timeout"
    assert row["attempt_count"] == (1 if previous == "missing" else 2)
    conn.execute("set role anon")
    public = conn.execute(
        "select status,result,attachment_status from notice_summaries where notice_id=%s",
        (notice_id,),
    ).fetchone()
    view = build_notice_summary_view(
        status=public["status"], result=public["result"],
        attachment_status=public["attachment_status"],
    )
    assert view.content is not None and view.message == "원문 확인 요함"


@pytest.mark.parametrize("superseded_by", ["new_execution", "source_change"])
def test_obsolete_correction_fallback_cannot_write_or_increment_attempts(
    preserved_notice, superseded_by,
):
    conn, notice_id = preserved_notice
    _publish(conn, notice_id)
    old_token = begin_summary_execution(conn, notice_id)
    if superseded_by == "new_execution":
        begin_summary_execution(conn, notice_id)
    else:
        conn.execute("update notices set body_html=%s where id=%s", (NEW_BODY, notice_id))
    before = _row(conn, notice_id)
    with pytest.raises(SummaryExecutionSuperseded):
        _save(conn, _candidate(notice_id), _metadata(readable=True), token=old_token)
    assert _row(conn, notice_id) == before


@pytest.mark.parametrize("same_hash", [True, False])
def test_legacy_correction_fallback_preserves_only_a_matching_source_hash(
    preserved_notice, same_hash,
):
    conn, notice_id = preserved_notice
    _publish(conn, notice_id)
    before = _row(conn, notice_id)
    candidate = _candidate(notice_id)
    stored = _save(conn, candidate, _metadata(readable=same_hash))
    after = _row(conn, notice_id)
    assert after["last_error_code"] == "api_timeout" and after["attempt_count"] == 2
    if same_hash:
        assert stored.status == "summarized" and stored.generated_at == before["generated_at"]
        assert after["result"] == before["result"]
        assert after["deadline_on"] == before["deadline_on"]
    else:
        assert stored.status == "needs_review" and stored.generated_at == CANDIDATE_TIME
        assert after["result"] == candidate.summary.model_dump(mode="json")
        assert after["deadline_on"] is None


@pytest.mark.parametrize("invalid_code", ["private provider text", "", 7, True, [], {}])
def test_correction_failure_rejects_invalid_codes_before_any_database_write(
    preserved_notice, invalid_code,
):
    conn, notice_id = preserved_notice
    _publish(conn, notice_id)
    before = _row(conn, notice_id)
    candidate = replace(_candidate(notice_id), correction_failure_code=invalid_code)
    with pytest.raises(SummaryRecordError, match="invalid_error_code"):
        _save(conn, candidate, _metadata(readable=True))
    assert _row(conn, notice_id) == before


def test_correction_fallback_keeps_the_callers_transaction_and_rolls_back_as_a_unit(
    preserved_notice,
):
    conn, notice_id = preserved_notice
    _publish(conn, notice_id)
    token = begin_summary_execution(conn, notice_id)
    before = _row(conn, notice_id)
    with pytest.raises(RuntimeError, match="force rollback"):
        with conn.transaction():
            _save(conn, _candidate(notice_id), _metadata(readable=True), token=token)
            assert _row(conn, notice_id)["last_error_code"] == "api_timeout"
            raise RuntimeError("force rollback")
    assert _row(conn, notice_id) == before


@pytest.mark.parametrize("existing_result", [False, True])
def test_actual_job_correction_timeout_keeps_first_or_previous_summary_through_anon_view(
    preserved_notice, monkeypatch, existing_result,
):
    conn, notice_id = preserved_notice
    notice = _notice()
    conn.execute("update notices set title=%s,body_html=%s where id=%s", (
        notice.title, notice.body_text, notice_id,
    ))
    prepared = PreparedInput(
        notice_id, notice, [{"type": "text", "text": render_notice_input(notice)}],
    )
    metadata = build_summary_metadata(
        body_text=notice.body_text, attachment_texts=(), total_file_count=0, read_file_count=0,
        model="gemini-full-job-test", prompt_version=SUMMARY_PROMPT_VERSION,
    )
    revision = conn.execute(
        "select content_revision from notices where id=%s", (notice_id,),
    ).fetchone()["content_revision"]
    if existing_result:
        monkeypatch.setattr(summarize_module, "generate_summary_json", lambda **_kwargs:
                            json.dumps(_response(FACT), ensure_ascii=False))
        initial = summarize_and_save_prepared_notice(
            conn, prepared, metadata, expected_source_revision=revision, api_key="mock-key",
        )
        assert isinstance(initial, StoredPreparedSummary) and initial.status == "summarized"
    before = _row(conn, notice_id)
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return json.dumps(_response(), ensure_ascii=False)
        assert len(calls) == 2
        raise GeminiRequestError("mock correction timeout", reason_code="api_timeout")

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    stored = summarize_and_save_prepared_notice(
        conn, prepared, metadata, expected_source_revision=revision, api_key="mock-key",
    )
    assert isinstance(stored, StoredPreparedSummary) and len(calls) == 2
    row = _row(conn, notice_id)
    assert row["last_error_code"] == "api_timeout"
    assert stored.result.correction_failure_code == "api_timeout"
    assert row["attempt_count"] == (2 if existing_result else 1)
    if existing_result:
        changed = {"last_error_code", "attempt_count", "updated_at"}
        assert {key: value for key, value in row.items() if key not in changed} == {
            key: value for key, value in before.items() if key not in changed
        }
        assert stored.status == "summarized"
        assert stored.generated_at == before["generated_at"]
        assert row["result"]["summary"] == FACT != stored.result.summary.summary
    else:
        assert stored.status == "needs_review" and stored.deadline_on is None
        assert row["result"] == stored.result.summary.model_dump(mode="json")
        assert row["result"]["card_summaries"] == _response()["card_summaries"]
    conn.execute("set role anon")
    public = conn.execute(
        "select status,result,attachment_status from notice_summaries where notice_id=%s",
        (notice_id,),
    ).fetchone()
    view = build_notice_summary_view(
        status=public["status"], result=public["result"],
        attachment_status=public["attachment_status"],
    )
    assert view.content is not None
    assert view.content.headline.value == (FACT if existing_result else _response()["summary"])
    assert view.message == (None if existing_result else "원문 확인 요함")
