"""Current fee checks must distinguish replaced prices from unrelated charges."""

import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from support.card_contract_repairs import _drop_references, _known_case

from pipeline.storage.summary_metadata import build_summary_metadata
from pipeline.storage.summary_record import build_summary_record, summary_requires_review
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.transform import summarize as summarize_module
from pipeline.transform.card_claims import _current_money_source, _notes_reasons
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.prepared_summary import PreparedSummaryResult


def _summarize_fee_card(quote, card):
    notice, data, _, _ = _known_case("changed-details")
    old_quote = data["changed_details"]
    notice = NoticeInput.model_validate(
        notice.model_dump() | {"body_text": notice.body_text.replace(old_quote, quote)},
    )
    data["changed_details"] = quote
    data["location"] = None
    _drop_references(data, "location")
    for item in data["evidence"]:
        if item["field"] in {"changed_details", "notice_update"}:
            item["excerpt"] = quote
    data["card_summaries"]["notes"] = card
    with patch.object(
        summarize_module, "generate_summary_json",
        return_value=json.dumps(data, ensure_ascii=False),
    ) as provider:
        summary = summarize_module.summarize_notice(notice, api_key="offline-fee-change")
    return notice, data, summary, provider.call_count


@pytest.mark.parametrize(("quote", "card"), [
    ("참가비가 3만원에서 2만원으로 변경", "참가비는 2만원으로 변경됐어요."),
    ("참가비가 2만원에서 3만원으로 변경", "참가비는 3만원으로 변경됐어요."),
    ("참가비 30,000원에서 20,000원으로 변경", "참가비는 2만원으로 인하됐어요."),
    ("참가비 3만원 -> 2만원으로 변경", "참가비는 20천원으로 조정됐어요."),
    ("참가비 3만원에서 0원으로 변경", "참가비가 무료로 변경됐어요."),
    ("참가비 0원에서 2만원으로 변경", "참가비는 2만원으로 변경됐어요."),
    ("참가비가 3만원에서 2만원으로 변경", "참가비는 3만원에서 2만원으로 변경됐어요."),
    ("다문화가정 참가비가 3만원에서 2만원으로 변경", "다문화가정 참가비는 2만원이에요."),
    ("한부모가정 참가비가 3만원에서 2만원으로 변경", "한부모가정 참가비는 2만원이에요."),
    ("검토 결과 참가비가 3만원에서 2만원으로 변경되었습니다", "참가비는 2만원이에요."),
])
def test_current_fee_card_passes_without_requiring_a_replaced_amount(quote, card):
    notice, _, summary, calls = _summarize_fee_card(quote, card)
    assert calls == 1
    assert summary.notes == []
    assert summary.changed_details == quote
    assert summary.card_summaries.notes == card
    assert not summary_requires_review(summary, attachment_status="none")
    metadata = build_summary_metadata(
        body_text=notice.body_text, total_file_count=0, read_file_count=0,
        model="offline-fee-change",
    )
    record = build_summary_record(
        PreparedSummaryResult(notice_id=17, summary=summary, warnings=()), metadata,
        deadline_on=None, generated_at=datetime(2026, 10, 7, tzinfo=UTC),
    )
    assert record.status == "summarized"
    view = build_notice_summary_view(
        status=record.status, result=record.result, attachment_status=metadata.attachment_status,
    )
    assert view.content.cards.notes.text == card
    assert view.content.cards.notes.items[-1].value == quote


@pytest.mark.parametrize("quote", [
    "참가비가 3만원에서 2만원으로 변경해야 합니다",
    "향후 참가비가 3만원에서 2만원으로 변경됩니다",
    "참가비가 3만원에서 2만원으로 변경한다고 가정합니다",
])
def test_unconfirmed_change_cannot_be_reported_as_a_current_price(quote):
    card = "참가비는 2만원이에요."
    _, _, summary, calls = _summarize_fee_card(quote, card)
    assert calls <= 2
    assert summary_requires_review(summary, attachment_status="none")
    assert summary.changed_details == quote
    assert summary.card_summaries.notes == card


@pytest.mark.parametrize(("source", "card", "reason"), [
    ("참가비가 3만원에서 2만원으로 변경", "참가비는 3만원이에요.",
     "card_notes_amount_omitted"),
    ("참가비가 3만원에서 2만원으로 변경", "참가비는 5만원이에요.",
     "card_notes_amount_added"),
    ("참가비가 3만원에서 2만원으로 변경", "참가비는 무료예요.", "card_notes_false_free"),
    ("참가비가 0원에서 2만원으로 변경", "참가비는 무료예요.", "card_notes_false_free"),
    ("참가비가 3만원에서 2만원으로 변경", "참가비 변경을 확인해 주세요.",
     "card_notes_amount_omitted"),
    ("참가비가 3만원에서 2만원으로 변경; 회비는 3만원", "참가비는 2만원이에요.",
     "card_notes_amount_omitted"),
    ("참가비가 3만원에서 2만원까지", "참가비는 2만원이에요.", "card_notes_amount_omitted"),
    ("참가비가 3만원에서 2만원으로 변경되지 않습니다", "참가비는 2만원이에요.",
     "card_notes_amount_omitted"),
    ("참가비가 3만원에서 2만원으로 변경할 예정", "참가비는 2만원이에요.",
     "card_notes_amount_omitted"),
])
def test_current_fee_check_still_detects_changed_or_missing_active_amounts(source, card, reason):
    assert reason in _notes_reasons(card, source)


