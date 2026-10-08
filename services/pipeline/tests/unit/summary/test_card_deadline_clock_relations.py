"""Keep clocks attached to explicit schedule roles and start/end endpoints."""

import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from support.card_deadline_endpoints import _dates, _input_response

from pipeline.storage.summary_deadline import compute_deadline_on
from pipeline.storage.summary_metadata import build_summary_metadata
from pipeline.storage.summary_record import build_summary_record
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.transform import summarize as summarize_module
from pipeline.transform.card_claims import _deadline_relation_reasons, card_claim_review_reasons
from pipeline.transform.prepared_summary import PreparedSummaryResult


def _clock_dates():
    entries = _dates()
    entries[0] = entries[0].model_copy(update={
        "text": None, "start_time": "14:00", "end_time": "20:00",
    })
    entries[1] = entries[1].model_copy(update={
        "text": None, "start_time": "10:00", "end_time": "14:00",
    })
    return entries


@pytest.mark.parametrize(("text", "reason"), [
    ("신청은 10월 3일 10시부터 10월 20일 20시까지예요.",
     "card_deadline_time_role_changed"),
    ("신청은 10월 3일 14시부터 10월 20일 14시까지예요.",
     "card_deadline_time_endpoint_changed"),
    ("신청은 10월 3일 20시부터 10월 20일 14시까지예요.",
     "card_deadline_time_endpoint_changed"),
    ("행사는 11월 1일 20시부터 11월 3일 14시까지예요.",
     "card_deadline_time_role_changed"),
    ("행사는 11월 1일 14시부터 11월 3일 10시까지예요.",
     "card_deadline_time_endpoint_changed"),
    ("신청은 10월 3일 오전 10시부터 10월 20일 오후 8시까지예요.",
     "card_deadline_time_role_changed"),
    ("신청 마감은 10월 20일 오후 2시예요.",
     "card_deadline_time_endpoint_changed"),
    ("접수 시작은 10월 3일 20:00이에요.",
     "card_deadline_time_endpoint_changed"),
    ("신청은 10월 20일 14시까지예요.", "card_deadline_time_endpoint_changed"),
    ("신청은 10월 3일 10시에 시작해요.", "card_deadline_time_role_changed"),
    ("신청은 10월 3일 14시에 시작하고 10월 20일 14시에 마감해요.",
     "card_deadline_time_endpoint_changed"),
    ("신청 시작은 10월 3일 20시이고 마감은 10월 20일 14시예요.",
     "card_deadline_time_endpoint_changed"),
    ("신청 마감은 오후 2시예요.", "card_deadline_time_endpoint_changed"),
    ("행사 시작은 20:00이에요.", "card_deadline_time_role_changed"),
    ("신청은 10시부터 20시까지예요.", "card_deadline_time_role_changed"),
    ("신청기간: 2026.10.03. 20:00~2026.10.20. 14:00이에요.",
     "card_deadline_time_endpoint_changed"),
])
def test_known_clock_reassigned_to_other_role_or_endpoint_is_detected(text, reason):
    entries = _clock_dates()
    original = [entry.model_dump() for entry in entries]
    assert reason in _deadline_relation_reasons(text, entries)
    assert [entry.model_dump() for entry in entries] == original


