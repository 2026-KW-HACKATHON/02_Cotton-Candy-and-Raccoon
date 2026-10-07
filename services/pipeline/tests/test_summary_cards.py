"""Preserve summary facts in named cards and append persisted review guidance."""

import json
import re
from copy import deepcopy
from typing import Any

import pytest

from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.transform.gemini_prompt import load_summary_prompt
from pipeline.transform.summary_cards import SummaryCardError, build_summary_cards
from pipeline.transform.summary_schema import GeminiNoticeSummary, NoticeSummary, validate_evidence

PRIVATE_MARKER = "PRIVATE_RESIDENT_NOTICE_PAYLOAD"


@pytest.fixture
def card_prompt_example() -> GeminiNoticeSummary:
    prompt = load_summary_prompt()
    start = prompt.index("{", prompt.index("[카드 합성 예시]"))
    data, _ = json.JSONDecoder().raw_decode(prompt[start:])
    return GeminiNoticeSummary.model_validate(data)


def test_card_prompt_example_keeps_original_fields_and_literal_source_evidence(
    card_prompt_example: GeminiNoticeSummary,
) -> None:
    prompt = load_summary_prompt()
    source_line = next(line for line in prompt.splitlines() if line.startswith("가상 입력 본문: "))
    body = json.loads(source_line.partition(": ")[2])
    example = card_prompt_example
    before = example.model_dump(mode="json")
    validate_evidence(example, body_text=body, attachment_texts=[])

    assert example.action == "주민센터에 방문하여 신청서 및 개인정보제공동의서 제출"
    assert example.audience == "만 19~39세 노원구 거주자(기존 참여자 제외)"
    assert example.action_requirement == "optional"
    assert example.dates[0].text == "선착순이며 정원 충원 시 조기 마감"
    assert len(example.dates[0].text) <= 30
    assert example.dates[0].start_time == "09:00"
    assert example.dates[0].end_time == "18:00"
    assert example.dates[1].start_time == "14:00"
    assert example.dates[1].end_time is None
    assert example.summary == "주민 문화강좌 모집"
    assert example.notes == [
        "선정 후 임의 취소는 불가합니다",
        "불가피한 경우 운영기관에 사전 연락하면 취소할 수 있습니다",
        "참가비는 2만원이며 저소득 주민은 증빙서류 제출 시 면제됩니다",
    ]

    cards = build_summary_cards(example)
    assert cards.cards.action.items[0].value == example.action
    assert cards.cards.audience.items[0].value == example.audience
    assert [item.value.model_dump() for item in cards.cards.deadline.items] == [
        entry.model_dump() for entry in example.dates
    ]
    assert [item.value for item in cards.cards.notes.items] == example.notes
    assert example.model_dump(mode="json") == before


@pytest.mark.parametrize(
    ("key", "required_terms"),
    [
        ("audience", ["19~39세", "노원구", "기존 참여자", "제외"]),
        (
            "deadline",
            [
                "신청", "2026-10-06", "09:00", "2026-10-13", "18:00",
                "선착순", "정원이 차면", "조기 마감", "첫 수업", "2026-10-20", "14:00",
            ],
        ),
        ("action", ["희망하시면", "주민센터", "신청서", "개인정보제공동의서", "모두 제출"]),
        (
            "notes",
            [
                "선정 후", "임의로 취소할 수 없어요", "불가피한 경우", "운영기관", "사전 연락",
                "취소할 수", "2만원", "저소득 주민", "증빙서류 제출", "면제",
            ],
        ),
    ],
)
def test_prompt_cards_retain_documents_eligibility_times_and_exceptions_on_one_line(
    card_prompt_example: GeminiNoticeSummary, key: str, required_terms: list[str]
) -> None:
    text = getattr(card_prompt_example.card_summaries, key)
    assert text is not None
    assert "\n" not in text and "\r" not in text
    for term in required_terms:
        assert term in text
    assert getattr(build_summary_cards(card_prompt_example).cards, key).text == text


