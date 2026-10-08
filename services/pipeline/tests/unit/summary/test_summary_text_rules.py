"""Resident-facing text limits must preserve usable claims and fail safely."""

import json

import pytest
from pydantic import ValidationError

from pipeline.transform import summarize as summarize_module
from pipeline.transform.grounding import ground_summary, unknown_summary
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_schema import NoticeSummary

TEXT_LIMITS = [
    ("summary", 40),
    ("audience", 40),
    ("action", 60),
    ("notes", 60),
    ("publisher", 30),
    ("applicable_area", 30),
    ("location", 30),
    ("status_detail", 30),
    ("changed_details", 30),
    ("dates.label", 30),
    ("dates.text", 30),
    ("topics.title", 20),
    ("topics.summary", 40),
    ("uncertainties", 15),
]


def make_notice(body: str = "공지\n온라인 신청", **metadata: object) -> NoticeInput:
    return NoticeInput.model_validate(
        {
            "title": "공지",
            "body_text": body,
            "reference_datetime": "2026-10-03T12:00:00+09:00",
            **metadata,
        }
    )


def make_data(notice: NoticeInput) -> dict:
    return unknown_summary(notice).model_dump()


def set_text(data: dict, field: str, value: str) -> None:
    if field.startswith("dates."):
        data["dates"] = [
            {
                "kind": "event",
                "label": None,
                "text": None,
                "start_date": None,
                "end_date": None,
                "start_time": None,
                "end_time": None,
                field.split(".")[1]: value,
            }
        ]
    elif field.startswith("topics."):
        data["topics"] = [
            {
                "title": "공지",
                "category": "news",
                "summary": "공지",
                field.split(".")[1]: value,
            }
        ]
    elif field in ("notes", "uncertainties"):
        data[field] = [value]
    else:
        data[field] = value


@pytest.mark.parametrize(("field", "limit"), TEXT_LIMITS)
def test_each_text_field_accepts_its_limit_and_rejects_one_more_character(
    field: str, limit: int
) -> None:
    data = make_data(make_notice())
    set_text(data, field, "가" * limit)
    NoticeSummary.model_validate(data)

    set_text(data, field, "가" * (limit + 1))
    with pytest.raises(ValidationError) as failure:
        NoticeSummary.model_validate(data)
    errors = failure.value.errors()
    assert len(errors) == 1
    assert errors[0]["type"] in ("string_too_long", "too_long")
    assert errors[0]["ctx"]["max_length"] == limit


def test_length_counts_korean_characters_and_spaces_without_byte_counting() -> None:
    data = make_data(make_notice())
    data["summary"] = "가나다라 " * 8
    assert len(data["summary"]) == 40
    assert len(data["summary"].encode("utf-8")) > 40
    assert NoticeSummary.model_validate(data).summary == data["summary"]

    data["summary"] += " "
    with pytest.raises(ValidationError, match="at most 40 characters"):
        NoticeSummary.model_validate(data)


def test_notes_allow_five_items_but_not_six() -> None:
    data = make_data(make_notice())
    data["notes"] = [f"유의사항 {index}" for index in range(5)]
    assert len(NoticeSummary.model_validate(data).notes) == 5

    data["notes"].append("유의사항 5")
    with pytest.raises(ValidationError) as failure:
        NoticeSummary.model_validate(data)
    error = failure.value.errors()[0]
    assert error["loc"] == ("notes",)
    assert error["type"] == "too_long"
    assert error["ctx"]["max_length"] == 5


def test_gemini_response_schema_exposes_field_specific_limits_and_note_count() -> None:
    schema = NoticeSummary.model_json_schema()
    for field, limit in TEXT_LIMITS:
        if "." in field:
            parent, nested = field.split(".")
            definition = "DateEntry" if parent == "dates" else "Topic"
            property_schema = schema["$defs"][definition]["properties"][nested]
        else:
            property_schema = schema["properties"][field]
        if field in ("notes", "uncertainties"):
            property_schema = property_schema["items"]
        if "anyOf" in property_schema:
            property_schema = next(
                option for option in property_schema["anyOf"] if option.get("type") == "string"
            )
        assert property_schema["maxLength"] == limit, field
    assert schema["properties"]["notes"]["maxItems"] == 5


