"""Incomplete retry input must not erase an unchanged notice's valid summary."""

import os
from collections.abc import Iterator
from dataclasses import replace

import psycopg
import pytest
from psycopg.rows import dict_row
from test_gemini_multimodal import PreparationIssue, PreparedInput
from test_summary_execution_storage import NEW_BODY, OLD_BODY, _completed, _notice, _row

from pipeline.storage.summaries import begin_summary_execution, save_notice_summary
from pipeline.storage.summary_metadata import SummaryAttachmentText, build_summary_metadata
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.summary_job import (
    StoredSummaryFailure,
    StoredSummarySuperseded,
    summarize_and_save_prepared_notice,
)
from pipeline.transform import summarize as summarize_module
from pipeline.transform.gemini_client import GeminiRequestError
from pipeline.transform.gemini_prompt import SUMMARY_PROMPT_VERSION
from pipeline.transform.notice_input import NoticeInput, render_notice_input


@pytest.fixture
def preserved_notice() -> Iterator[tuple[psycopg.Connection, int]]:
    dsn = os.environ.get("AUDIT_TEST_DATABASE_URL") or os.environ.get("PIPELINE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("A disposable AUDIT_TEST_DATABASE_URL is required")
    conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True, connect_timeout=5)
    if conn.info.host != "127.0.0.1" or not conn.info.dbname.startswith("pipeline_schema_test_"):
        conn.close()
        pytest.fail("Use a disposable loopback pipeline_schema_test_* database")
    notice_id = _notice(conn)
    try:
        conn.execute(
            "insert into notice_files(notice_id,kind,file_id,file_key,file_name,url) "
            "values (%s,'attachment','hwp','id:hwp','attachment.hwp',"
            "'https://www.nowon.kr/audit.hwp')", (notice_id,),
        )
        yield conn, notice_id
    finally:
        conn.execute("reset role")
        conn.execute("delete from notices where id=%s", (notice_id,))
        conn.close()


def _metadata(*, readable: bool):
    return build_summary_metadata(
        body_text=OLD_BODY,
        attachment_texts=(SummaryAttachmentText("id:hwp", "참가비 무료"),) if readable else (),
        total_file_count=1, read_file_count=int(readable), model="gemini-preservation-test",
        prompt_version=SUMMARY_PROMPT_VERSION,
    )


def _prepared(notice_id: int, *, failed: bool = False):
    notice = NoticeInput(
        title="지원 사업 신청", body_text=OLD_BODY,
        reference_datetime="2026-10-07T12:00:00+09:00",
    )
    return PreparedInput(
        notice_id, notice, [{"type": "text", "text": render_notice_input(notice)}],
        failures=(PreparationIssue("extract", 1, "hwp_extraction_failed"),) if failed else (),
    )


def _publish(conn, notice_id):
    token = begin_summary_execution(conn, notice_id)
    save_notice_summary(conn, replace(
        _completed(notice_id, OLD_BODY, "2026-10-20", execution_token=token),
        metadata=_metadata(readable=True),
    ))


@pytest.mark.parametrize("failure", ["preparation", "timeout", "response_shape"])
def test_failed_retry_with_different_partial_input_hash_preserves_full_public_result(
    preserved_notice, monkeypatch, failure,
):
    conn, notice_id = preserved_notice
    _publish(conn, notice_id)
    before = _row(conn, notice_id)
    revision = conn.execute(
        "select content_revision from notices where id=%s", (notice_id,),
    ).fetchone()["content_revision"]
    partial = _metadata(readable=False)
    assert partial.source_hash != before["source_hash"]
    prepared = _prepared(notice_id, failed=failure == "preparation")
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        if failure == "timeout":
            raise GeminiRequestError("upstream timeout", reason_code="api_timeout")
        return "invalid provider JSON"

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    outcome = summarize_and_save_prepared_notice(
        conn, prepared, partial, expected_source_revision=revision,
        api_key="non-secret-unit-test-key",
    )
    assert isinstance(outcome, StoredSummaryFailure)
    assert len(calls) == {"preparation": 0, "timeout": 1, "response_shape": 2}[failure]
    after = _row(conn, notice_id)
    changed = {"last_error_code", "attempt_count", "updated_at"}
    assert {key: value for key, value in after.items() if key not in changed} == {
        key: value for key, value in before.items() if key not in changed
    }
    assert after["attempt_count"] == before["attempt_count"] + 1
    assert after["last_error_code"] == {
        "preparation": "input_preparation_failed", "timeout": "api_timeout",
        "response_shape": "response_validation_failed",
    }[failure]
    conn.execute("set role anon")
    public = conn.execute(
        "select status,result,card_summaries,attachment_status,deadline_on "
        "from notice_summaries where notice_id=%s", (notice_id,),
    ).fetchone()
    view = build_notice_summary_view(
        status=public["status"], result=public["result"],
        attachment_status=public["attachment_status"],
    )
    assert view.status == "summarized" and view.content is not None
    assert public["card_summaries"] == before["card_summaries"]
    assert public["deadline_on"] == before["deadline_on"]


def test_first_guarded_preparation_failure_still_inserts_failed_without_result(
    preserved_notice, monkeypatch,
):
    conn, notice_id = preserved_notice
    revision = conn.execute(
        "select content_revision from notices where id=%s", (notice_id,),
    ).fetchone()["content_revision"]
    prepared = _prepared(notice_id, failed=True)
    calls = []
    monkeypatch.setattr(
        summarize_module, "generate_summary_json", lambda **kwargs: calls.append(kwargs)
    )
    outcome = summarize_and_save_prepared_notice(
        conn, prepared, _metadata(readable=False),
        expected_source_revision=revision, api_key="non-secret-unit-test-key",
    )
    assert isinstance(outcome, StoredSummaryFailure) and calls == []
    row = _row(conn, notice_id)
    assert row["status"] == "failed" and row["last_error_code"] == "input_preparation_failed"
    assert row["attempt_count"] == 1
    assert all(row[key] is None for key in (
        "result", "card_summaries", "category", "category_code", "deadline_on", "generated_at",
    ))


def test_actual_source_edit_invalidates_result_and_rejects_inflight_failed_outcome(
    preserved_notice, monkeypatch,
):
    conn, notice_id = preserved_notice
    _publish(conn, notice_id)
    before = _row(conn, notice_id)
    revision = conn.execute(
        "select content_revision from notices where id=%s", (notice_id,),
    ).fetchone()["content_revision"]
    prepared = _prepared(notice_id)

    def generate(**_kwargs):
        with psycopg.connect(conn.info.dsn, autocommit=True) as collector:
            collector.execute("update notices set body_html=%s where id=%s", (NEW_BODY, notice_id))
        raise GeminiRequestError("upstream timeout", reason_code="api_timeout")

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    outcome = summarize_and_save_prepared_notice(
        conn, prepared, _metadata(readable=False), expected_source_revision=revision,
        api_key="non-secret-unit-test-key",
    )
    assert isinstance(outcome, StoredSummarySuperseded)
    after = _row(conn, notice_id)
    assert after["status"] == "needs_review" and after["result"] is None
    assert after["card_summaries"] is None and after["deadline_on"] is None
    assert after["attempt_count"] == before["attempt_count"]
    assert after["last_error_code"] == before["last_error_code"]