@pytest.mark.parametrize("section", ["[카드 합성 예시]", "[8. 출력 형식]"])
def test_every_sentence_in_prompt_card_examples_uses_polite_yo_style(section: str) -> None:
    prompt = load_summary_prompt()
    card_rules = prompt.split("[7-1. 네 카드에 표시할 합성 문구]", 1)[1].split(
        "[카드 합성 예시]", 1
    )[0]
    assert "지역명이 시설·사업·활동을 수식하는지" in card_rules
    assert "원문에 없는 조건을 추가해 대상을 제한하는 변경도 금지" in card_rules
    assert "각 그룹의 연령·학년·거주지·선착순·증빙" in card_rules
    assert "그 그룹의 금액·단위·전액 또는 일부 지원 여부와 함께 보존" in card_rules
    start = prompt.index("{", prompt.index(section))
    data, _ = json.JSONDecoder().raw_decode(prompt[start:])
    example = GeminiNoticeSummary.model_validate(data)

    for text in example.card_summaries.model_dump().values():
        if text is None:
            continue
        # Check each sentence, including separate schedules, rather than just the last ending.
        sentences = [part.strip() for part in re.split(r"[.!?;]", text) if part.strip()]
        assert sentences
        assert all(sentence.endswith("요") for sentence in sentences)
        assert not any(ending in text for ending in ("입니다요", "합니다요", "됩니다요", "제출요"))
        assert "\n" not in text and "\r" not in text


def _date(**changes: Any) -> dict[str, Any]:
    return {
        "kind": "application",
        "label": None,
        "text": None,
        "start_date": None,
        "end_date": None,
        "start_time": None,
        "end_time": None,
    } | changes


def _evidence(field: str, **changes: Any) -> dict[str, Any]:
    return {
        "field": field,
        "excerpt": f"{field} 원문 근거",
        "source_type": "text",
        "source_id": None,
        "page": None,
        "verification": "text_matched",
    } | changes


def _summary(**changes: Any) -> NoticeSummary:
    data = {
        "category": "application",
        "category_code": 27,
        "summary": "주민 지원사업 신청 안내",
        "publisher": "노원구청",
        "applicable_area": None,
        "audience": None,
        "audience_scope": "unknown",
        "action": None,
        "action_requirement": "unknown",
        "location": None,
        "dates": [],
        "status": "open",
        "status_detail": None,
        "notice_update": "new",
        "changed_details": None,
        "notes": [],
        "topics": [],
        "uncertainties": [],
    } | changes
    if "evidence" not in changes:
        data["evidence"] = [
            _evidence(field)
            for field in (
                "summary",
                "category_code",
                "applicable_area",
                "audience",
                "action",
                "location",
                "dates",
                "notes",
                "topics",
            )
            if data[field] not in (None, [])
        ]
    return NoticeSummary.model_validate(data)


def test_four_named_cards_preserve_claims_and_all_remaining_metadata() -> None:
    note = "참가비 2만원이며 저소득 주민은 증빙서류 제출 시 면제됩니다."
    summary = _summary(
        category="mixed",
        category_code=26,
        summary="주민 공연 신청 및 강사 모집",
        applicable_area="월계1동",
        audience="월계1동 주민 중 만 65세 이상",
        audience_scope="conditional",
        action="온라인 신청 후 증빙서류는 주민센터에 제출",
        action_requirement="optional",
        location="월계1동 주민센터",
        dates=[_date(label="서류 제출", end_date="2026-10-20")],
        notes=[note, "인터넷 신청 마감 후 현장 접수는 받지 않습니다."],
        topics=[
            {"title": "공연", "category": "event", "summary": "주민 공연 신청"},
            {"title": "강사", "category": "application", "summary": "문화 강사 모집"},
        ],
        status="cancelled",
        status_detail="행사 취소로 신청을 받지 않습니다.",
        notice_update="cancelled",
        changed_details="공연과 강사 모집 모두 취소",
        uncertainties=["대상 확인 필요"],
    )
    result = build_summary_cards(summary)

    assert set(type(result.cards).model_fields) == {"audience", "deadline", "action", "notes"}
    assert {
        result.cards.audience.key: result.cards.audience.title,
        result.cards.deadline.key: result.cards.deadline.title,
        result.cards.action.key: result.cards.action.title,
        result.cards.notes.key: result.cards.notes.title,
    } == {"audience": "대상", "deadline": "기한", "action": "할 일", "notes": "유의사항"}
    assert result.headline.value == result.headline.text == summary.summary
    assert result.cards.audience.items[0].value == summary.audience
    assert [(item.source_path, item.value) for item in result.cards.action.items] == [
        ("action", summary.action),
        ("location", summary.location),
    ]
    note_items = {item.source_path: item for item in result.cards.notes.items}
    for index, original in enumerate(summary.notes):
        assert note_items[f"notes[{index}]"].value == original
        assert note_items[f"notes[{index}]"].text == original
    for field in ("notice_update", "status", "changed_details", "status_detail"):
        assert note_items[field].value == getattr(summary, field)
    assert note_items["notice_update"].text == "취소"
    assert note_items["status"].text == "취소됨"
    metadata = result.metadata.model_dump()
    for field in (
        "category",
        "category_code",
        "publisher",
        "applicable_area",
        "audience_scope",
        "action_requirement",
        "status",
        "status_detail",
        "notice_update",
        "changed_details",
        "topics",
        "uncertainties",
    ):
        assert metadata[field] == summary.model_dump()[field]
    assert result.metadata.category_name == "문화"