@pytest.mark.parametrize("source", [
    "참가비가 3만원에서 2만원까지예요",
    "참가비가 3만원에서 2만원으로 변경되지 않습니다",
    "참가비가 3만원에서 2만원으로 변경하지 않습니다",
    "참가비가 3만원에서 2만원으로 변경할 예정입니다",
    "참가비가 3만원에서 2만원으로 변경될 경우 안내합니다",
    "참가비가 3만원에서 2만원으로 변경하면 알려주세요",
    "참가비가 3만원에서 2만원으로 변경 예정입니다",
    "참가비가 3만원에서 2만원으로 변경 계획입니다",
    "참가비가 3만원에서 2만원으로 변경 검토 중입니다",
    "참가비가 3만원에서 2만원으로 변경되는 경우 안내합니다",
    "참가비가 3만원에서 2만원으로 변경을 검토합니다",
    "참가비가 3만원에서 2만원으로 변경 (예정)",
    "참가비가 3만원에서 2만원으로 변경이 가능합니다",
    "참가비가 3만원에서 2만원으로 변경된 경우 안내합니다",
    "참가비가 3만원에서 2만원으로 변경 의무가 있습니다",
    "참가비가 3만원에서 2만원으로 변경한다고 가정합니다",
    "참가비가 3만원에서 2만원으로 변경해야 합니다",
    "참가비가 3만원에서 2만원으로 변경한다는 계획입니다",
    "참가비가 3만원에서 2만원으로 변경될지는 아직 미정입니다",
    "향후 참가비가 3만원에서 2만원으로 변경됩니다",
    "내년부터 참가비가 3만원에서 2만원으로 변경됩니다",
    "다음 달부터 참가비가 3만원에서 2만원으로 변경됩니다",
    "가정하면 참가비가 3만원에서 2만원으로 변경됩니다",
    "인하 예정인 참가비가 3만원에서 2만원으로 변경",
    "변경계획: 참가비가 3만원에서 2만원으로 변경",
])
def test_ambiguous_range_negated_or_future_changes_do_not_guess_an_active_price(source):
    assert _current_money_source(source) == source
    assert "card_notes_amount_omitted" in _notes_reasons("참가비는 2만원이에요.", source)


def test_unspecified_money_relationship_does_not_guess_a_current_fee():
    source = "3만원에서 2만원으로 변경"
    assert _current_money_source(source) == source


def test_replaced_occurrence_does_not_remove_the_same_price_from_another_charge():
    source = "참가비가 3만원에서 2만원으로 변경; 회비는 3만원"
    assert _notes_reasons("참가비는 2만원이고 회비는 3만원이에요.", source) == []
    assert "card_notes_amount_omitted" in _notes_reasons("참가비는 2만원이에요.", source)


def test_sources_and_card_prose_are_unchanged_by_comparison():
    source = "참가비가 3만원에서 2만원으로 변경"
    card = "참가비는 2만원으로 변경됐어요."
    assert _notes_reasons(card, source) == []
    assert source == "참가비가 3만원에서 2만원으로 변경"
    assert card == "참가비는 2만원으로 변경됐어요."


@pytest.mark.parametrize("verb", ["변경", "정정", "조정", "인하", "인상"])
def test_fee_relationship_comparison_accepts_explicit_change_verbs(verb):
    source = f"참가비가 3만원에서 2만원으로 {verb}"
    assert _notes_reasons("참가비는 2만원이에요.", source) == []


@pytest.mark.parametrize("ending", [
    "됐어요", "되었습니다", "되었음", "했습니다", "하였습니다", "됩니다", "합니다",
    "완료", "확정됨",
])
def test_current_fee_changes_accept_affirmative_statements(ending):
    source = f"참가비가 3만원에서 2만원으로 변경{ending}."
    assert _notes_reasons("참가비는 2만원이에요.", source) == []


@pytest.mark.parametrize("context", ["운영계획에 따라", "검토 결과"])
def test_past_planning_context_does_not_make_a_confirmed_fee_change_hypothetical(context):
    source = f"{context} 참가비가 3만원에서 2만원으로 변경되었습니다"
    assert _notes_reasons("참가비는 2만원이에요.", source) == []