@pytest.mark.parametrize("text", [
    "신청은 10월 3일 14시부터 10월 20일 20시까지예요.",
    "신청은 10월 3일 오후 2시부터 10월 20일 오후 8시까지예요.",
    "신청은 2026.10.03. 14:00~2026.10.20. 20:00이에요.",
    "신청은 10월 3일(토) 오후 2시부터 10월 20일(화) 오후 8시까지예요.",
    "신청은 10월 3일에 14시부터 10월 20일에 20시까지예요.",
    "행사는 11월 1일 오전 10시부터 11월 3일 오후 2시까지예요.",
    "신청 마감은 10월 20일 20:00이에요.",
    "접수 시작은 10월 3일 오후 2시예요.",
    "신청은 10월 20일 오후 8시까지예요.",
    "신청은 10월 3일 14시에 시작하고 10월 20일 20시에 마감해요.",
    "신청 시작은 10월 3일 14시이고 마감은 10월 20일 20시예요.",
    "신청 마감은 오후 8시예요.",
    "행사 시작은 오전 10시예요.",
    "신청은 14시부터 20시까지예요.",
    "신청은 10월 3일 오후 2시부터 10월 20일 오후 8시까지이고 "
    "행사는 11월 1일 오전 10시부터 11월 3일 오후 2시까지예요.",
    "신청은 오후 8시부터 오후 2시까지가 아니라 오후 2시부터 오후 8시까지이고, "
    "행사는 11월 1일 오전 10시부터 11월 3일 오후 2시까지예요.",
    "신청은 10월 20일 오후 8시부터 10월 3일 오후 2시까지가 아니라 "
    "10월 3일 오후 2시부터 10월 20일 오후 8시까지예요.",
    "신청은 오후 8시부터 오후 2시까지가 아니라 마감은 오후 8시예요.",
    "신청 마감은 오후 2시가 아니라 오후 8시예요.",
    "신청은 오후 2시까지가 아니라 오후 8시예요.",
    "신청 시작은 오후 8시가 아니라 오후 2시이고 "
    "마감은 오후 2시가 아니라 오후 8시예요.",
    "신청 마감은 오후 2시가 아니라 오후 8시이고 "
    "시작은 오후 8시가 아니라 오후 2시예요.",
])
def test_correct_role_endpoint_and_equivalent_clock_notation_stays_clean(text):
    assert _deadline_relation_reasons(text, _clock_dates()) == []


@pytest.mark.parametrize("text", [
    "10월 20일 오후 2시까지 기다려 주세요.",
    "신청은 10월 20일 오후 2시에 가능해요.",
    "신청은 추후 공지해요. 오후 2시까지 기다려 주세요.",
    "신청은 10월 20일 오후 2시에 마감하지 않아요.",
    "신청 마감은 10월 20일 오후 2시가 아니에요.",
    "신청 마감은 오후 2시가 아니라 오후 8시예요.",
    "신청은 10월 3일 20시부터 10월 20일 14시까지가 아니에요.",
    "신청은 20시부터 14시까지 운영하지 않아요.",
    "신청은 20시부터 14시까지 신청할 수 없어요.",
    "신청은 10월 20일 14시까지 신청할 수 없어요.",
    "신청은 10월 20일 14시까지 신청이 안 돼요.",
    "신청 마감은 20시부터 14시까지예요.",
    "신청은 문의센터가 14시에 마감할 때까지 가능해요.",
    "신청은 문의센터가 10월 20일 14시에 마감할 때까지 가능해요.",
    "신청은 14시부터 20시까지예요. 문의센터는 10시부터 14시까지 운영해요.",
    "신청은 14시부터 20시까지이고 문의센터는 10시부터 14시까지 운영해요.",
    "행사는 10월 3일 오후 2시부터 신청 가능하고 11월 1일 오전 10시에 시작해요.",
    "신청은 10월 20일 오후 8시까지이고 11월 1일 오후 2시에 시작하는 행사에 "
    "참여할 수 있어요.",
])
def test_ambiguous_unlabelled_and_negated_clock_claims_are_not_assigned(text):
    assert _deadline_relation_reasons(text, _clock_dates()) == []