@pytest.mark.parametrize(
    ("kind", "label"),
    [
        ("application", "신청 일정"),
        ("event", "행사 일정"),
        ("operation", "운영 일정"),
        ("payment", "납부 일정"),
        ("submission", "제출 일정"),
        ("effective", "시행 일정"),
        ("disruption", "중단 일정"),
        ("result", "결과 발표 일정"),
        ("other", "기타 일정"),
    ],
)
def test_date_kinds_are_kept_distinct_in_the_deadline_card(kind: str, label: str) -> None:
    summary = _summary(dates=[_date(kind=kind, label="원문 일정", end_date="2026-10-20")])
    item = build_summary_cards(summary).cards.deadline.items[0]
    assert item.label == label
    assert item.source_path == "dates[0]"
    assert item.value.model_dump() == summary.dates[0].model_dump()
    assert item.text == "원문 일정 · 종료: 2026.10.20"


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({}, None),
        ({"start_date": "2026-10-06"}, "시작: 2026.10.06"),
        ({"end_date": "2026-10-20"}, "종료: 2026.10.20"),
        ({"start_time": "09:00", "end_time": "18:00"}, "시작: 09:00 · 종료: 18:00"),
        ({"start_date": "2026-10-06", "end_time": "18:00"}, "시작: 2026.10.06 · 종료: 18:00"),
        (
            {
                "start_date": "2026-10-06",
                "end_date": "2026-10-20",
                "start_time": "09:00",
                "end_time": "18:00",
            },
            "시작: 2026.10.06 09:00 · 종료: 2026.10.20 18:00",
        ),
        ({"text": "매주 화요일, 예산 소진 시까지"}, "매주 화요일, 예산 소진 시까지"),
        ({"label": "신청 일정"}, "신청 일정"),
    ],
)
def test_date_endpoints_and_recurring_text_are_not_inferred(
    values: dict[str, Any],
    expected: str | None,
) -> None:
    summary = _summary(dates=[_date(**values)])
    result = build_summary_cards(summary)
    card = result.cards.deadline
    item = card.items[0]
    assert card.availability == "provided"
    assert card.guidance is None
    assert item.value.model_dump() == summary.dates[0].model_dump()
    assert item.text == expected
    assert item.guidance == (
        "구체적인 날짜·시간은 원문을 확인하세요." if expected is None else None
    )


def test_online_and_onsite_deadlines_keep_their_own_labels_and_endpoints() -> None:
    entries = [
        _date(label="인터넷 신청", end_date="2026-10-18", end_time="18:00"),
        _date(label="현장 신청", end_date="2026-10-20", end_time="17:00"),
    ]
    result = build_summary_cards(_summary(dates=entries))
    assert [item.value.model_dump() for item in result.cards.deadline.items] == entries
    assert [item.text for item in result.cards.deadline.items] == [
        "인터넷 신청 · 종료: 2026.10.18 18:00",
        "현장 신청 · 종료: 2026.10.20 17:00",
    ]


def test_missing_audience_action_dates_and_notes_are_guidance_only() -> None:
    result = build_summary_cards(_summary(applicable_area="월계1동"))
    for card in (
        result.cards.audience,
        result.cards.deadline,
        result.cards.action,
        result.cards.notes,
    ):
        assert card.availability == "not_provided"
        assert card.text == "원문을 확인해 주세요"
        assert card.items == []
        assert card.guidance == "원문을 확인해 주세요"
    assert result.metadata.publisher == "노원구청"
    assert result.metadata.applicable_area == "월계1동"
    assert result.metadata.audience_scope == "unknown"
    assert result.metadata.action_requirement == "unknown"


