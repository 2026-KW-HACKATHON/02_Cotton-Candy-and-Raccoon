"""Explicit single-date endpoint checks must preserve prose and avoid guessing."""

import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from support.card_deadline_endpoints import _dates, _input_response

from pipeline.storage.summary_metadata import build_summary_metadata
from pipeline.storage.summary_record import build_summary_record
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.transform import summarize as summarize_module
from pipeline.transform.card_claims import _deadline_relation_reasons, card_claim_review_reasons
from pipeline.transform.prepared_summary import PreparedSummaryResult


@pytest.mark.parametrize(("text", "reason"), [
    ("신청은 11월 3일까지이고 행사는 10월 3일에 시작해요.",
     "card_deadline_role_changed"),
    ("접수 마감은 2026-11-03이고 행사 시작은 2026-11-01이에요.",
     "card_deadline_role_changed"),
    ("신청 시작일은 2026.11.1.이에요.", "card_deadline_role_changed"),
    ("행사 종료는 2026년 10월 20일이에요.", "card_deadline_role_changed"),
    ("신청은 10월 3일까지예요.", "card_deadline_endpoint_changed"),
    ("신청은 10월 20일부터 가능해요.", "card_deadline_endpoint_changed"),
    ("신청 마감은 10월 3일이에요.", "card_deadline_endpoint_changed"),
    ("신청은 10월 20일에 시작해요.", "card_deadline_endpoint_changed"),
    ("행사 개시는 11월 3일이에요.", "card_deadline_endpoint_changed"),
    ("행사 종료일은 11월 1일이에요.", "card_deadline_endpoint_changed"),
    ("신청은 10월 3일(토) 오후 6시까지예요.", "card_deadline_endpoint_changed"),
    ("신청은 11월 3일까지이고 10월 3일에 시작해요.", "card_deadline_role_changed"),
    ("신청 시작은 10월 20일이고 마감은 10월 3일이에요.",
     "card_deadline_endpoint_changed"),
    ("신청 시작은 2026.10.03이고 마감은 2026.11.03이에요.",
     "card_deadline_role_changed"),
    ("신청은 10월 3일부터 11월 3일까지예요.", "card_deadline_role_changed"),
    ("신청은 11월 1일부터 10월 20일까지예요.", "card_deadline_role_changed"),
    ("신청기간: 2026.10.03.~2026.11.03.이에요.", "card_deadline_role_changed"),
])
def test_explicit_single_date_reassignment_is_detected(text, reason):
    entries = _dates()
    before = [entry.model_dump() for entry in entries]
    assert reason in _deadline_relation_reasons(text, entries)
    assert [entry.model_dump() for entry in entries] == before


@pytest.mark.parametrize("text", [
    "신청은 10월 20일까지이고 행사는 11월 1일에 시작해요.",
    "접수 시작은 2026-10-03이고 행사 종료일은 2026-11-03이에요.",
    "신청 마감일은 2026년 10월 20일이에요.",
    "행사 개시는 2026.11.1.이에요.",
    "신청은 10월 3일(토) 오전 10시부터 가능해요.",
    "신청은 10월 20일 오후 6시까지예요.",
    "신청은 10월 3일부터 10월 20일까지이고 행사는 11월 1일부터 11월 3일까지예요.",
    "신청 시작은 10월 3일이고 마감은 10월 20일이에요.",
    "신청은 10월 20일까지이고 10월 3일에 시작해요.",
    "신청은 2026.10.03에 시작하고 2026.10.20에 마감해요.",
    "신청기간: 2026.10.03.~2026.10.20.이에요.",
    "신청은 10월 20일까지이고 11월 1일에 시작하는 행사에 참여할 수 있어요.",
    "행사는 10월 3일부터 신청 가능하고 11월 1일에 시작해요.",
    "신청은 10월 3일부터 가능하며 11월 1일에 시작하는 행사에 참여하려면 "
    "10월 20일까지 신청해 주세요.",
])
def test_correct_single_endpoints_and_existing_ranges_remain_clean(text):
    assert _deadline_relation_reasons(text, _dates()) == []


@pytest.mark.parametrize("text", [
    "10월 3일까지 기다려 주세요.",  # No schedule subject.
    "신청은 11월 3일에 가능해요.",  # Start/end cannot be inferred from '가능'.
    "신청은 추후 공지해요. 11월 3일까지 기다려 주세요.",
    "신청은 추후 공지해요; 11월 3일까지 기다려 주세요.",
    "신청은 11월 3일에 마감하지 않아요.",
    "신청 마감은 11월 3일이 아니에요.",
    "신청 마감은 10월 3일부터예요.",  # Conflicting endpoint words.
    "신청은 10월 20일부터 10월 3일까지가 아니에요.",
    "신청은 10월 3일에 마감하지 않으며 10월 20일에 시작하지 않아요.",
])
def test_unlabelled_ambiguous_or_negated_prose_is_not_assigned(text):
    assert _deadline_relation_reasons(text, _dates()) == []


