"""Keep generated card prose separate from the source fields used for evidence."""

import json
from copy import deepcopy

import pytest

from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.transform import summarize as summarize_module
from pipeline.transform.grounding import unknown_summary
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_schema import NoticeSummary, SummaryValidationError

BODY = (
    "지원 사업 신청\n대상: 월계1동 주민\n"
    "신청 기간: 2026-10-03~2026-10-20\n"
    "월계1동 주민센터 방문 신청\n신분증 지참"
)
CARD_TEXT = {
    "audience": "월계1동 주민이 대상이에요.",
    "deadline": "신청 기간은 2026년 10월 3일부터 10월 20일까지예요.",
    "action": "월계1동 주민센터를 방문해 신청하세요.",
    "notes": "방문할 때 신분증을 준비하세요.",
}


def _notice() -> NoticeInput:
    return NoticeInput.model_validate({
        "title": "지원 사업 신청", "body_text": BODY,
        "reference_datetime": "2026-10-07T12:00:00+09:00",
    })


def _response() -> dict:
    data = unknown_summary(_notice()).model_dump(mode="json")
    data.update(
        category="application", category_code=27, summary="지원 사업 신청",
        audience="월계1동 주민", audience_scope="specific",
        action="월계1동 주민센터 방문 신청", action_requirement="optional",
        location="월계1동 주민센터", status="open", notice_update="new",
        dates=[{
            "kind": "application", "label": "신청 기간", "text": "2026-10-03~2026-10-20",
            "start_date": "2026-10-03", "end_date": "2026-10-20",
            "start_time": None, "end_time": None,
        }], notes=["신분증 지참"], uncertainties=[],
        evidence=[
            {"field": field, "excerpt": excerpt}
            for field, excerpt in (
                ("summary", "지원 사업 신청"), ("category", "지원 사업 신청"),
                ("category_code", "지원 사업 신청"), ("audience", "월계1동 주민"),
                ("action", "월계1동 주민센터 방문 신청"),
                ("location", "월계1동 주민센터 방문 신청"),
                ("dates", "신청 기간: 2026-10-03~2026-10-20"), ("notes", "신분증 지참"),
            )
        ], card_summaries=deepcopy(CARD_TEXT),
    )
    return data