def test_gemini_card_text_is_verbatim_and_original_items_are_fully_preserved() -> None:
    summary = _summary(
        audience="월계1동 주민 중 만 65세 이상",
        audience_scope="conditional",
        action="온라인 신청 후 증빙서류 제출",
        action_requirement="optional",
        location="월계1동 주민센터",
        dates=[
            _date(label="인터넷 신청", end_date="2026-10-18", end_time="18:00"),
            _date(kind="event", label="주민 공연", start_date="2026-10-20"),
        ],
        notes=["참가비 2만원", "저소득 주민은 증빙서류 제출 시 면제됩니다."],
        notice_update="extended",
        changed_details="인터넷 신청 마감 연장",
        status_detail="인터넷 신청 접수 중",
        card_summaries={
            "audience": "월계1동에 거주하는 만 65세 이상 주민이 대상이에요.",
            "deadline": "인터넷 신청은 10월 18일 18시까지 연장됐어요. 주민 공연은 10월 20일이에요.",
            "action": (
                "희망하시면 온라인으로 신청하고 증빙서류를 제출해 주세요. "
                "안내된 장소는 월계1동 주민센터예요."
            ),
            "notes": "참가비는 2만원이에요. 저소득 주민은 증빙서류를 제출하면 면제돼요.",
        },
    )
    before = summary.model_dump(mode="json")
    legacy = NoticeSummary.model_validate(summary.model_dump(exclude={"card_summaries"}))
    original_cards = build_summary_cards(legacy)
    cards = build_summary_cards(summary)

    for key, expected in before["card_summaries"].items():
        card = getattr(cards.cards, key)
        assert card.text == expected
        assert card.model_dump(exclude={"text"}) == getattr(
            original_cards.cards, key
        ).model_dump(exclude={"text"})
    assert cards.headline.model_dump() == original_cards.headline.model_dump()
    assert cards.metadata.model_dump() == original_cards.metadata.model_dump()
    assert summary.model_dump(mode="json") == before


@pytest.mark.parametrize("key", ["audience", "deadline", "action", "notes"])
def test_ai_text_alone_provides_a_card_without_missing_information_guidance(key: str) -> None:
    text = "모델이 반환한 카드 문구를 그대로 표시해요."
    card_text = dict.fromkeys(("audience", "deadline", "action", "notes"))
    card_text[key] = text
    summary = _summary(card_summaries=card_text)
    before = summary.model_dump(mode="json")
    result = build_summary_cards(summary)

    for card_key in card_text:
        card = getattr(result.cards, card_key)
        assert card.items == []
        assert card.text == (card_text[card_key] or "원문을 확인해 주세요")
        assert card.availability == ("provided" if card_key == key else "not_provided")
        assert card.guidance == (None if card_key == key else "원문을 확인해 주세요")
    assert summary.model_dump(mode="json") == before


@pytest.mark.parametrize("missing", ["omitted", "null", "null_fields"])
def test_missing_ai_text_keeps_legacy_item_display_without_backend_synthesis(missing: str) -> None:
    legacy = _summary(
        audience="월계1동 주민",
        audience_scope="conditional",
        action="주민센터에 신청서 제출",
        action_requirement="required",
        location="월계1동 주민센터",
        dates=[_date(label="서류 제출", end_date="2026-10-20", end_time="18:00")],
        notes=["신분증 지참"],
    )
    values = legacy.model_dump(mode="json", exclude={"card_summaries"})
    if missing == "null":
        values["card_summaries"] = None
    elif missing == "null_fields":
        values["card_summaries"] = dict.fromkeys(("audience", "deadline", "action", "notes"))
    result = build_summary_cards(NoticeSummary.model_validate(values))

    assert result.model_dump() == build_summary_cards(legacy).model_dump()
    for key in ("audience", "deadline", "action", "notes"):
        card = getattr(result.cards, key)
        assert card.text is None
        assert card.availability == "provided"
        assert card.guidance is None
        assert card.items
    assert result.cards.audience.items[0].text == "월계1동 주민"
    assert result.cards.action.items[0].text == "주민센터에 신청서 제출"
    assert result.cards.deadline.items[0].text == "서류 제출 · 종료: 2026.10.20 18:00"
    assert result.cards.notes.items[0].text == "신분증 지참"


@pytest.mark.parametrize(
    ("scope", "text"),
    [
        ("general", "일반 대상"),
        ("conditional", "조건에 해당하는 대상"),
        ("specific", "특정 대상"),
        ("unknown", "확인되지 않음"),
    ],
)
def test_audience_scope_qualifies_the_original_audience(scope: str, text: str) -> None:
    result = build_summary_cards(_summary(audience="원문에 적힌 주민", audience_scope=scope))
    item = result.cards.audience.items[0]
    assert item.value == item.text == "원문에 적힌 주민"
    assert len(item.qualifiers) == 1
    qualifier = item.qualifiers[0]
    assert (qualifier.source_path, qualifier.value, qualifier.text) == (
        "audience_scope",
        scope,
        text,
    )


