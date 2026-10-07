"""Validate summary storage contracts with mock transactions, never a database."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from pipeline.storage.summaries import (
    UPSERT_SUMMARY,
    UPSERT_SUMMARY_FAILURE,
    UPSERT_SUMMARY_PENDING,
    SummaryStorageError,
    record_summary_failure,
    save_notice_summary,
)
from pipeline.storage.summary_record import SummaryMetadata, SummaryRecord, SummaryRecordError
from pipeline.transform.summary_schema import NoticeSummary

PRIVATE_MARKER = "MOCK_PRIVATE_DATABASE_PAYLOAD_123"
GENERATED_AT = datetime(2026, 10, 3, 14, 30, tzinfo=timezone(timedelta(hours=9)))


def _metadata(**changes: Any) -> SummaryMetadata:
    values = {
        "source_hash": "ab" * 32,
        "model": "gemini-3.5-flash-lite",
        "prompt_version": "notice-summary-v3",
        "attachment_status": "all_read",
    }
    return SummaryMetadata(**(values | changes))


def _summary(**changes: Any) -> NoticeSummary:
    values = {
        "category": "application",
        "category_code": 27,
        "summary": "지원 사업 신청",
        "publisher": "노원구청",
        "applicable_area": "월계1동",
        "audience": "월계1동 주민",
        "audience_scope": "specific",
        "action": "주민센터 방문 신청",
        "action_requirement": "optional",
        "location": "월계1동 주민센터",
        "dates": [
            {
                "kind": "application",
                "label": "신청 기간",
                "text": "2026-10-03~2026-10-20",
                "start_date": "2026-10-03",
                "end_date": "2026-10-20",
                "start_time": "09:00",
                "end_time": "18:00",
            }
        ],
        "status": "open",
        "status_detail": "신청 접수 중",
        "notice_update": "new",
        "changed_details": None,
        "notes": ["신분증 지참"],
        "topics": [{"title": "지원 사업", "category": "application", "summary": "지원 사업 신청"}],
        "uncertainties": [],
        "evidence": [
            {
                "field": "summary",
                "excerpt": "지원 사업 신청",
                "source_type": "text",
                "source_id": None,
                "page": None,
                "verification": "text_matched",
            },
            {
                "field": "dates",
                "excerpt": "신청 기간: 2026-10-03~2026-10-20 오전 9시~오후 6시",
                "source_type": "text",
                "source_id": None,
                "page": None,
                "verification": "text_matched",
            },
            {
                "field": "notes",
                "excerpt": "신분증 지참",
                "source_type": "text",
                "source_id": None,
                "page": None,
                "verification": "text_matched",
            },
        ],
    }
    values["evidence"].extend(
        {
            "field": field,
            "excerpt": excerpt,
            "source_type": "text",
            "source_id": None,
            "page": None,
            "verification": "text_matched",
        }
        for field, excerpt in (
            ("category_code", "지원 사업 신청"),
            ("applicable_area", "월계1동"),
            ("audience", "월계1동 주민"),
            ("action", "주민센터 방문 신청"),
            ("location", "월계1동 주민센터"),
            ("topics", "지원 사업 신청"),
        )
    )
    return NoticeSummary.model_validate(values | changes)


def _record(status: str = "summarized", **changes: Any) -> SummaryRecord:
    values: dict[str, Any] = {"notice_id": 42, "status": status, "metadata": _metadata()}
    if status in ("summarized", "needs_review"):
        values["generated_at"] = GENERATED_AT
    if status == "summarized":
        values["result"] = _summary()
        values["deadline_on"] = date(2026, 10, 20)
    if status == "failed":
        values["last_error_code"] = "api_timeout"
    return SummaryRecord(**(values | changes))


def _connection(row: Any = (42,)) -> tuple[MagicMock, MagicMock]:
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = row
    return conn, cursor


def _assert_caller_keeps_transaction(conn: MagicMock) -> None:
    conn.commit.assert_not_called()
    conn.rollback.assert_not_called()
    conn.close.assert_not_called()


@pytest.mark.parametrize("status", ["pending", "summarized", "needs_review", "failed"])
def test_all_four_summary_states_bind_their_complete_row_and_leave_transaction_to_caller(
    status: str,
) -> None:
    record = _record(status)
    conn, cursor = _connection()
    assert save_notice_summary(conn, record) == 42
    conn.cursor.assert_called_once_with(row_factory=tuple_row)
    cursor.execute.assert_called_once()
    sql, values = cursor.execute.call_args.args
    assert sql == {"failed": UPSERT_SUMMARY_FAILURE, "pending": UPSERT_SUMMARY_PENDING}.get(
        status, UPSERT_SUMMARY
    )
    assert len(values) == 14
    assert values[13] is None
    assert values[:2] == (42, status)
    assert values[6:11] == ("all_read", "ab" * 32, "gemini-3.5-flash-lite", "notice-summary-v3", 1)
    assert values[11] == ("api_timeout" if status == "failed" else None)
    assert values[12] == (GENERATED_AT if status in {"summarized", "needs_review"} else None)
    _assert_caller_keeps_transaction(conn)


def test_storage_preserves_the_entire_verified_summary_json() -> None:
    record = _record()
    conn, cursor = _connection()
    save_notice_summary(conn, record)
    _, values = cursor.execute.call_args.args
    assert isinstance(values[2], Jsonb)
    assert values[2].obj == record.result.model_dump(mode="json")
    assert values[3] == "application"
    assert values[4] == 27
    assert values[5] == date(2026, 10, 20)
    assert all(item["verification"] == "text_matched" for item in values[2].obj["evidence"])
    assert values[12].utcoffset() == timedelta(hours=9)


def test_pending_state_binds_null_result_columns() -> None:
    conn, cursor = _connection()
    save_notice_summary(conn, _record())
    save_notice_summary(conn, _record("pending"))
    sql, values = cursor.execute.call_args.args
    assert values[2:6] == (None, None, None, None)
    assert values[12] is None
    for column in (
        "result",
        "category",
        "category_code",
        "deadline_on",
        "generated_at",
        "last_error_code",
    ):
        assert f"{column} = excluded.{column}" not in sql
    assert sql == UPSERT_SUMMARY_PENDING
    assert values[11] is None
    _assert_caller_keeps_transaction(conn)


def test_failure_atomically_withholds_changed_source_and_preserves_generation_metadata() -> None:
    conn, cursor = _connection()
    assert record_summary_failure(conn, 42, _metadata(), reason_code="api_timeout") == 42
    sql, values = cursor.execute.call_args.args
    assert sql == UPSERT_SUMMARY_FAILURE
    updates = sql.split("do update set", 1)[1].split("returning", 1)[0]
    assert "source_hash is distinct from excluded.source_hash" in updates
    assert "then 'needs_review' else notice_summaries.status end" in updates
    assert "last_error_code = excluded.last_error_code" in updates
    for column in ("source_hash", "model", "prompt_version", "generated_at", "attachment_status"):
        assert f"{column} =" not in updates
    assert values[:6] == (42, "failed", None, None, None, None)
    assert values[10:] == (1, "api_timeout", None, None)
    assert "select " not in sql.lower()
    _assert_caller_keeps_transaction(conn)


def test_attempt_count_is_incremented_in_the_single_upsert_without_a_prior_read() -> None:
    conn, cursor = _connection()
    save_notice_summary(conn, _record("pending", attempt_increment=1))
    save_notice_summary(conn, _record(attempt_increment=0))
    calls = cursor.execute.call_args_list
    assert len(calls) == 2
    assert calls[0].args[1][10] == 1
    assert calls[1].args[1][10] == 0
    assert (
        "attempt_count = notice_summaries.attempt_count + excluded.attempt_count" in UPSERT_SUMMARY
    )
    assert "on conflict (notice_id) do update" in UPSERT_SUMMARY
    assert "select " not in UPSERT_SUMMARY.lower()
    _assert_caller_keeps_transaction(conn)


@pytest.mark.parametrize(
    "changes",
    [
        {"result": _summary(category="unknown")},
        {"result": _summary(uncertainties=["원문 확인 필요"])},
        {"metadata": _metadata(attachment_status="partial")},
        {"metadata": _metadata(attachment_status="unread")},
    ],
)
def test_uncertain_or_incompletely_read_summary_cannot_be_claimed_as_summarized(
    changes: dict[str, Any],
) -> None:
    with pytest.raises(SummaryRecordError) as failure:
        _record(**changes)
    assert failure.value.reason_code == "summary_requires_review"


@pytest.mark.parametrize("attachment_status", ["none", "all_read", "partial", "unread"])
def test_review_state_preserves_summary_and_category_without_a_sorting_deadline(
    attachment_status: str,
) -> None:
    record = _record(
        "needs_review",
        metadata=_metadata(attachment_status=attachment_status),
        result=_summary(uncertainties=["원문 확인 필요"]),
    )
    conn, cursor = _connection()
    save_notice_summary(conn, record)
    values = cursor.execute.call_args.args[1]
    assert values[2].obj == record.result.model_dump(mode="json")
    assert values[3:6] == ("application", 27, None)
    assert values[6] == attachment_status


@pytest.mark.parametrize("status", ["pending", "failed", "needs_review"])
def test_only_summarized_state_can_store_a_deadline(status: str) -> None:
    with pytest.raises(SummaryRecordError) as failure:
        _record(status, deadline_on=date(2026, 10, 20))
    assert failure.value.reason_code == "unexpected_deadline_on"


@pytest.mark.parametrize(
    ("changes", "reason_code"),
    [
        ({"result": None}, "summary_result_required"),
        ({"result": {}}, "summary_result_required"),
        ({"generated_at": None}, "summary_generated_at_required"),
        ({"last_error_code": "api_error"}, "unexpected_error_code"),
    ],
)
def test_result_states_require_summary_and_generation_time_without_failure_code(
    changes: dict[str, Any], reason_code: str
) -> None:
    with pytest.raises(SummaryRecordError) as failure:
        _record(**changes)
    assert failure.value.reason_code == reason_code


@pytest.mark.parametrize("result", [{}, "invalid"])
def test_review_state_rejects_invalid_summary_payload(result: Any) -> None:
    with pytest.raises(SummaryRecordError, match="summary_result_required"):
        _record("needs_review", result=result)


def test_review_without_generated_content_remains_compatible_with_invalidated_rows() -> None:
    conn, cursor = _connection()
    save_notice_summary(conn, _record("needs_review"))
    assert cursor.execute.call_args.args[1][2:6] == (None, None, None, None)


def test_review_unknown_classification_retains_json_without_inventing_category_columns() -> None:
    summary = _summary(category="unknown", category_code=None)
    conn, cursor = _connection()
    save_notice_summary(conn, _record("needs_review", result=summary))
    values = cursor.execute.call_args.args[1]
    assert values[2].obj == summary.model_dump(mode="json")
    assert values[3:6] == (None, None, None)


@pytest.mark.parametrize(
    ("changes", "reason_code"),
    [
        ({"generated_at": None}, "summary_generated_at_required"),
        ({"last_error_code": "api_error"}, "unexpected_error_code"),
    ],
)
def test_review_state_requires_generation_time_without_failure_code(
    changes: dict[str, Any], reason_code: str
) -> None:
    with pytest.raises(SummaryRecordError) as failure:
        _record("needs_review", **changes)
    assert failure.value.reason_code == reason_code


@pytest.mark.parametrize("status", ["pending", "failed"])
@pytest.mark.parametrize("changes", [{"result": _summary()}, {"generated_at": GENERATED_AT}])
def test_pending_or_failed_state_cannot_retain_a_success_payload(
    status: str, changes: dict[str, Any]
) -> None:
    with pytest.raises(SummaryRecordError) as failure:
        _record(status, **changes)
    assert failure.value.reason_code == "unexpected_summary_result"


@pytest.mark.parametrize("error_code", [None, True, 429, PRIVATE_MARKER, "api_error: secret"])
def test_failed_state_accepts_only_safe_known_failure_codes(error_code: Any) -> None:
    with pytest.raises(SummaryRecordError) as failure:
        _record("failed", last_error_code=error_code)
    assert failure.value.reason_code == "invalid_error_code"
    assert PRIVATE_MARKER not in str(failure.value)


def test_pending_state_cannot_have_a_failure_code() -> None:
    with pytest.raises(SummaryRecordError, match="unexpected_error_code"):
        _record("pending", last_error_code="api_timeout")


@pytest.mark.parametrize(
    ("field", "value", "reason_code"),
    [
        ("source_hash", True, "invalid_source_hash"),
        ("source_hash", "g" * 64, "invalid_source_hash"),
        ("source_hash", "ab" * 31, "invalid_source_hash"),
        ("source_hash", "AB" * 32, "invalid_source_hash"),
        ("model", True, "invalid_model"),
        ("model", "", "invalid_model"),
        ("model", "model\n" + PRIVATE_MARKER, "invalid_model"),
        ("prompt_version", True, "invalid_prompt_version"),
        ("prompt_version", "", "invalid_prompt_version"),
        ("prompt_version", "version " + PRIVATE_MARKER, "invalid_prompt_version"),
        ("attachment_status", True, "invalid_attachment_status"),
        ("attachment_status", "done", "invalid_attachment_status"),
        ("attachment_status", [], "invalid_attachment_status"),
        ("attachment_status", {}, "invalid_attachment_status"),
    ],
)
def test_invalid_metadata_is_rejected_with_field_code_not_its_private_value(
    field: str, value: Any, reason_code: str
) -> None:
    with pytest.raises(SummaryRecordError) as failure:
        _metadata(**{field: value})
    assert failure.value.reason_code == reason_code
    assert PRIVATE_MARKER not in str(failure.value)


@pytest.mark.parametrize(
    ("changes", "reason_code"),
    [
        ({"notice_id": True}, "invalid_notice_id"),
        ({"notice_id": 0}, "invalid_notice_id"),
        ({"notice_id": -1}, "invalid_notice_id"),
        ({"notice_id": 2**63}, "invalid_notice_id"),
        ({"notice_id": 42.0}, "invalid_notice_id"),
        ({"notice_id": "42"}, "invalid_notice_id"),
        ({"status": True}, "invalid_summary_status"),
        ({"status": "done"}, "invalid_summary_status"),
        ({"status": []}, "invalid_summary_status"),
        ({"status": {}}, "invalid_summary_status"),
        ({"metadata": True}, "invalid_summary_metadata"),
        ({"attempt_increment": True}, "invalid_attempt_increment"),
        ({"attempt_increment": -1}, "invalid_attempt_increment"),
        ({"attempt_increment": 2}, "invalid_attempt_increment"),
        ({"attempt_increment": 2**31 - 1}, "invalid_attempt_increment"),
        ({"attempt_increment": 2**31}, "invalid_attempt_increment"),
        ({"attempt_increment": 1.0}, "invalid_attempt_increment"),
        ({"deadline_on": True}, "invalid_deadline_on"),
        ({"deadline_on": GENERATED_AT}, "invalid_deadline_on"),
        ({"generated_at": True}, "invalid_generated_at"),
        ({"generated_at": datetime(2026, 10, 3)}, "invalid_generated_at"),
    ],
)
def test_record_rejects_invalid_types_and_integer_date_boundaries(
    changes: dict[str, Any], reason_code: str
) -> None:
    with pytest.raises(SummaryRecordError) as failure:
        _record(**changes)
    assert failure.value.reason_code == reason_code


def test_constructing_a_record_snapshots_nested_summary_values() -> None:
    original = _summary()
    record = _record(result=original)
    before = record.result.model_dump(mode="json")
    original.notes.append("다른 메모")
    original.dates[0].end_date = "2027-01-01"
    original.evidence[1].page = 9
    conn, cursor = _connection()
    save_notice_summary(conn, record)
    assert record.result.model_dump(mode="json") == before
    assert cursor.execute.call_args.args[1][2].obj == before


@pytest.mark.parametrize("mutation", ["too_long_note", "bad_page", "wrong_nested_type"])
def test_mutating_record_nested_summary_is_revalidated_before_cursor_open(mutation: str) -> None:
    record = _record()
    if mutation == "too_long_note":
        record.result.notes.append("가" * 61)
    elif mutation == "bad_page":
        record.result.evidence[1].page = 0
    else:
        record.result.dates.append("invalid date record")
    conn, _ = _connection()
    with pytest.raises(SummaryRecordError) as failure:
        save_notice_summary(conn, record)
    assert failure.value.reason_code == "invalid_summary_result"
    conn.cursor.assert_not_called()
    _assert_caller_keeps_transaction(conn)


def test_mutating_record_into_uncertainty_cannot_bypass_summarized_status_rule() -> None:
    record = _record()
    record.result.uncertainties.append("원문 확인 필요")
    conn, _ = _connection()
    with pytest.raises(SummaryRecordError, match="summary_requires_review"):
        save_notice_summary(conn, record)
    conn.cursor.assert_not_called()


@pytest.mark.parametrize("record", [None, True, {}, "invalid record"])
def test_storage_rejects_non_record_before_opening_cursor(record: Any) -> None:
    conn, _ = _connection()
    with pytest.raises(SummaryRecordError, match="invalid_summary_record"):
        save_notice_summary(conn, record)
    conn.cursor.assert_not_called()
    _assert_caller_keeps_transaction(conn)


@pytest.mark.parametrize("stage", ["cursor", "execute", "fetch", "exit"])
@pytest.mark.parametrize("error_type", [psycopg.IntegrityError, psycopg.OperationalError])
def test_database_errors_never_return_success_or_expose_the_bound_summary(
    stage: str, error_type: type[psycopg.Error]
) -> None:
    conn, cursor = _connection()
    error = error_type(PRIVATE_MARKER)
    if stage == "cursor":
        conn.cursor.side_effect = error
    elif stage == "execute":
        cursor.execute.side_effect = error
    elif stage == "fetch":
        cursor.fetchone.side_effect = error
    else:
        conn.cursor.return_value.__exit__.side_effect = error
    with pytest.raises(SummaryStorageError) as failure:
        save_notice_summary(conn, _record())
    assert failure.value.reason_code == "summary_storage_failed"
    assert str(failure.value) == "summary_storage_failed"
    assert PRIVATE_MARKER not in str(failure.value)
    assert failure.value.__cause__ is None
    assert failure.value.__suppress_context__ is True
    _assert_caller_keeps_transaction(conn)


@pytest.mark.parametrize("row", [None, (), (43,), (True,), ("42",), (42, 43), 42, {"id": 42}])
def test_missing_malformed_or_mismatched_returning_id_is_failure(row: Any) -> None:
    conn, _ = _connection(row)
    with pytest.raises(SummaryStorageError) as failure:
        save_notice_summary(conn, _record())
    assert failure.value.reason_code == "summary_storage_missing_id"
    _assert_caller_keeps_transaction(conn)


def test_serialized_json_is_detached_from_later_record_mutation() -> None:
    record = _record()
    conn, cursor = _connection()
    save_notice_summary(conn, record)
    stored = cursor.execute.call_args.args[1][2].obj
    record.result.notes.append("추가 메모")
    record.result.evidence[1].excerpt = "변경된 기간"
    assert stored["notes"] == ["신분증 지참"]
    assert stored["evidence"][1]["excerpt"] == "신청 기간: 2026-10-03~2026-10-20 오전 9시~오후 6시"


def test_optional_deadline_remains_caller_supplied_instead_of_using_every_end_date() -> None:
    # The JSON still has an application end date, but the storage owner chooses no deadline.
    conn, cursor = _connection()
    save_notice_summary(conn, replace(_record(), deadline_on=None))
    values = cursor.execute.call_args.args[1]
    assert values[2].obj["dates"][0]["end_date"] == "2026-10-20"
    assert values[5] is None
