"""Regressions for missing card prose, bounded correction, and truthful empty slots."""

import json
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError
from support.card_contract_repairs import _drop_references, _known_case
from support.gemini_card_generation import CARD_TEXT, _notice, _response

from pipeline.storage.summary_record import summary_requires_review
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.transform import summarize as summarize_module
from pipeline.transform.card_claims import card_claim_review_reasons
from pipeline.transform.gemini_client import GeminiRequestError
from pipeline.transform.grounding import REVIEW_NOTE, unknown_summary
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_cards import MISSING_CARD_GUIDANCE
from pipeline.transform.summary_schema import (
    GeminiNoticeSummary,
    NoticeSummary,
    SummaryValidationError,
)


def _provider(
    monkeypatch: pytest.MonkeyPatch, *responses: dict[str, Any] | str | Exception,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def generate(**kwargs: Any) -> str:
        index = len(calls)
        calls.append(deepcopy(kwargs))
        assert index < len(responses), "corrections must share the two-call budget"
        response = responses[index]
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    return calls


KNOWN_CASES = [
    "location", "explicit-none", "changed-details", "status-detail", "cancelled", "update-only",
]


def _assert_base_facts_retained(summary: NoticeSummary, first: dict[str, Any]) -> None:
    baseline = NoticeSummary.model_validate(first)
    excluded = {"card_summaries", "uncertainties", "evidence"}
    assert summary.model_dump(exclude=excluded) == baseline.model_dump(exclude=excluded)
    assert [item.model_dump(exclude={"verification"}) for item in summary.evidence] == [
        item.model_dump(exclude={"verification"}) for item in baseline.evidence
    ]


@pytest.mark.parametrize("case", KNOWN_CASES)
def test_known_nonprimary_card_sources_require_fresh_prose_but_legacy_remains_readable(case):
    _, first, slot, _ = _known_case(case)
    legacy = NoticeSummary.model_validate(first)
    assert getattr(legacy.card_summaries, slot) is None
    with pytest.raises(ValidationError):
        GeminiNoticeSummary.model_validate(first)
    view = build_notice_summary_view(status="needs_review", result=legacy, attachment_status="none")
    card = getattr(view.content.cards, slot)
    assert card.items
    assert card.availability == "provided"
    assert card.guidance is None
    assert card.text is None
    assert view.content.headline.text == first["summary"]


@pytest.mark.parametrize("case", KNOWN_CASES)
def test_one_shared_correction_repairs_known_prose_without_replacing_first_facts(monkeypatch, case):
    notice, first, slot, prose = _known_case(case)
    corrected = deepcopy(first)
    corrected["card_summaries"][slot] = prose
    corrected["audience"] = "다른 지역 주민"
    corrected["card_summaries"]["audience"] = "다른 지역 주민이 대상이에요."
    first_snapshot = deepcopy(first)
    calls = _provider(monkeypatch, first, corrected)

    summary = summarize_module.summarize_notice(notice, api_key="offline-contract-test")

    assert len(calls) == 2
    assert "card_summaries" in calls[1]["notice_text"]
    assert summary.card_summaries is not None
    assert getattr(summary.card_summaries, slot) == prose
    assert summary.card_summaries.audience == CARD_TEXT["audience"]
    _assert_base_facts_retained(summary, first)
    assert first == first_snapshot
    for key, text in first["card_summaries"].items():
        if key != slot:
            assert getattr(summary.card_summaries, key) == text


@pytest.mark.parametrize("case", ["location", "changed-details", "status-detail"])
@pytest.mark.parametrize("failure", ["malformed", "missing-required", "timeout"])
def test_failed_known_card_correction_preserves_first_schema_valid_nullable_response(
    monkeypatch, case, failure,
):
    notice, first, slot, _ = _known_case(case)
    NoticeSummary.model_validate(first)
    if failure == "malformed":
        second: dict[str, Any] | str | Exception = "{broken-json"
    elif failure == "missing-required":
        second = deepcopy(first)
        del second["category"]
    else:
        second = GeminiRequestError("mock correction timeout", reason_code="api_timeout")
    calls = _provider(monkeypatch, first, second)

    summary = summarize_module.summarize_notice(notice, api_key="offline-contract-test")

    assert len(calls) == 2
    _assert_base_facts_retained(summary, first)
    assert summary.card_summaries.model_dump() == first["card_summaries"]
    assert getattr(summary.card_summaries, slot) is None
    assert REVIEW_NOTE in summary.uncertainties
    assert summary._correction_failure_code == (
        "api_timeout" if failure == "timeout" else "response_validation_failed"
    )
    assert "correction_failure_code" not in summary.model_dump_json()
    assert summary_requires_review(summary, attachment_status="none")
    view = build_notice_summary_view(
        status="needs_review", result=summary, attachment_status="none",
    )
    assert getattr(view.content.cards, slot).items
    assert getattr(view.content.cards, slot).guidance is None
    assert getattr(view.content.cards, slot).text is None
    assert view.content.headline.text == first["summary"]


@pytest.mark.parametrize("case", ["location", "status-detail"])
def test_repeated_nullable_missing_prose_retains_known_items_for_review_without_guessing(
    monkeypatch, case,
):
    notice, first, slot, _ = _known_case(case)
    calls = _provider(monkeypatch, first, first)

    summary = summarize_module.summarize_notice(notice, api_key="offline-contract-test")

    assert len(calls) == 2
    _assert_base_facts_retained(summary, first)
    assert getattr(summary.card_summaries, slot) is None
    assert REVIEW_NOTE in summary.uncertainties
    assert summary._correction_failure_code == "response_validation_failed"


@pytest.mark.parametrize(("slot", "prose"), [
    ("audience", "만 65세 이상만 신청할 수 있어요."),
    ("action", "50만원을 송금해 주세요."),
    ("deadline", "12월 31일까지 신청해 주세요."),
    ("notes", "참가비는 50만원이에요."),
])
def test_card_only_claim_without_source_field_or_evidence_is_retained_for_review(
    monkeypatch, slot, prose,
):
    first = _response()
    if slot == "audience":
        first.update(audience=None, audience_scope="unknown")
        fields = ["audience", "audience_scope"]
    elif slot == "action":
        first.update(action=None, action_requirement="unknown", location=None)
        fields = ["action", "action_requirement", "location"]
    elif slot == "deadline":
        first["dates"] = []
        fields = ["dates"]
    else:
        first["notes"] = []
        fields = ["notes"]
    _drop_references(first, *fields)
    first["card_summaries"][slot] = prose
    calls = _provider(monkeypatch, first, first)

    summary = summarize_module.summarize_notice(_notice(), api_key="offline-contract-test")

    assert 1 <= len(calls) <= 2
    _assert_base_facts_retained(summary, first)
    assert getattr(summary.card_summaries, slot) == prose
    assert card_claim_review_reasons(summary, _notice())
    assert summary_requires_review(summary, attachment_status="none")
    view = build_notice_summary_view(status="summarized", result=summary, attachment_status="none")
    assert view.status == "needs_review"
    assert getattr(view.content.cards, slot).text == prose


@pytest.mark.parametrize("case", ["location", "explicit-none"])
def test_location_or_no_application_cannot_validate_an_unrelated_payment_claim(monkeypatch, case):
    notice, first, slot, prose = _known_case(case)
    first["card_summaries"][slot] = prose + " 50만원을 송금해 주세요."
    GeminiNoticeSummary.model_validate(first)
    calls = _provider(monkeypatch, first, first)

    summary = summarize_module.summarize_notice(notice, api_key="offline-contract-test")

    assert 1 <= len(calls) <= 2
    _assert_base_facts_retained(summary, first)
    assert summary.card_summaries.action == first["card_summaries"]["action"]
    reasons = card_claim_review_reasons(summary, notice)
    assert reasons
    assert any("amount" in reason for reason in reasons)
    assert summary_requires_review(summary, attachment_status="none")
    view = build_notice_summary_view(status="summarized", result=summary, attachment_status="none")
    assert view.status == "needs_review"
    assert view.content.cards.action.text == first["card_summaries"]["action"]


SECONDARY_NOTE_CASES = ["status-detail", "changed-details", "cancelled", "update-only"]


def _secondary_note_case(
    case: str, *, actual_price: bool,
) -> tuple[NoticeInput, dict[str, Any]]:
    notice, first, slot, prose = _known_case(case)
    # The fixture's action quote is not a venue-specific location reference.
    # Remove that unrelated uncertainty so it cannot hide a currency guard bypass.
    first["location"] = None
    _drop_references(first, "location")
    first["card_summaries"][slot] = prose + " 참가비는 50만원이에요."
    if actual_price:
        if case == "status-detail":
            field, quote = "status_detail", "주차권 미발급, 참가비 50만원"
            first[field] = quote
        elif case == "changed-details":
            field, quote = "changed_details", "행사 장소 변경, 참가비 50만원"
            first[field] = quote
        elif case == "cancelled":
            field, quote = "status", "참가비 50만원인 행사가 취소되었습니다."
        else:
            field, quote = "notice_update", "참가비가 50만원으로 변경되었습니다."
        _drop_references(first, field)
        first["evidence"].append({"field": field, "excerpt": quote})
        notice = NoticeInput.model_validate(
            notice.model_dump() | {"body_text": notice.body_text + "\n" + quote},
        )
    return notice, first


@pytest.mark.parametrize("case", SECONDARY_NOTE_CASES)
def test_secondary_note_facts_cannot_validate_an_unrelated_fee_without_notes(monkeypatch, case):
    notice, first = _secondary_note_case(case, actual_price=False)
    calls = _provider(monkeypatch, first, first)

    summary = summarize_module.summarize_notice(notice, api_key="offline-contract-test")

    assert len(calls) == 1
    assert summary.notes == []
    _assert_base_facts_retained(summary, first)
    assert summary.card_summaries.notes == first["card_summaries"]["notes"]
    assert "card_notes_amount_added" in card_claim_review_reasons(summary, notice)
    assert summary_requires_review(summary, attachment_status="none")
    view = build_notice_summary_view(status="summarized", result=summary, attachment_status="none")
    assert view.status == "needs_review"
    assert view.content.cards.notes.text == first["card_summaries"]["notes"]


@pytest.mark.parametrize("case", SECONDARY_NOTE_CASES)
def test_real_fee_in_secondary_note_evidence_is_not_rejected_when_notes_are_empty(
    monkeypatch, case,
):
    notice, first = _secondary_note_case(case, actual_price=True)
    calls = _provider(monkeypatch, first, first)

    summary = summarize_module.summarize_notice(notice, api_key="offline-contract-test")

    assert len(calls) == 1
    assert summary.notes == []
    _assert_base_facts_retained(summary, first)
    assert summary.card_summaries.notes == first["card_summaries"]["notes"]
    reasons = card_claim_review_reasons(summary, notice)
    assert not any("amount" in reason or "free" in reason for reason in reasons)
    if case != "update-only":
        assert reasons == ()
        assert not summary_requires_review(summary, attachment_status="none")


def test_all_absent_source_facts_and_cards_remain_null_without_inventing_details(monkeypatch):
    notice = NoticeInput.model_validate({
        "title": "공지 확인 안내", "body_text": "공지 확인 안내",
        "reference_datetime": "2026-10-07T12:00:00+09:00",
    })
    first = unknown_summary(notice).model_dump(mode="json")
    first.update(category="news", category_code=30, summary=notice.title, uncertainties=[])
    first["evidence"] = [
        {"field": field, "excerpt": notice.title}
        for field in ("summary", "category", "category_code")
    ]
    first["card_summaries"] = dict.fromkeys(CARD_TEXT)
    calls = _provider(monkeypatch, first, first)

    summary = summarize_module.summarize_notice(notice, api_key="offline-contract-test")

    assert 1 <= len(calls) <= 2
    assert summary.audience is None
    assert summary.action is None
    assert summary.location is None
    assert summary.dates == []
    assert summary.notes == []
    assert summary.card_summaries.model_dump() == dict.fromkeys(CARD_TEXT)
    assert card_claim_review_reasons(summary, notice) == ()
    view = build_notice_summary_view(
        status="needs_review", result=summary, attachment_status="none",
    )
    for card in view.content.cards.model_dump().values():
        assert card["items"] == []
        assert card["availability"] == "not_provided"
        assert card["guidance"] == MISSING_CARD_GUIDANCE


@pytest.mark.parametrize("malformed", ["{broken-json", "{}"])
def test_no_schema_valid_response_never_fabricates_known_cards_or_a_success(monkeypatch, malformed):
    calls = _provider(monkeypatch, malformed, malformed)
    with pytest.raises(SummaryValidationError):
        summarize_module.summarize_notice(_notice(), api_key="offline-contract-test")
    assert len(calls) == 2