def test_grounding_preserves_full_source_conditions_actions_and_notes_over_fifteen_chars() -> None:
    audience = "노원구에 거주하는 만 19세 이상 39세 이하 미취업 청년"
    action = "신청서를 작성해 신분증 사본과 함께 노원구청 누리집에서 온라인 신청"
    note = "신청서와 신분증 사본을 반드시 함께 제출"
    notice = make_notice(f"청년 지원사업 모집\n대상: {audience}\n신청: {action}\n유의사항: {note}")
    data = make_data(notice)
    data.update(
        category="application",
        summary="청년 지원사업 모집",
        audience=audience,
        audience_scope="conditional",
        action=action,
        action_requirement="optional",
        notes=[note],
        uncertainties=[],
        evidence=[
            {"field": "summary", "excerpt": "청년 지원사업 모집"},
            {"field": "audience", "excerpt": audience},
            {"field": "action", "excerpt": action},
            {"field": "notes", "excerpt": note},
        ],
    )
    assert all(len(value) > 15 for value in (audience, action, note))
    result = ground_summary(NoticeSummary.model_validate(data), notice)
    assert result.audience == audience
    assert result.audience_scope == "conditional"
    assert result.action == action
    assert result.action_requirement == "optional"
    assert result.notes == [note]
    assert result.uncertainties == []


@pytest.mark.parametrize("length", [16, 30, 31])
def test_publisher_metadata_uses_its_thirty_character_limit_in_grounding_and_fallback(
    length: int,
) -> None:
    publisher = "기" * length
    notice = make_notice(publisher=publisher)
    fallback = unknown_summary(notice)
    expected = publisher if length <= 30 else None
    assert fallback.publisher == expected

    data = make_data(notice)
    data.update(
        category="news",
        summary="공지",
        uncertainties=[],
        evidence=[{"field": "summary", "excerpt": "공지"}],
    )
    result = ground_summary(NoticeSummary.model_validate(data), notice)
    assert result.publisher == expected


def test_retry_feedback_reports_each_actual_limit_and_notes_item_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notice = make_notice()
    first = make_data(notice)
    first.update(
        summary="가" * 41,
        action="나" * 61,
        notes=["항목"] * 6,
        dates=[
            {
                "kind": "event",
                "label": "다" * 31,
                "text": None,
                "start_date": None,
                "end_date": None,
                "start_time": None,
                "end_time": None,
            }
        ],
    )
    first_raw = json.dumps(first, ensure_ascii=False)
    valid = make_data(notice)
    valid_raw = json.dumps(valid, ensure_ascii=False)
    requests: list[str] = []

    def fake_generate(**kwargs: object) -> str:
        requests.append(str(kwargs["notice_text"]))
        return first_raw if len(requests) == 1 else valid_raw

    monkeypatch.setattr(summarize_module, "generate_summary_json", fake_generate)
    result = summarize_module.summarize_notice(notice, api_key="dummy-key")
    assert len(requests) == 2
    assert "summary: 40자 제한 초과 (현재 41자)" in requests[1]
    assert "action: 60자 제한 초과 (현재 61자)" in requests[1]
    assert "dates.0.label: 30자 제한 초과 (현재 31자)" in requests[1]
    assert "notes: 최대 5개 제한 초과 (현재 6개)" in requests[1]
    assert first_raw in requests[1]
    assert "공백 포함 15자 이하" not in requests[1]
    assert result.action is None
    assert result.dates == []


def test_repeated_overlong_action_is_removed_without_truncating_valid_source_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audience = "노원구에 거주하는 만 19세 이상 39세 이하 미취업 청년"
    action = "신청서와 신분증 사본 및 추가 서류를 노원구청 담당 부서 누리집에서 제출" + "가" * 61
    note = "대상 조건과 제출 서류는 공지 원문에서 확인"
    notice = make_notice(f"청년 지원사업 모집\n대상: {audience}\n신청: {action}\n유의사항: {note}")
    data = make_data(notice)
    data.update(
        category="application",
        summary="청년 지원사업 모집",
        audience=audience,
        audience_scope="conditional",
        action=action,
        action_requirement="optional",
        notes=[note],
        uncertainties=[],
        card_summaries={
            "audience": f"{audience}이 대상이에요.",
            "deadline": None,
            "action": "담당 부서 누리집에서 서류를 제출해 주세요.",
            "notes": "대상 조건과 제출 서류는 공지 원문에서 확인해 주세요.",
        },
        evidence=[
            {"field": "summary", "excerpt": "청년 지원사업 모집"},
            {"field": "audience", "excerpt": audience},
            {"field": "action", "excerpt": action},
            {"field": "notes", "excerpt": note},
        ],
    )
    raw = json.dumps(data, ensure_ascii=False)
    calls = 0

    def fake_generate(**_kwargs: object) -> str:
        nonlocal calls
        calls += 1
        return raw

    monkeypatch.setattr(summarize_module, "generate_summary_json", fake_generate)
    result = summarize_module.summarize_notice(notice, api_key="dummy-key")
    assert calls == 2
    assert result.summary == "청년 지원사업 모집"
    assert result.audience == audience
    assert result.action is None
    assert result.action_requirement == "unknown"
    assert result.notes == [note]
    assert result.uncertainties == ["원문 확인 필요"]