def test_multiple_same_role_schedules_do_not_guess_which_start_or_end_is_intended():
    entries = _dates()
    second_application = entries[0].model_copy(update={
        "label": "추가 신청기간", "start_date": "2026-10-21", "end_date": "2026-10-25",
    })
    entries.append(second_application)
    assert _deadline_relation_reasons("신청은 11월 3일까지예요.", entries) == []
    assert _deadline_relation_reasons(
        "신청 시작은 11월 1일이고 마감은 11월 3일이에요.", entries,
    ) == []


def test_missing_source_endpoint_is_left_unassigned():
    entries = _dates()
    entries[0].end_date = None
    assert _deadline_relation_reasons("신청은 11월 3일까지예요.", entries) == []


def test_unique_non_generic_source_label_supports_explicit_endpoint():
    entries = _dates()
    entries[0].label = "문화강좌 신청"
    assert "card_deadline_endpoint_changed" in _deadline_relation_reasons(
        "문화강좌 신청은 10월 3일까지예요.", entries,
    )


def test_existing_role_swapped_and_reversed_ranges_are_still_detected():
    assert "card_deadline_role_changed" in _deadline_relation_reasons(
        "신청은 11월 1일부터 11월 3일까지예요.", _dates(),
    )
    assert "card_deadline_range_reversed" in _deadline_relation_reasons(
        "신청은 10월 20일부터 10월 3일까지예요.", _dates(),
    )


@pytest.mark.parametrize(("text", "expected_status", "reason"), [
    ("신청은 10월 20일까지이고 행사는 11월 1일에 시작해요.", "summarized", None),
    ("신청은 11월 3일까지이고 행사는 10월 3일에 시작해요.", "needs_review",
     "card_deadline_role_changed"),
    ("신청은 10월 3일까지이고 행사는 11월 1일에 시작해요.", "needs_review",
     "card_deadline_endpoint_changed"),
    ("신청은 11월 3일까지이고 10월 3일에 시작해요. 행사는 11월 1일부터 11월 3일까지예요.",
     "needs_review", "card_deadline_role_changed"),
    ("신청 시작은 10월 20일이고 마감은 10월 3일이에요. 행사는 11월 1일부터 11월 3일까지예요.",
     "needs_review", "card_deadline_endpoint_changed"),
    ("신청은 10월 3일부터 11월 3일까지이고 행사는 11월 1일부터 11월 3일까지예요.",
     "needs_review", "card_deadline_role_changed"),
    ("신청 시작은 10월 3일이고 마감은 10월 20일이에요. 행사는 11월 1일부터 11월 3일까지예요.",
     "summarized", None),
    ("신청은 10월 20일까지이고 11월 1일에 시작하는 행사에 참여할 수 있어요.",
     "summarized", None),
    ("행사는 10월 3일부터 신청 가능하고 11월 1일에 시작해요.", "summarized", None),
])
def test_generation_record_and_public_view_preserve_prose_and_extracted_dates(
    monkeypatch, text, expected_status, reason,
):
    notice, response = _input_response()
    response["card_summaries"]["deadline"] = text
    original = deepcopy(response)
    calls = []

    def provider(**kwargs):
        calls.append(kwargs)
        return json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", provider)
    summary = summarize_module.summarize_notice(notice, api_key="offline-endpoint-audit")
    metadata = build_summary_metadata(
        body_text=notice.body_text, total_file_count=0, read_file_count=0,
        model="offline-endpoint-audit", prompt_version="endpoint-audit",
    )
    record = build_summary_record(
        PreparedSummaryResult(notice_id=9002, summary=summary, warnings=()), metadata,
        deadline_on=None, generated_at=datetime(2026, 10, 7, tzinfo=UTC),
    )
    view = build_notice_summary_view(
        status=record.status, result=record.result, attachment_status=metadata.attachment_status,
    )
    assert 1 <= len(calls) <= 2
    assert record.status == view.status == expected_status
    assert summary.card_summaries.deadline == text
    assert view.content.cards.deadline.text == text
    assert [entry.model_dump(mode="json") for entry in summary.dates] == original["dates"]
    assert summary.audience == original["audience"]
    assert summary.action == original["action"]
    assert summary.notes == original["notes"]
    if reason:
        assert reason in card_claim_review_reasons(summary, notice)
        assert summary.uncertainties
        assert record.deadline_on is None
    else:
        assert summary.uncertainties == []