@pytest.mark.parametrize(
    ("requirement", "text"),
    [
        ("required", "필수"),
        ("optional", "선택"),
        ("recommended", "권장"),
        ("none", "할 일 없음"),
        ("unknown", "확인되지 않음"),
    ],
)
def test_action_requirement_is_separate_from_the_original_action(
    requirement: str,
    text: str,
) -> None:
    result = build_summary_cards(
        _summary(action="원문에서 안내한 행동", action_requirement=requirement)
    )
    item = result.cards.action.items[0]
    assert item.value == item.text == "원문에서 안내한 행동"
    qualifier = item.qualifiers[0]
    assert (qualifier.source_path, qualifier.value, qualifier.text) == (
        "action_requirement",
        requirement,
        text,
    )


def test_explicit_no_action_and_location_are_source_facts_not_missing_action_guidance() -> None:
    result = build_summary_cards(_summary(action_requirement="none", location="구청 대강당"))
    card = result.cards.action
    assert card.availability == "provided"
    assert card.guidance is None
    assert {(item.source_path, item.value, item.text) for item in card.items} == {
        ("action_requirement", "none", "할 일 없음"),
        ("location", "구청 대강당", "구청 대강당"),
    }
    assert result.cards.audience.items == []


@pytest.mark.parametrize(
    ("update", "text"),
    [("modified", "변경"), ("extended", "연장"), ("cancelled", "취소")],
)
def test_update_details_remain_visible_with_the_original_update_value(
    update: str, text: str
) -> None:
    result = build_summary_cards(
        _summary(notice_update=update, changed_details="신청 일정 변경 안내")
    )
    items = {item.source_path: item for item in result.cards.notes.items}
    assert items["notice_update"].value == update
    assert items["notice_update"].text == text
    assert items["changed_details"].value == items["changed_details"].text == "신청 일정 변경 안내"


def test_topics_are_metadata_without_invented_topic_to_date_or_action_relationships() -> None:
    summary = _summary(
        category="mixed",
        topics=[
            {"title": "문화 공연", "category": "event", "summary": "주민 공연 개최"},
            {"title": "강사 모집", "category": "application", "summary": "강사 채용 안내"},
        ],
        action="자세한 절차는 원문 확인",
        dates=[
            _date(kind="event", end_date="2026-10-20"),
            _date(kind="submission", end_date="2026-10-10"),
        ],
    )
    result = build_summary_cards(summary)
    assert result.metadata.topics == summary.topics
    assert len(result.cards.action.items) == 1
    assert result.cards.action.items[0].value == summary.action
    assert [item.source_path for item in result.cards.deadline.items] == ["dates[0]", "dates[1]"]
    for card in (
        result.cards.audience,
        result.cards.deadline,
        result.cards.action,
        result.cards.notes,
    ):
        assert all(not item.source_path.startswith("topics") for item in card.items)


def test_identical_note_and_change_text_preserve_both_original_source_paths() -> None:
    original = "일정 변경에 따라 신청서를 다시 제출하세요."
    result = build_summary_cards(_summary(notes=[original], changed_details=original))
    items = result.cards.notes.items
    assert {(item.source_path, item.value) for item in items} == {
        ("notes[0]", original),
        ("changed_details", original),
    }
    assert len(items) == 2


def test_evidence_keeps_original_index_verification_and_field_level_scope() -> None:
    refs = [
        _evidence("summary"),
        _evidence("dates", excerpt="인터넷 신청은 18일 마감"),
        _evidence("notes", excerpt="면제 조건", verification=None),
        _evidence(
            "dates",
            excerpt="현장 신청은 20일 마감",
            source_type="document",
            source_id="media_1",
            page=3,
            verification="file_reference_only",
        ),
        _evidence("audience_scope", excerpt="거주지 조건"),
        _evidence("action_requirement", excerpt="필수 제출"),
    ]
    summary = _summary(
        dates=[_date(label="인터넷 신청"), _date(label="현장 신청")],
        audience="월계1동 주민",
        audience_scope="conditional",
        action="서류 제출",
        action_requirement="required",
        notes=["면제 조건은 원문 확인"],
        evidence=refs,
    )
    result = build_summary_cards(summary)
    for item in result.cards.deadline.items:
        assert [ref.source_path for ref in item.evidence] == ["evidence[1]", "evidence[3]"]
        assert all(ref.scope == "field" for ref in item.evidence)
        assert [ref.evidence.model_dump() for ref in item.evidence] == [refs[1], refs[3]]
    assert result.cards.notes.items[0].evidence[0].evidence.verification is None
    assert result.cards.audience.items[0].qualifiers[0].evidence[0].source_path == "evidence[4]"
    assert result.cards.action.items[0].qualifiers[0].evidence[0].source_path == "evidence[5]"
    assert [ref.evidence.model_dump() for ref in result.metadata.evidence] == refs
    assert all(ref.scope == "field" for ref in result.metadata.evidence)