@pytest.mark.parametrize(("text", "reason"), [
    ("신청은 오후 8시부터 오후 2시까지가 아니라 오전 10시부터 오후 8시까지예요.",
     "card_deadline_time_role_changed"),
    ("신청은 오후 8시부터 오후 2시까지가 아니라 오후 8시부터 오후 2시까지예요.",
     "card_deadline_time_endpoint_changed"),
    ("신청은 오후 8시부터 오후 2시까지가 아니라 마감은 오후 2시예요.",
     "card_deadline_time_endpoint_changed"),
    ("신청 마감은 오후 8시가 아니라 오후 2시예요.",
     "card_deadline_time_endpoint_changed"),
    ("신청은 오후 8시까지가 아니라 오후 2시예요.",
     "card_deadline_time_endpoint_changed"),
    ("신청 시작은 오후 2시가 아니라 오후 8시이고 "
     "마감은 오후 2시가 아니라 오후 8시예요.",
     "card_deadline_time_endpoint_changed"),
    ("신청 시작은 오후 8시가 아니라 오후 2시이고 "
     "마감은 오후 8시가 아니라 오후 2시예요.",
     "card_deadline_time_endpoint_changed"),
    ("신청 마감은 오후 2시가 아니라 오후 8시이고 "
     "시작은 오후 2시가 아니라 오전 10시예요.",
     "card_deadline_time_role_changed"),
    ("신청은 오후 8시부터 오후 2시까지가 아니라 오후 2시부터 오후 8시까지이고, "
     "행사는 11월 1일 오후 8시부터 11월 3일 오후 2시까지예요.",
     "card_deadline_time_role_changed"),
    ("신청은 10월 20일 오후 8시부터 10월 3일 오후 2시까지가 아니라 "
     "10월 3일 오전 10시부터 10월 20일 오후 8시까지예요.",
     "card_deadline_time_role_changed"),
])
def test_denied_range_does_not_suppress_later_affirmed_range_or_deadline(text, reason):
    assert reason in _deadline_relation_reasons(text, _clock_dates())


def test_multiple_same_role_schedules_and_missing_clock_do_not_guess():
    entries = _clock_dates()
    entries.append(entries[0].model_copy(update={
        "label": "추가 신청", "start_time": "10:00", "end_time": "14:00",
    }))
    assert _deadline_relation_reasons("신청 마감은 오후 2시예요.", entries) == []
    entries = _clock_dates()
    entries[0].end_time = None
    assert _deadline_relation_reasons("신청 마감은 오후 2시예요.", entries) == []


@pytest.mark.parametrize(("start", "end", "text"), [
    ("23:00", "01:00", "행사는 11월 1일 23시부터 11월 2일 오전 1시까지예요."),
    ("00:00", "12:00", "행사는 11월 1일 오전 12시부터 11월 2일 오후 12시까지예요."),
])
def test_explicit_next_day_and_midnight_noon_clocks_stay_clean(start, end, text):
    entry = _clock_dates()[1].model_copy(update={
        "end_date": "2026-11-02", "start_time": start, "end_time": end,
    })
    assert _deadline_relation_reasons(text, [entry]) == []


@pytest.mark.parametrize(("text", "reason"), [
    ("행사는 11월 1일 14시부터 10시까지예요.", "card_deadline_time_endpoint_changed"),
    ("행사는 11월 1일 오후 2시~오전 10시예요.", "card_deadline_time_endpoint_changed"),
    ("행사는 11월 1일 오전 10시부터 오후 2시까지예요.", None),
])
def test_same_day_two_clock_range_uses_individual_endpoints(text, reason):
    entry = _clock_dates()[1].model_copy(update={"end_date": "2026-11-01"})
    reasons = _deadline_relation_reasons(text, [entry])
    if reason:
        assert reason in reasons
    else:
        assert reasons == []


def _clock_input_response():
    notice, response = _input_response()
    quotes = [
        "신청기간: 2026-10-03 14:00~2026-10-20 20:00",
        "행사기간: 2026-11-01 10:00~2026-11-03 14:00",
    ]
    notice = notice.model_copy(update={
        "body_text": "\n".join([
            notice.title, response["audience"], response["action"], *response["notes"], *quotes,
        ]),
    })
    response["dates"] = [entry.model_dump(mode="json") for entry in _clock_dates()]
    response["evidence"] = [item for item in response["evidence"] if item["field"] != "dates"]
    response["evidence"].extend({"field": "dates", "excerpt": quote} for quote in quotes)
    return notice, response


