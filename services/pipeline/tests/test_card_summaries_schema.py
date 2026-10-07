"""Keep the four Gemini card slots strict while reading historical summaries."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from pipeline.transform.summary_schema import (
    CardSummaries,
    Evidence,
    GeminiCardSummaries,
    GeminiNoticeSummary,
    NoticeSummary,
    card_text_uses_yoche,
)

CARD_KEYS = ("audience", "deadline", "action", "notes")
ORIGINAL_FIELDS = {
    "category",
    "category_code",
    "summary",
    "publisher",
    "applicable_area",
    "audience",
    "audience_scope",
    "action",
    "action_requirement",
    "location",
    "dates",
    "status",
    "status_detail",
    "notice_update",
    "changed_details",
    "notes",
    "topics",
    "uncertainties",
    "evidence",
}


def _legacy_output() -> dict[str, Any]:
    return {
        "category": "event",
        "category_code": 26,
        "summary": "구민 문화행사 안내",
        "publisher": "문화과",
        "applicable_area": "노원구",
        "audience": "노원구민",
        "audience_scope": "general",
        "action": "행사장 방문",
        "action_requirement": "optional",
        "location": "구청 강당",
        "dates": [{
            "kind": "event",
            "label": "행사일",
            "text": "10월 10일",
            "start_date": "2026-10-10",
            "end_date": "2026-10-10",
            "start_time": "14:00",
            "end_time": "16:00",
        }],
        "status": "upcoming",
        "status_detail": "행사 예정",
        "notice_update": "modified",
        "changed_details": "장소 변경",
        "notes": ["대중교통 이용 권장"],
        "topics": [{"title": "문화행사", "category": "event", "summary": "구민 문화행사"}],
        "uncertainties": ["원문 확인 필요"],
        "evidence": [{
            "field": "summary",
            "excerpt": "구민 문화행사 안내",
            "source_type": "text",
            "source_id": None,
            "page": None,
            "verification": "text_matched",
        }],
    }


def _empty_cards() -> dict[str, None]:
    return dict.fromkeys(CARD_KEYS)


def _known_cards() -> dict[str, str]:
    return {
        "audience": "노원구민이 대상이에요.",
        "deadline": "행사는 10월 10일 14:00부터 16:00까지예요.",
        "action": "구청 강당의 행사장을 방문해 주세요.",
        "notes": "대중교통을 이용해 주세요.",
    }


@pytest.mark.parametrize("include_null", [False, True])
def test_historical_summary_accepts_absent_or_null_cards(include_null: bool) -> None:
    data = _legacy_output()
    if include_null:
        data["card_summaries"] = None
    summary = NoticeSummary.model_validate_json(json.dumps(data))
    assert summary.card_summaries is None
    assert summary.model_dump(exclude={"card_summaries"}) == _legacy_output()


@pytest.mark.parametrize("include_null", [False, True])
def test_fresh_summary_requires_a_card_object(include_null: bool) -> None:
    data = _legacy_output()
    if include_null:
        data["card_summaries"] = None
    with pytest.raises(ValidationError) as failure:
        GeminiNoticeSummary.model_validate_json(json.dumps(data))
    assert failure.value.errors()[0]["loc"] == ("card_summaries",)


@pytest.mark.parametrize("missing", CARD_KEYS)
@pytest.mark.parametrize("model", [CardSummaries, GeminiCardSummaries])
def test_all_four_nullable_card_keys_are_required(
    missing: str, model: type[CardSummaries]
) -> None:
    cards = _empty_cards()
    del cards[missing]
    with pytest.raises(ValidationError) as failure:
        model.model_validate_json(json.dumps(cards))
    assert failure.value.errors()[0]["loc"] == (missing,)
    assert failure.value.errors()[0]["type"] == "missing"


def test_legacy_null_cards_are_valid_without_changing_original_fields() -> None:
    data = _legacy_output() | {"card_summaries": _empty_cards()}
    summary = NoticeSummary.model_validate_json(json.dumps(data))
    assert summary.card_summaries.model_dump() == _empty_cards()
    assert summary.model_dump(exclude={"card_summaries"}) == _legacy_output()
    assert isinstance(summary, NoticeSummary)


@pytest.mark.parametrize("model", [NoticeSummary, GeminiNoticeSummary])
def test_summary_rejects_unknown_fields(model: type[NoticeSummary]) -> None:
    data = _legacy_output() | {"card_summaries": _empty_cards(), "unexpected": "claim"}
    with pytest.raises(ValidationError) as failure:
        model.model_validate_json(json.dumps(data))
    assert failure.value.errors()[0]["loc"] == ("unexpected",)
    assert failure.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize("model", [CardSummaries, GeminiCardSummaries])
def test_card_object_rejects_unknown_fields(model: type[CardSummaries]) -> None:
    cards = _empty_cards() | {"unexpected": "claim"}
    with pytest.raises(ValidationError) as failure:
        model.model_validate_json(json.dumps(cards))
    assert failure.value.errors()[0]["loc"] == ("unexpected",)
    assert failure.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize("field", CARD_KEYS)
@pytest.mark.parametrize("value", [0, 1, 1.5, True, False, [], {}, ["claim"]])
@pytest.mark.parametrize("model", [CardSummaries, GeminiCardSummaries])
def test_card_text_rejects_non_string_values(
    field: str, value: Any, model: type[CardSummaries]
) -> None:
    cards: dict[str, Any] = _empty_cards() | {field: value}
    with pytest.raises(ValidationError) as failure:
        model.model_validate_json(json.dumps(cards))
    assert failure.value.errors()[0]["loc"] == (field,)
    assert failure.value.errors()[0]["type"] == "string_type"


@pytest.mark.parametrize("field", CARD_KEYS)
@pytest.mark.parametrize("value", ["", " ", "\t", "앞\n뒤", "앞\r뒤", "앞\r\n뒤"])
@pytest.mark.parametrize("model", [CardSummaries, GeminiCardSummaries])
def test_card_text_rejects_blank_or_multiline_values(
    field: str, value: str, model: type[CardSummaries]
) -> None:
    cards = _empty_cards() | {field: value}
    with pytest.raises(ValidationError) as failure:
        model.model_validate_json(json.dumps(cards))
    assert failure.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize("field", CARD_KEYS)
@pytest.mark.parametrize("value", ["0", "🚦 주민 안내 — 令和 / مرحبا", "가" * 4096])
def test_card_text_preserves_unicode_without_an_arbitrary_length_limit(
    field: str, value: str
) -> None:
    cards = _empty_cards() | {field: value}
    assert CardSummaries.model_validate_json(json.dumps(cards)).model_dump() == cards


@pytest.mark.parametrize("field", CARD_KEYS)
@pytest.mark.parametrize("punctuation", ["", ".", "!", "?", ".!?", "?! "])
def test_fresh_cards_accept_yoche_endings_and_preserve_punctuation(
    field: str, punctuation: str
) -> None:
    text = {
        "audience": "노원구에 거주하는 초등학생이 대상이에요",
        "deadline": "10월 20일까지 신청할 수 있어요",
        "action": "신청서와 신분증을 제출해 주세요",
        "notes": "이용료는 무료예요",
    }[field] + punctuation
    cards = _known_cards() | {field: text}
    summary = GeminiNoticeSummary.model_validate(_legacy_output() | {"card_summaries": cards})
    assert summary.card_summaries.model_dump() == cards
    assert card_text_uses_yoche(text) is True


@pytest.mark.parametrize("field", CARD_KEYS)
@pytest.mark.parametrize("text", [
    "서류를 지참하세요.",
    "지원받는다는 안내가 있어요.",
    "신청한다면 준비물을 확인해 주세요.",
    '원문에는 "무료입니다."라고 쓰여 있어요.',
    '원문에는 "무료입니다; 예약은 필수입니다"라고 쓰여 있어요.',
    "원문에는 ‘무료이다；예약은 필수이다’라고 쓰여 있어요.",
    "원문에는 ‘무료이다.’라고 쓰여 있어요.",
    "2026.10.20. 20:00까지 신청할 수 있어요.",
    "원어민 교실 (English / مرحبا / 🚦)을 이용할 수 있어요.",
    "가" * 4096 + "라고 안내되어 있어요.",
])
def test_fresh_yoche_preserves_quotes_embedded_clauses_unicode_and_long_text(
    field: str, text: str
) -> None:
    cards = _empty_cards() | {field: text}
    assert GeminiCardSummaries.model_validate(cards).model_dump() == cards


@pytest.mark.parametrize("field", CARD_KEYS)
@pytest.mark.parametrize("text", [
    "노원구민 대상",
    "신청 가능합니다.",
    "신청할 수 있죠.",
    "신분증 필요.",
    "요",
    "신청해야 합니다요.",
    "성인이 대상이다. 신청할 수 있어요.",
    "접수는 시작한다! 서류를 준비해 주세요.",
    "지원금은 지급된다. 유의해 주세요.",
    "지원이 없다. 다시 확인해 주세요.",
    "서류를 제출해야 합니다. 준비해 주세요.",
    "서류를 제출해야 합니다.준비해 주세요.",
    "신청은 10월 20일까지입니다; 수업은 10월 27일부터예요.",
    "신청은 10월 20일까지입니다；수업은 10월 27일부터예요.",
])
def test_fresh_cards_reject_plain_dache_mixed_sentences_or_non_yoche_endings(
    field: str, text: str
) -> None:
    cards = _empty_cards() | {field: text}
    with pytest.raises(ValidationError) as failure:
        GeminiNoticeSummary.model_validate(_legacy_output() | {"card_summaries": cards})
    assert failure.value.errors()[0]["loc"] == ("card_summaries", field)
    assert card_text_uses_yoche(text) is False
    # The persisted v4 contract still reads older card wording exactly as saved.
    legacy = NoticeSummary.model_validate(_legacy_output() | {"card_summaries": cards})
    assert legacy.card_summaries is not None
    assert legacy.card_summaries.model_dump() == cards


def test_fresh_yoche_does_not_change_original_dache_fields_or_evidence_quotes() -> None:
    data = _legacy_output()
    data["notes"] = ["신분증을 제출해야 합니다."]
    data["evidence"].append(data["evidence"][0] | {
        "field": "notes", "excerpt": "신분증을 제출해야 합니다."
    })
    summary = GeminiNoticeSummary.model_validate(data | {
        "card_summaries": _known_cards() | {"notes": "신분증을 제출해 주세요."}
    })
    assert summary.model_dump(exclude={"card_summaries"}) == data


def test_new_schema_preserves_all_original_fields_and_requires_exactly_four_card_keys() -> None:
    legacy = NoticeSummary.model_json_schema()
    fresh = GeminiNoticeSummary.model_json_schema()
    assert set(NoticeSummary.model_fields) == ORIGINAL_FIELDS | {"card_summaries"}
    assert set(legacy["required"]) == ORIGINAL_FIELDS
    assert set(fresh["required"]) == ORIGINAL_FIELDS | {"card_summaries"}
    assert legacy["additionalProperties"] is False
    assert fresh["additionalProperties"] is False
    assert fresh["properties"]["card_summaries"] == {"$ref": "#/$defs/GeminiCardSummaries"}
    for field in ORIGINAL_FIELDS:
        assert fresh["properties"][field] == legacy["properties"][field]
    cards = fresh["$defs"]["GeminiCardSummaries"]
    assert set(cards["properties"]) == set(CARD_KEYS)
    assert set(cards["required"]) == set(CARD_KEYS)
    assert cards["additionalProperties"] is False
    for field in CARD_KEYS:
        options = cards["properties"][field]["anyOf"]
        assert {option["type"] for option in options} == {"string", "null"}
        text = next(option for option in options if option["type"] == "string")
        assert text["minLength"] == 1
        assert "maxLength" not in text


def test_card_prose_does_not_add_an_evidence_field() -> None:
    fields = Evidence.model_json_schema()["properties"]["field"]["enum"]
    assert set(fields) == ORIGINAL_FIELDS - {"evidence"}
    with pytest.raises(ValidationError):
        Evidence.model_validate({"field": "card_summaries", "excerpt": "claim"})