def test_multiple_dates_keep_independent_copies_of_the_same_field_reference() -> None:
    summary = _summary(dates=[_date(label="신청"), _date(label="행사")])
    original = summary.evidence[-1].model_dump()
    result = build_summary_cards(summary)
    first, second = result.cards.deadline.items
    assert first.evidence[0].scope == second.evidence[0].scope == "field"
    assert first.evidence[0].source_path == second.evidence[0].source_path == "evidence[2]"
    first.evidence[0].evidence.excerpt = "수정한 첫 번째 항목"
    assert second.evidence[0].evidence.model_dump() == original
    assert summary.evidence[-1].model_dump() == original
    assert result.metadata.evidence[-1].evidence.model_dump() == original


def test_cards_do_not_mutate_inputs_or_share_nested_values_between_outputs() -> None:
    summary = _summary(
        dates=[_date(text="매주 화요일")],
        notes=["비용은 원문 확인"],
        topics=[{"title": "공연", "category": "event", "summary": "공연 안내"}],
    )
    before = summary.model_dump()
    first = build_summary_cards(summary)
    second = build_summary_cards(summary)
    assert summary.model_dump() == before
    first.cards.deadline.items[0].value.text = "수정한 표시 날짜"
    first.cards.deadline.items[0].evidence[0].evidence.excerpt = "수정한 표시 근거"
    first.metadata.topics[0].title = "수정한 표시 주제"
    first.cards.notes.items.clear()
    assert summary.model_dump() == before
    assert second.cards.deadline.items[0].value.text == "매주 화요일"
    assert second.metadata.topics[0].title == "공연"
    assert len(second.cards.notes.items) == 1
    summary.dates[0].text = "수정한 입력 날짜"
    summary.evidence[0].excerpt = "수정한 입력 근거"
    summary.topics[0].title = "수정한 입력 주제"
    assert second.cards.deadline.items[0].value.text == "매주 화요일"
    assert second.headline.evidence[0].evidence.excerpt == before["evidence"][0]["excerpt"]
    assert second.metadata.topics[0].title == "공연"


@pytest.mark.parametrize("stored_status", ["pending", "failed"])
def test_unavailable_stored_status_withholds_even_malformed_supplied_result(
    stored_status: str,
) -> None:
    result = build_notice_summary_view(
        status=stored_status,
        result={"summary": PRIVATE_MARKER, "category_code": "wrong"},
        attachment_status="all_read",
    )
    assert result.status == stored_status
    assert result.content is None
    assert result.message
    assert PRIVATE_MARKER not in result.model_dump_json()


def test_review_row_without_a_result_returns_guidance_without_inventing_content() -> None:
    result = build_notice_summary_view(
        status="needs_review", result=None, attachment_status="partial"
    )
    assert result.model_dump() == {
        "status": "needs_review",
        "message": "원문 확인 요함",
        "content": None,
    }