@pytest.mark.parametrize(("text", "expected_status", "reason"), [
    ("신청은 10월 3일 14시부터 10월 20일 20시까지이고 "
     "행사는 11월 1일 오전 10시부터 11월 3일 오후 2시까지예요.", "summarized", None),
    ("신청은 10월 3일 10시부터 10월 20일 20시까지이고 "
     "행사는 11월 1일 14시부터 11월 3일 14시까지예요.", "needs_review",
     "card_deadline_time_role_changed"),
    ("신청은 10월 3일 20시부터 10월 20일 14시까지이고 "
     "행사는 11월 1일 10시부터 11월 3일 14시까지예요.", "needs_review",
     "card_deadline_time_endpoint_changed"),
    ("신청은 오후 8시부터 오후 2시까지가 아니라 오후 2시부터 오후 8시까지이고, "
     "행사는 11월 1일 오전 10시부터 11월 3일 오후 2시까지예요.", "summarized", None),
    ("신청은 10월 20일 오후 8시부터 10월 3일 오후 2시까지가 아니라 "
     "10월 3일 오후 2시부터 10월 20일 오후 8시까지이고, "
     "행사는 11월 1일 오전 10시부터 11월 3일 오후 2시까지예요.", "summarized", None),
    ("신청은 오후 8시부터 오후 2시까지가 아니라 오전 10시부터 오후 8시까지이고, "
     "행사는 11월 1일 오전 10시부터 11월 3일 오후 2시까지예요.", "needs_review",
     "card_deadline_time_role_changed"),
    ("신청 시작은 오후 8시가 아니라 오후 2시이고 마감은 오후 2시가 아니라 오후 8시예요. "
     "행사는 11월 1일 오전 10시부터 11월 3일 오후 2시까지예요.", "summarized", None),
    ("신청 시작은 오후 8시가 아니라 오후 2시이고 마감은 오후 8시가 아니라 오후 2시예요. "
     "행사는 11월 1일 오전 10시부터 11월 3일 오후 2시까지예요.", "needs_review",
     "card_deadline_time_endpoint_changed"),
])
def test_generation_storage_and_view_keep_clock_swapped_prose_without_extra_call(
    monkeypatch, text, expected_status, reason,
):
    notice, response = _clock_input_response()
    response["card_summaries"]["deadline"] = text
    original = deepcopy(response)
    calls = []

    def provider(**kwargs):
        calls.append(kwargs)
        return json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", provider)
    summary = summarize_module.summarize_notice(notice, api_key="offline-clock-audit")
    metadata = build_summary_metadata(
        body_text=notice.body_text, total_file_count=0, read_file_count=0,
        model="offline-clock-audit", prompt_version="clock-audit",
    )
    record = build_summary_record(
        PreparedSummaryResult(notice_id=9100, summary=summary, warnings=()), metadata,
        deadline_on=compute_deadline_on(summary), generated_at=datetime(2026, 10, 7, tzinfo=UTC),
    )
    view = build_notice_summary_view(
        status=record.status, result=record.result, attachment_status=metadata.attachment_status,
    )
    assert len(calls) == 1
    assert record.status == view.status == expected_status
    assert summary.card_summaries.deadline == view.content.cards.deadline.text == text
    assert [entry.model_dump(mode="json") for entry in summary.dates] == original["dates"]
    assert summary.summary == original["summary"]
    assert summary.audience == original["audience"]
    assert summary.action == original["action"]
    assert summary.notes == original["notes"]
    if reason:
        assert reason in card_claim_review_reasons(summary, notice)
        assert summary.uncertainties
        assert record.deadline_on is None
    else:
        assert summary.uncertainties == []
        assert record.deadline_on.isoformat() == "2026-10-20"