def _generate(monkeypatch, responses: list[dict]) -> list[dict]:
    iterator = iter(responses)
    requests = []

    def generate(**kwargs):
        requests.append(kwargs)
        return json.dumps(next(iterator), ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    return requests


def test_one_generation_returns_new_card_prose_and_preserves_existing_evidence_fields(monkeypatch):
    raw = _response()
    snapshot = deepcopy(raw)
    requests = _generate(monkeypatch, [raw])
    summary = summarize_module.summarize_notice(_notice(), api_key="test")
    legacy = deepcopy(raw)
    del legacy["card_summaries"]
    original_fields = summarize_module._validate_summary(json.dumps(legacy), _notice())

    assert len(requests) == 1
    assert summary.card_summaries.model_dump() == CARD_TEXT
    assert summary.model_dump(exclude={"card_summaries"}) == original_fields.model_dump(
        exclude={"card_summaries"}
    )
    assert raw == snapshot
    view = build_notice_summary_view(
        status="needs_review", result=summary, attachment_status="none"
    )
    for key, value in CARD_TEXT.items():
        card = getattr(view.content.cards, key)
        assert card.text == value
        assert card.items  # Original values and references remain available for evidence viewing.
    assert view.content.cards.audience.items[0].value == summary.audience
    assert view.content.cards.audience.items[0].evidence[0].evidence.excerpt == "월계1동 주민"
    assert view.message == "원문 확인 요함"


@pytest.mark.parametrize("missing", ["absent", "null", "incomplete"])
def test_missing_new_card_contract_gets_one_retry_without_changing_valid_first_facts(
    monkeypatch, missing
):
    first = _response()
    if missing == "absent":
        del first["card_summaries"]
    elif missing == "null":
        first["card_summaries"] = None
    else:
        del first["card_summaries"]["notes"]
    retry = _response()
    retry["audience"] = "다른 지역 주민"
    requests = _generate(monkeypatch, [first, retry])

    summary = summarize_module.summarize_notice(_notice(), api_key="test")

    assert len(requests) == 2
    assert summary.audience == "월계1동 주민"
    assert summary.card_summaries.model_dump() == CARD_TEXT
    assert "card_summaries" in requests[1]["notice_text"]
    assert "원문 확인 필요" in summary.uncertainties


@pytest.mark.parametrize("missing", ["absent", "null", "incomplete"])
def test_missing_cards_on_both_responses_is_not_fabricated_as_a_success(monkeypatch, missing):
    raw = _response()
    if missing == "absent":
        del raw["card_summaries"]
    elif missing == "null":
        raw["card_summaries"] = None
    else:
        del raw["card_summaries"]["notes"]
    requests = _generate(monkeypatch, [raw, raw])
    with pytest.raises(SummaryValidationError):
        summarize_module.summarize_notice(_notice(), api_key="test")
    assert len(requests) == 2


def test_retry_restores_omitted_generated_cards_without_erasing_evidence_values():
    first = NoticeSummary.model_validate(_response())
    retry = first.model_copy(deep=True)
    retry.card_summaries.audience = None
    retry.card_summaries.notes = None
    before = first.model_dump(mode="json")
    merged = summarize_module._restore_retry_fields(first, retry, _notice())
    assert merged["card_summaries"] == CARD_TEXT
    assert merged["audience"] == first.audience
    assert merged["notes"] == first.notes
    assert merged["uncertainties"] == ["원문 확인 필요"]
    assert first.model_dump(mode="json") == before


def test_multiline_card_gets_explicit_correction_without_erasing_source_fields(monkeypatch):
    first = _response()
    first["card_summaries"]["deadline"] = "신청 시작: 2026-10-03\n마감: 2026-10-20"
    requests = _generate(monkeypatch, [first, _response()])

    summary = summarize_module.summarize_notice(_notice(), api_key="test")

    assert len(requests) == 2
    assert "줄바꿈(LF/CR)" in requests[1]["notice_text"]
    assert "세미콜론" in requests[1]["notice_text"]
    assert summary.card_summaries.deadline == CARD_TEXT["deadline"]
    assert summary.dates[0].end_date == "2026-10-20"
    assert summary.audience == "월계1동 주민"


def test_notes_retry_uses_corrected_card_text_and_keeps_first_other_cards():
    first = NoticeSummary.model_validate(_response())
    first.card_summaries.notes = "신분증을 지참하세요."
    retry = first.model_copy(deep=True)
    retry.card_summaries.notes = "신분증을 준비해 방문하세요."
    retry.card_summaries.audience = None
    before = first.model_dump(mode="json")
    merged = summarize_module._merge_note_correction(first, retry, _notice())
    assert merged.card_summaries.notes == retry.card_summaries.notes
    assert merged.card_summaries.audience == first.card_summaries.audience
    assert merged.notes == first.notes
    assert first.model_dump(mode="json") == before


@pytest.mark.parametrize("slot", ["audience", "deadline", "action", "notes"])
def test_retry_corrects_card_style_and_preserves_original_facts_and_quotes(monkeypatch, slot):
    first = _response()
    first["card_summaries"][slot] = "안내 내용을 확인해야 합니다."
    retry = _response()
    retry["audience"] = "다른 지역 주민"
    requests = _generate(monkeypatch, [first, retry])

    summary = summarize_module.summarize_notice(_notice(), api_key="test")

    assert len(requests) == 2
    assert "해요체" in requests[1]["notice_text"]
    assert summary.card_summaries.model_dump() == CARD_TEXT
    assert summary.audience == first["audience"]
    assert summary.action == first["action"]
    assert summary.dates[0].end_date == "2026-10-20"
    audience_evidence = next(item for item in summary.evidence if item.field == "audience")
    assert audience_evidence.excerpt == "월계1동 주민"


def test_repeated_wrong_card_style_fails_without_appending_a_suffix(monkeypatch):
    raw = _response()
    raw["card_summaries"]["audience"] = "월계1동 주민을 대상으로 합니다."
    before = deepcopy(raw)
    requests = _generate(monkeypatch, [raw, raw])

    with pytest.raises(SummaryValidationError):
        summarize_module.summarize_notice(_notice(), api_key="test")

    assert len(requests) == 2
    assert raw == before


def test_wrong_style_restored_by_preservation_cannot_pass_fresh_result_boundary():
    raw = _response()
    raw["card_summaries"]["notes"] = "신분증 지참 필요"
    # A stored row remains readable; fresh generation has the stronger contract.
    stored = NoticeSummary.model_validate(raw)
    with pytest.raises(SummaryValidationError):
        summarize_module._require_generated_cards(stored)