@pytest.mark.parametrize("stored_status", ["needs_review", "summarized"])
@pytest.mark.parametrize("as_mapping", [False, True])
def test_review_guidance_preserves_four_cards_metadata_and_maximum_length_source(
    stored_status: str, as_mapping: bool
) -> None:
    summary = _summary(
        summary="가" * 40,
        applicable_area="월계1동",
        audience="월계1동 주민",
        audience_scope="conditional",
        action="신청서 제출",
        action_requirement="required",
        location="주민센터",
        dates=[
            _date(label="접수", start_date="2026-10-10", end_date="2026-10-20"),
            _date(kind="event", text="매주 화요일"),
        ],
        notes=["신분증 지참", "비용은 원문 확인"],
        topics=[{"title": "주민 지원", "category": "application", "summary": "지원 신청"}],
        notice_update="extended",
        changed_details="접수 마감 연장",
        status_detail="주민센터 운영시간 내 접수",
        uncertainties=["정확한 운영시간 확인 필요"],
        card_summaries={
            "audience": "월계1동 주민이 대상이에요.",
            "deadline": "접수는 10월 10일부터 20일까지예요. 행사는 매주 화요일이에요.",
            "action": "신청서를 반드시 제출해 주세요. 안내된 장소는 주민센터예요.",
            "notes": "접수 마감이 연장됐어요. 신분증을 지참해 주세요.",
        },
    )
    source = summary.model_dump(mode="json") if as_mapping else summary
    before = deepcopy(source) if as_mapping else summary.model_dump(mode="json")
    plain_cards = build_summary_cards(summary)
    view = build_notice_summary_view(
        status=stored_status, result=source, attachment_status="all_read"
    )
    repeated = build_notice_summary_view(
        status=stored_status, result=source, attachment_status="all_read"
    )

    assert view.status == "needs_review"
    assert view.message == "원문 확인 요함"
    assert view.content is not None
    assert repeated.content is not None
    assert view.content.headline.value == summary.summary == "가" * 40
    assert view.content.headline.text == f"{summary.summary} (원문 확인 요함)"
    assert len(view.content.headline.text) > 40
    assert view.content.cards.model_dump() == plain_cards.cards.model_dump()
    for key, text in before["card_summaries"].items():
        assert getattr(view.content.cards, key).text == text
    assert view.content.metadata.model_dump() == plain_cards.metadata.model_dump()
    assert view.content.headline.model_dump(exclude={"text"}) == plain_cards.headline.model_dump(
        exclude={"text"}
    )
    assert repeated.content.model_dump() == view.content.model_dump()
    assert (source if as_mapping else summary.model_dump(mode="json")) == before

    view.content.cards.deadline.items[0].value.end_date = "2026-11-01"
    view.content.headline.evidence[0].evidence.excerpt = "표시 근거 수정"
    view.content.metadata.topics[0].title = "표시 주제 수정"
    view.content.cards.notes.items.clear()
    assert (source if as_mapping else summary.model_dump(mode="json")) == before
    assert repeated.content.cards.model_dump() == plain_cards.cards.model_dump()
    assert repeated.content.metadata.model_dump() == plain_cards.metadata.model_dump()


def test_persisted_review_status_keeps_guidance_even_when_summary_checks_pass() -> None:
    summary = _summary()
    view = build_notice_summary_view(
        status="needs_review", result=summary, attachment_status="all_read"
    )
    assert view.status == "needs_review"
    assert view.message == "원문 확인 요함"
    assert view.content is not None
    assert view.content.headline.value == summary.summary
    assert view.content.headline.text == f"{summary.summary} (원문 확인 요함)"


@pytest.mark.parametrize("attachment_status", ["none", "all_read"])
def test_verified_stored_summary_exposes_cards_without_changing_the_source(
    attachment_status: str,
) -> None:
    summary = _summary(audience="월계1동 주민", notes=["신분증 지참"])
    data = summary.model_dump(mode="json")
    before = deepcopy(data)
    result = build_notice_summary_view(
        status="summarized",
        result=data,
        attachment_status=attachment_status,
    )
    assert result.status == "summarized"
    assert result.message is None
    assert result.content is not None
    assert result.content.headline.value == summary.summary
    assert result.content.cards.audience.items[0].value == "월계1동 주민"
    assert data == before
    result.content.headline.evidence[0].evidence.excerpt = "표시 데이터만 수정"
    assert data == before


@pytest.mark.parametrize(
    ("changes", "attachment_status"),
    [
        ({"category": "unknown"}, "all_read"),
        ({"category_code": None}, "all_read"),
        ({"uncertainties": ["원문 확인 필요"]}, "all_read"),
        ({}, "partial"),
        ({}, "unread"),
        (
            {"evidence": [_evidence("summary"), _evidence("category_code", verification=None)]},
            "all_read",
        ),
        ({"evidence": [_evidence("summary")]}, "all_read"),
        (
            {
                "audience": "월계1동 주민",
                "evidence": [_evidence("summary"), _evidence("category_code")],
            },
            "all_read",
        ),
        (
            {
                "evidence": [
                    _evidence(
                        field,
                        source_type="document",
                        source_id="media_1",
                        page=1,
                        verification="file_reference_only",
                    )
                    for field in ("summary", "category_code")
                ]
            },
            "all_read",
        ),
    ],
)
def test_stored_summarized_rows_keep_content_and_gain_guidance_under_review_rules(
    changes: dict[str, Any],
    attachment_status: str,
) -> None:
    summary = _summary(**changes)
    result = build_notice_summary_view(
        status="summarized",
        result=summary,
        attachment_status=attachment_status,
    )
    assert result.status == "needs_review"
    assert result.message == "원문 확인 요함"
    assert result.content is not None
    assert result.content.headline.value == summary.summary
    assert result.content.headline.text == f"{summary.summary} (원문 확인 요함)"
    assert result.content.cards == build_summary_cards(summary).cards