@pytest.mark.parametrize("corrected_on_retry", [True, False])
def test_excess_notes_get_one_retry_and_do_not_discard_other_valid_claims(
    monkeypatch: pytest.MonkeyPatch, corrected_on_retry: bool
) -> None:
    notes = [f"준비 서류 {index}" for index in range(6)]
    notice = make_notice("주민 공지\n" + "\n".join(notes))
    data = make_data(notice)
    data.update(
        category="news",
        summary="주민 공지",
        notes=notes,
        uncertainties=[],
        card_summaries={
            "audience": None, "deadline": None, "action": None,
            "notes": "준비 서류 0, 1, 2, 3, 4, 5를 확인해 주세요.",
        },
        evidence=[
            {"field": "summary", "excerpt": "주민 공지"},
            *({"field": "notes", "excerpt": note} for note in notes),
        ],
    )
    first_raw = json.dumps(data, ensure_ascii=False)
    if corrected_on_retry:
        data["notes"] = notes[:5]
    retry_raw = json.dumps(data, ensure_ascii=False)
    requests: list[str] = []

    def fake_generate(**kwargs: object) -> str:
        requests.append(str(kwargs["notice_text"]))
        return first_raw if len(requests) == 1 else retry_raw

    monkeypatch.setattr(summarize_module, "generate_summary_json", fake_generate)
    result = summarize_module.summarize_notice(notice, api_key="dummy-key")
    assert len(requests) == 2
    assert "notes: 최대 5개 제한 초과 (현재 6개)" in requests[1]
    assert result.category == "news"
    assert result.summary == "주민 공지"
    if corrected_on_retry:
        assert result.notes == notes[:5]
        assert result.uncertainties == []
    else:
        assert result.notes == []
        assert result.uncertainties == ["원문 확인 필요"]
        assert not any(item.field == "notes" for item in result.evidence)


def test_long_cancellation_rule_preserves_restriction_and_exception_in_adjacent_notes() -> None:
    title = "캠프 참가자 모집"
    restriction = "전산추첨 후, 선정 된 학생은 임의로 취소 또는 포기할 수 없으며,"
    exception = "불가피한 경우에 한하여 노원구청 기획예산과(02-2116-3158)로 반드시 사전 연락 바람"
    rule = f"{restriction} {exception}"
    cost = "참가비용 : 30,000원"
    subsidy = "사회적배려대상 본인부담금 전액 노원구 지원"
    disaster = "천재지변 등의 재난상황 발생시 캠프가 취소 또는 연기될 수 있음"
    notes = [cost, subsidy, restriction, exception, disaster]
    notice = make_notice(f"{title}\n{cost}\n{subsidy}\n※ {rule}\n※ {disaster}", title=title)
    data = make_data(notice)
    data.update(
        category="application",
        summary=title,
        notes=notes,
        uncertainties=[],
        evidence=[
            {"field": "summary", "excerpt": title},
            {"field": "notes", "excerpt": cost},
            {"field": "notes", "excerpt": subsidy},
            {"field": "notes", "excerpt": rule},
            {"field": "notes", "excerpt": disaster},
        ],
    )

    result = ground_summary(NoticeSummary.model_validate(data), notice)

    assert len(rule) > 60
    assert all(len(note) <= 60 for note in notes)
    assert result.notes == notes
    assert result.uncertainties == []
    assert any(item.field == "notes" and item.excerpt == rule for item in result.evidence)
    assert "02-2116-3158" in result.notes[3]
