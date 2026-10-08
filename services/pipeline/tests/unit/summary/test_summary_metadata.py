"""Verify source identity, full-file coverage and deterministic deadline behavior."""

from dataclasses import replace
from datetime import date
from unittest.mock import MagicMock

import pytest
from support.gemini_multimodal import _prepared
from support.summary_storage import GENERATED_AT, _metadata, _summary

from pipeline import summary_job
from pipeline.storage.summary_deadline import compute_deadline_on
from pipeline.storage.summary_metadata import (
    SummaryAttachmentText,
    build_summary_metadata,
    compute_source_hash,
    resolve_attachment_status,
)
from pipeline.storage.summary_record import SummaryRecordError, build_summary_record
from pipeline.transform.gemini_prompt import SUMMARY_PROMPT_VERSION
from pipeline.transform.prepared_summary import PreparedSummaryResult
from pipeline.transform.summary_schema import NoticeSummary


def test_file_key_order_and_duplicate_roles_do_not_change_hash_but_content_does() -> None:
    first = SummaryAttachmentText("id:first", "첫 첨부 내용")
    second = SummaryAttachmentText("url:" + "a" * 64, "다음 첨부 내용")
    hashed = compute_source_hash("공지 본문", [first, second])
    assert len(hashed) == 64
    assert hashed == compute_source_hash("공지 본문", [second, first, first])
    assert hashed != compute_source_hash("수정 본문", [first, second])
    assert hashed != compute_source_hash("공지 본문", [replace(first, text="수정 첨부"), second])
    assert hashed != compute_source_hash(
        "공지 본문", [replace(first, file_key="id:changed"), second]
    )


def test_hash_has_unambiguous_boundaries_for_body_and_attachment_text() -> None:
    assert compute_source_hash("a", [SummaryAttachmentText("id:b", "c")]) != compute_source_hash(
        "ab", [SummaryAttachmentText("id:b", "c")]
    )
    assert compute_source_hash("a", [SummaryAttachmentText("id:b", "c\nd")]) != compute_source_hash(
        "a", [SummaryAttachmentText("id:b", "c"), SummaryAttachmentText("id:d", "d")]
    )


def test_conflicting_extracted_text_for_same_file_key_is_rejected() -> None:
    with pytest.raises(SummaryRecordError, match="conflicting_attachment_text"):
        compute_source_hash(
            "", [SummaryAttachmentText("id:a", "one"), SummaryAttachmentText("id:a", "two")]
        )


@pytest.mark.parametrize("attachment_texts", ["", None, iter(())])
def test_hash_rejects_inputs_that_cannot_be_rechecked_for_file_coverage(attachment_texts) -> None:
    with pytest.raises(SummaryRecordError, match="invalid_attachment_text"):
        compute_source_hash("", attachment_texts)


@pytest.mark.parametrize(
    ("total", "read", "expected"),
    [(0, 0, "none"), (2, 0, "unread"), (2, 1, "partial"), (2, 2, "all_read")],
)
def test_attachment_status_uses_all_original_files(total: int, read: int, expected: str) -> None:
    metadata = build_summary_metadata(
        body_text="본문", total_file_count=total, read_file_count=read, model="gemini-test"
    )
    assert metadata.attachment_status == expected
    assert metadata.prompt_version == SUMMARY_PROMPT_VERSION
    assert metadata.source_hash == compute_source_hash("본문")


@pytest.mark.parametrize(("total", "read"), [(0, 1), (2, 3), (-1, 0), (1, -1), (True, 1), (1, 1.0)])
def test_invalid_or_coerced_file_counts_cannot_claim_complete_input(total, read) -> None:
    with pytest.raises(SummaryRecordError, match="invalid_attachment_counts"):
        resolve_attachment_status(total_file_count=total, read_file_count=read)


def test_extracted_text_cannot_outnumber_successfully_read_files() -> None:
    with pytest.raises(SummaryRecordError, match="invalid_attachment_counts"):
        build_summary_metadata(
            body_text="",
            attachment_texts=[SummaryAttachmentText("id:a", "읽은 내용")],
            total_file_count=1,
            read_file_count=0,
            model="gemini-test",
        )


def _date(kind: str, end: str | None) -> dict:
    return {
        "kind": kind,
        "label": "일정",
        "text": None,
        "start_date": None,
        "end_date": end,
        "start_time": None,
        "end_time": None,
    }


def test_deadline_uses_latest_relevant_end_date_and_ignores_later_event_dates() -> None:
    summary = _summary(
        dates=[
            _date("application", "2026-10-10"),
            _date("submission", "2026-10-20"),
            _date("payment", "2026-10-15"),
            _date("event", "2027-01-01"),
            _date("application", None),
        ]
    )
    assert compute_deadline_on(summary) == date(2026, 10, 20)


@pytest.mark.parametrize("dates", [[], [_date("event", "2026-10-20")], [_date("payment", None)]])
def test_no_relevant_end_date_leaves_deadline_empty(dates: list[dict]) -> None:
    assert compute_deadline_on(_summary(dates=dates)) is None


def test_job_uses_default_deadline_rule_for_verified_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = PreparedSummaryResult(notice_id=17, summary=_summary(), warnings=(), media_sources=())
    monkeypatch.setattr(summary_job, "summarize_prepared_notice", lambda *_args, **_kwargs: result)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.side_effect = [
        (17,), (17, "summarized", date(2026, 10, 20), GENERATED_AT, None),
    ]
    metadata = replace(_metadata(), prompt_version=SUMMARY_PROMPT_VERSION)
    stored = summary_job.summarize_and_save_prepared_notice(
        conn, _prepared("본문"), metadata, expected_source_revision=1,
    )
    assert stored.status == "summarized"
    assert stored.deadline_on == date(2026, 10, 20)
    assert (
        conn.cursor.return_value.__enter__.return_value.execute.call_args.args[1][7]
        == stored.deadline_on
    )


def test_job_rejects_metadata_for_another_prompt_before_calling_gemini(monkeypatch) -> None:
    generate = MagicMock()
    monkeypatch.setattr(summary_job, "summarize_prepared_notice", generate)
    conn = MagicMock()
    with pytest.raises(SummaryRecordError, match="prompt_version_mismatch"):
        summary_job.summarize_and_save_prepared_notice(
            conn, _prepared("본문"), _metadata(), expected_source_revision=1,
        )
    generate.assert_not_called()
    conn.cursor.assert_not_called()


@pytest.mark.parametrize("reason", ["unknown-code", "missing-code-evidence", "file-code-evidence"])
def test_unverified_subject_preserves_review_summary_without_a_sorting_deadline(
    reason: str,
) -> None:
    data = _summary().model_dump(mode="json")
    if reason == "unknown-code":
        data["category_code"] = None
        data["evidence"] = [e for e in data["evidence"] if e["field"] != "category_code"]
    elif reason == "missing-code-evidence":
        data["evidence"] = [e for e in data["evidence"] if e["field"] != "category_code"]
    else:
        evidence = next(e for e in data["evidence"] if e["field"] == "category_code")
        evidence.update(
            source_type="document", source_id="media_1", page=1, verification="file_reference_only"
        )
    result = PreparedSummaryResult(
        notice_id=17, summary=NoticeSummary.model_validate(data), warnings=(), media_sources=()
    )
    record = build_summary_record(
        result, _metadata(), deadline_on=date(2026, 10, 20), generated_at=GENERATED_AT
    )
    assert record.status == "needs_review"
    assert record.result.model_dump(mode="json") == result.summary.model_dump(mode="json")
    assert record.deadline_on is None