def test_text_matched_headline_keeps_file_only_deadline_with_review_guidance() -> None:
    summary = _summary(
        dates=[_date(end_date="2026-10-20")],
        evidence=[
            _evidence("summary"),
            _evidence("category_code"),
            _evidence(
                "dates",
                source_type="image",
                source_id="media_2",
                verification="file_reference_only",
            ),
        ],
    )
    result = build_notice_summary_view(
        status="summarized",
        result=summary,
        attachment_status="all_read",
    )
    assert result.status == "needs_review"
    assert result.message == "원문 확인 요함"
    assert result.content is not None
    assert result.content.cards.deadline.items[0].value == summary.dates[0]
    assert (
        result.content.cards.deadline.items[0].evidence[0].evidence.verification
        == "file_reference_only"
    )


def test_card_construction_and_public_projection_make_no_gemini_or_database_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_io(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Card projection must not call Gemini or the database")

    monkeypatch.setattr("pipeline.transform.gemini_client.generate_summary_json", forbidden_io)
    monkeypatch.setattr("pipeline.transform.summarize.generate_summary_json", forbidden_io)
    monkeypatch.setattr("pipeline.storage.summaries.save_notice_summary", forbidden_io)
    monkeypatch.setattr("psycopg.connect", forbidden_io)
    summary = _summary()
    assert build_summary_cards(summary).headline.value == summary.summary
    assert (
        build_notice_summary_view(
            status="summarized",
            result=summary,
            attachment_status="none",
        ).content
        is not None
    )


@pytest.mark.parametrize(
    ("values", "code"),
    [
        (
            {"status": PRIVATE_MARKER, "result": None, "attachment_status": "none"},
            "invalid_summary_status",
        ),
        ({"status": [], "result": None, "attachment_status": "none"}, "invalid_summary_status"),
        (
            {"status": "summarized", "result": None, "attachment_status": PRIVATE_MARKER},
            "invalid_attachment_status",
        ),
        (
            {"status": "summarized", "result": None, "attachment_status": "none"},
            "summary_result_required",
        ),
        (
            {
                "status": "summarized",
                "result": {"summary": PRIVATE_MARKER},
                "attachment_status": "none",
            },
            "invalid_summary_result",
        ),
        (
            {"status": "summarized", "result": PRIVATE_MARKER, "attachment_status": "none"},
            "invalid_summary_result",
        ),
        (
            {
                "status": "needs_review",
                "result": {"summary": PRIVATE_MARKER},
                "attachment_status": "all_read",
            },
            "invalid_summary_result",
        ),
        (
            {"status": "needs_review", "result": PRIVATE_MARKER, "attachment_status": "none"},
            "invalid_summary_result",
        ),
    ],
)
def test_public_projection_errors_expose_only_safe_codes(values: dict[str, Any], code: str) -> None:
    with pytest.raises(SummaryCardError) as caught:
        build_notice_summary_view(**values)
    assert caught.value.reason_code == code
    assert str(caught.value) == code
    assert PRIVATE_MARKER not in str(caught.value)


def test_mutated_nested_summary_is_revalidated_without_leaking_the_invalid_value() -> None:
    summary = _summary(dates=[_date(end_date="2026-10-20")])
    summary.dates[0].end_date = PRIVATE_MARKER
    for project in (
        build_summary_cards,
        lambda value: build_notice_summary_view(
            status="summarized",
            result=value,
            attachment_status="none",
        ),
        lambda value: build_notice_summary_view(
            status="needs_review",
            result=value,
            attachment_status="none",
        ),
    ):
        with pytest.raises(SummaryCardError) as caught:
            project(summary)
        assert caught.value.reason_code == "invalid_summary_result"
        assert str(caught.value) == "invalid_summary_result"
        assert PRIVATE_MARKER not in str(caught.value)
