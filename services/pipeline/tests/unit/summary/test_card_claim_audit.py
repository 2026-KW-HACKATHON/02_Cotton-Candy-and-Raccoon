"""Independent adversarial checks for claims introduced only in Gemini card prose."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest

from pipeline.storage.summary_metadata import build_summary_metadata
from pipeline.storage.summary_record import SummaryRecord, build_summary_record
from pipeline.storage.summary_view import NoticeSummaryView, build_notice_summary_view
from pipeline.transform import summarize as summarize_module
from pipeline.transform.card_claims import card_claim_review_reasons
from pipeline.transform.grounding import unknown_summary
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.prepared_summary import PreparedSummaryResult
from pipeline.transform.summary_schema import Evidence, NoticeSummary, SummaryValidationError

TITLE = "성인 안전교육 신청 안내"
AUDIENCE = "월계1동 주민 중 만 19세 이상 성인"
ACTION = "신청자는 신분증 사본을 반드시 제출"
DATE_QUOTE = "신청기간: 2026-10-03~2026-10-20"
NOTES = ["참가비: 30,000원", "환불은 불가하나 수업 취소 시 전액 환불"]
MUTATIONS = [
    pytest.param(
        "audience", "거주지와 나이에 관계없이 누구나 신청할 수 있어요.",
        id="eligibility-expanded",
    ),
    pytest.param(
        "notes", "참가비는 무료예요; 환불은 불가하지만 수업 취소 시에는 전액 환불돼요.",
        id="paid-course-made-free",
    ),
    pytest.param(
        "deadline", "신청 기간은 2026년 10월 3일부터 11월 30일까지예요.",
        id="deadline-extended-without-source",
    ),
    pytest.param(
        "action", "신분증 사본 제출은 선택이며 제출하지 않아도 신청할 수 있어요.",
        id="required-document-made-optional",
    ),
    pytest.param(
        "notes", "참가비는 30,000원이에요; 사유와 관계없이 환불은 불가해요.",
        id="refund-exception-denied",
    ),
    pytest.param(
        "notes", "참가비는 30,000원이에요; 환불은 불가해요.",
        id="refund-exception-omitted",
    ),
    pytest.param(
        "notes", "참가비는 30,000원이에요; 수업 취소 시에도 전액 환불은 불가해요.",
        id="refund-exception-explicitly-negated",
    ),
    pytest.param(
        "audience", "월계1동 주민 중 만 65세 이상 성인이 대상이에요.",
        id="unsupported-age-added",
    ),
    pytest.param("notes", "신분증을 준비해 주세요.", id="verified-fee-omitted"),
    pytest.param(
        "deadline", "신청 기간은 2026년 10월 3일부터 10월 20일 오후 8시까지예요.",
        id="unsupported-clock-added",
    ),
    pytest.param(
        "deadline", "신청 기간은 2027년 10월 3일부터 10월 20일까지예요.",
        id="unsupported-year-added",
    ),
    pytest.param(
        "notes", "참가비는 3만원이에요; 1만원을 지원해요; 수업 취소 시 전액 환불돼요.",
        id="unsupported-subsidy-added",
    ),
    pytest.param(
        "notes", "참가비는 3만원이에요; 강좌 취소 때도 전액 환불되지 않아요.",
        id="refund-exception-negated-with-keywords-retained",
    ),
    pytest.param(
        "audience", "월계1동 주민 중 만 19세 미만인 사람이 대상이에요.",
        id="age-comparison-reversed",
    ),
    pytest.param(
        "deadline", "신청 기간은 10월 20일부터 10월 3일까지예요.",
        id="single-period-direction-reversed",
    ),
]


def _notice() -> NoticeInput:
    return NoticeInput.model_validate({
        "title": TITLE,
        "body_text": "\n".join([
            TITLE, "대상: " + AUDIENCE, DATE_QUOTE, "할 일: " + ACTION, *NOTES,
        ]),
        "reference_datetime": "2026-10-07T12:00:00+09:00",
    })


def _response() -> dict[str, Any]:
    data = unknown_summary(_notice()).model_dump(mode="json")
    data.update(
        category="application", category_code=22, summary=TITLE,
        audience=AUDIENCE, audience_scope="conditional",
        action=ACTION, action_requirement="required", status="open", notice_update="new",
        uncertainties=[], dates=[{
            "kind": "application", "label": "신청기간", "text": "2026-10-03~2026-10-20",
            "start_date": "2026-10-03", "end_date": "2026-10-20",
            "start_time": None, "end_time": None,
        }], notes=deepcopy(NOTES),
        evidence=[
            {"field": field, "excerpt": excerpt}
            for field, excerpt in [
                ("summary", TITLE), ("category", TITLE), ("category_code", TITLE),
                ("audience", "대상: " + AUDIENCE), ("action", "할 일: " + ACTION),
                ("action_requirement", "할 일: " + ACTION), ("dates", DATE_QUOTE),
                *(("notes", note) for note in NOTES),
            ]
        ],
        card_summaries={
            "audience": AUDIENCE + "이 대상이에요.",
            "deadline": "신청 기간은 2026년 10월 3일부터 10월 20일까지예요.",
            "action": "신청자는 신분증 사본을 반드시 제출해 주세요.",
            "notes": "참가비는 30,000원이에요; 환불은 불가하지만 수업 취소 시에는 전액 환불돼요.",
        },
    )
    return data


def _run(
    monkeypatch: pytest.MonkeyPatch, response: dict[str, Any]
) -> tuple[NoticeSummary, SummaryRecord, NoticeSummaryView, list[dict[str, Any]]]:
    calls = []

    def provider(**kwargs: Any) -> str:
        calls.append(kwargs)
        return json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", provider)
    notice = _notice()
    summary = summarize_module.summarize_notice(notice, api_key="offline-audit")
    metadata = build_summary_metadata(
        body_text=notice.body_text, total_file_count=0, read_file_count=0,
        model="offline-audit", prompt_version="card-audit",
    )
    record = build_summary_record(
        PreparedSummaryResult(notice_id=9001, summary=summary, warnings=()), metadata,
        deadline_on=None, generated_at=datetime(2026, 10, 7, tzinfo=UTC),
    )
    view = build_notice_summary_view(
        status=record.status, result=record.result, attachment_status=metadata.attachment_status,
    )
    return summary, record, view, calls


def test_audit_control_preserves_all_source_facts_and_verified_quotes(monkeypatch) -> None:
    response = _response()
    summary, record, view, calls = _run(monkeypatch, response)
    assert len(calls) == 1
    assert summary.uncertainties == []
    assert summary.audience == AUDIENCE
    assert summary.action == ACTION
    assert summary.action_requirement == "required"
    assert summary.dates[0].end_date == "2026-10-20"
    assert summary.notes == NOTES
    assert all(item.verification == "text_matched" for item in summary.evidence)
    assert all(item.excerpt in _notice().body_text for item in summary.evidence)
    assert record.status == view.status == "summarized"
    assert view.message is None
    assert summary.card_summaries is not None
    assert summary.card_summaries.model_dump() == response["card_summaries"]
    assert card_claim_review_reasons(summary, _notice()) == ()


@pytest.mark.parametrize(("slot", "text"), MUTATIONS)
def test_bounded_guard_detects_the_reproduced_contradictions_without_mutating_facts(
    monkeypatch, slot, text
) -> None:
    summary, _, _, _ = _run(monkeypatch, _response())
    assert summary.card_summaries is not None
    setattr(summary.card_summaries, slot, text)
    before = summary.model_dump(mode="json")
    reasons = card_claim_review_reasons(summary, _notice())
    assert reasons
    assert all(reason.startswith("card_") and reason.isascii() for reason in reasons)
    assert summary.model_dump(mode="json") == before


@pytest.mark.parametrize("cards", [
    {
        "audience": "월계1동에 거주하며 만 19세 이상인 성인이 신청할 수 있어요.",
        "deadline": "2026년 10월 3일부터 10월 20일까지 신청할 수 있어요.",
        "action": "전화 문의는 선택이에요; 신청자는 신분증 사본을 반드시 제출해 주세요.",
        "notes": "참가비는 3만원이에요; 수업이 취소되면 전액 환불돼요.",
    },
    {
        "audience": "월계1동 주민 중 만 19세 이상인 사람은 누구나 신청할 수 있어요.",
        "deadline": "신청 기간은 2026.10.3.~2026.10.20.이에요.",
        "action": "신청자는 신분증 사본을 반드시 준비해 주세요.",
        "notes": "참가비는 30천원이에요; 교육 취소 시에는 전액 환불해 드려요.",
    },
    {
        "audience": "월계1동 주민 중 만 19세부터 신청할 수 있어요.",
        "deadline": "마감은 10월 20일이고 신청 시작은 10월 3일이에요.",
        "action": "신분증 사본 제출은 선택이 아니에요.",
        "notes": "참가비는 무료가 아니며 3만원이에요; 수업 취소 시 전액 환불돼요.",
    },
])
def test_guard_accepts_natural_paraphrases_and_equivalent_currency_units(
    monkeypatch, cards
) -> None:
    summary, _, _, _ = _run(monkeypatch, _response())
    for slot, text in cards.items():
        setattr(summary.card_summaries, slot, text)
    assert card_claim_review_reasons(summary, _notice()) == ()


def test_guard_accepts_mixed_currency_units_and_korean_clock_paraphrases(monkeypatch) -> None:
    summary, _, _, _ = _run(monkeypatch, _response())
    notice_data = _notice().model_dump()
    notice_data["body_text"] = notice_data["body_text"].replace("30,000원", "28,000원")
    notice_data["body_text"] = notice_data["body_text"].replace(
        DATE_QUOTE, DATE_QUOTE + " 14:00~20:00"
    )
    notice = NoticeInput.model_validate(notice_data)
    summary.notes[0] = "참가비: 28,000원"
    for item in summary.evidence:
        item.excerpt = item.excerpt.replace("30,000원", "28,000원")
        if item.field == "dates":
            item.excerpt += " 14:00~20:00"
    summary.dates[0].start_time, summary.dates[0].end_time = "14:00", "20:00"
    summary.card_summaries.notes = "참가비는 2만8천원이에요; 수업 취소 시에는 전액 환불돼요."
    summary.card_summaries.deadline = "10월 3일 오후 2시부터 10월 20일 오후 8시까지예요."
    assert card_claim_review_reasons(summary, notice) == ()


def test_age_numbers_are_not_inferred_from_dong_numbers_or_calendar_years(monkeypatch) -> None:
    summary, _, _, _ = _run(monkeypatch, _response())
    data = _notice().model_dump()
    data["body_text"] = data["body_text"].replace(AUDIENCE, "월계1동 주민")
    notice = NoticeInput.model_validate(data)
    summary.audience = "월계1동 주민"
    for item in summary.evidence:
        if item.field == "audience":
            item.excerpt = "대상: 월계1동 주민"
    summary.card_summaries.audience = "월계1동 주민이 대상이에요."
    assert card_claim_review_reasons(summary, notice) == ()
    summary.card_summaries.audience = "월계1동 주민 중 만 65세 이상인 분이 대상이에요."
    assert "card_audience_age_added" in card_claim_review_reasons(summary, notice)
    summary.audience_scope = "general"
    summary.card_summaries.audience = "거주지와 관계없이 누구나 신청할 수 있어요."
    assert "card_audience_unrestricted" in card_claim_review_reasons(summary, notice)


def test_age_range_retains_both_endpoints_and_allows_natural_expansion(monkeypatch) -> None:
    summary, _, _, _ = _run(monkeypatch, _response())
    audience = "월계1동 주민 중 만 19~39세"
    data = _notice().model_dump()
    data["body_text"] = data["body_text"].replace(AUDIENCE, audience)
    notice = NoticeInput.model_validate(data)
    summary.audience = audience
    for item in summary.evidence:
        if item.field == "audience":
            item.excerpt = "대상: " + audience
    summary.card_summaries.audience = "월계1동 주민 중 만 19세부터 39세까지가 대상이에요."
    assert card_claim_review_reasons(summary, notice) == ()
    summary.card_summaries.audience = "월계1동 주민 중 만 39세가 대상이에요."
    assert "card_audience_age_omitted" in card_claim_review_reasons(summary, notice)


def test_amount_and_age_conditions_in_support_notes_cannot_disappear(monkeypatch) -> None:
    summary, _, _, _ = _run(monkeypatch, _response())
    note = "만 24세 이하에게 28,000원 지원금 지급"
    data = _notice().model_dump()
    data["body_text"] += "\n" + note
    notice = NoticeInput.model_validate(data)
    summary.notes.append(note)
    summary.evidence.append(Evidence(field="notes", excerpt=note, verification="text_matched"))
    summary.card_summaries.notes += " 만 24세 이하에게 2만8천원 지원금을 지급해요."
    assert card_claim_review_reasons(summary, notice) == ()
    summary.card_summaries.notes = _response()["card_summaries"]["notes"]
    reasons = card_claim_review_reasons(summary, notice)
    assert "card_notes_amount_omitted" in reasons
    assert "card_notes_age_omitted" in reasons


def test_guard_allows_an_explicit_source_fee_waiver_without_inventing_free_cost(
    monkeypatch
) -> None:
    summary, _, _, _ = _run(monkeypatch, _response())
    note = "기초생활수급자는 참가비 무료"
    data = _notice().model_dump()
    data["body_text"] += "\n" + note
    notice = NoticeInput.model_validate(data)
    summary.notes.append(note)
    summary.evidence.append(Evidence(field="notes", excerpt=note, verification="text_matched"))
    summary.card_summaries.notes += " 기초생활수급자는 참가비가 무료예요."
    assert card_claim_review_reasons(summary, notice) == ()


def test_guard_does_not_promote_an_unverified_quote_or_inspect_media(monkeypatch) -> None:
    summary, _, _, _ = _run(monkeypatch, _response())
    summary.card_summaries.audience = "누구나 신청할 수 있어요."
    for item in summary.evidence:
        if item.field == "audience":
            item.verification = None
    assert card_claim_review_reasons(summary, _notice()) == ()
    for item in summary.evidence:
        if item.field == "audience":
            item.source_type, item.source_id = "image", "media_1"
            item.verification = "file_reference_only"
    assert card_claim_review_reasons(summary, _notice()) == ()


@pytest.mark.parametrize(("slot", "text"), MUTATIONS)
def test_adversarial_card_must_be_rejected_or_marked_for_review(monkeypatch, slot, text) -> None:
    canonical, _, _, _ = _run(monkeypatch, _response())
    response = _response()
    response["card_summaries"][slot] = text
    try:
        summary, record, view, _ = _run(monkeypatch, response)
    except SummaryValidationError:
        return  # A semantic rejection also prevents an unqualified public claim.
    assert summary.model_dump(exclude={"card_summaries", "uncertainties"}) == canonical.model_dump(
        exclude={"card_summaries", "uncertainties"}
    )
    assert view.content is not None
    assert getattr(view.content.cards, slot).text == text
    # Matching evidence for the untouched old fields cannot validate this new claim.
    assert record.status == "needs_review"
    assert view.status == "needs_review"
    assert view.message is not None
