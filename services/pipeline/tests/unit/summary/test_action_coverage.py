"""Adversarial signup omission checks using source text, without external calls."""

from dataclasses import FrozenInstanceError

import pytest

from pipeline.transform.action_coverage import (
    MAX_CONDITION_EXCERPT,
    MAX_MISSING_ACTION_CONDITIONS,
    MissingActionCondition,
    find_missing_action_conditions,
)
from pipeline.transform.grounding import unknown_summary
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_schema import NoticeSummary

FRESH_ECO_BODY = """2026년 마.들.장(마을에서 만나는 나들이 장터)
‘노원 모두의정원 가을 소풍’
◼일시 2026년 10월 24일(토) 11:00 ~ 14:00
◼장소 노원에코센터 (숲놀이터, 모두의정원)
◼내용 정원.환경 제험부스, 농부장터, 공연, 에코 이벤트외
◼마들장 행사 및 체험부스 문의 : 02-3392-4911(노원에코센터)
◼가을 상상데이 사전 접수 문의 : 02-931-1104 (마들상상놀이터)
*모든 체험은 무료입니다
*우천시에도 정상 진행, 행사장내 주차불가"""
ECO_SIGNUP = "가을 상상데이 사전 접수 문의 : 02-931-1104 (마들상상놀이터)"


def _notice(body: str, *, attachment: str | None = None) -> NoticeInput:
    return NoticeInput.model_validate(
        {
            "title": "행사 안내",
            "body_text": body,
            "reference_datetime": "2026-10-07T12:00:00+09:00",
            "attachments": [{"name": "안내.txt", "text": attachment}] if attachment else [],
        }
    )


def _summary(notice: NoticeInput, **changes: object) -> NoticeSummary:
    payload = unknown_summary(notice).model_dump(mode="json")
    payload.update(category="event", action_requirement="none")
    return NoticeSummary.model_validate(payload | changes)


def test_actual_fresh_eco_notice_reports_program_signup_not_plain_contact() -> None:
    notice = _notice(FRESH_ECO_BODY)
    summary = _summary(notice)
    assert find_missing_action_conditions(summary, notice) == (
        MissingActionCondition("preregistration", ECO_SIGNUP),
    )
    assert summary.action is None
    assert summary.action_requirement == "none"


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ("사전 신청: 홈페이지", "preregistration"),
        ("사전접수 문의: 02-1234-5678", "preregistration"),
        ("신청 필수", "required_application"),
        ("현장 접수", "onsite_registration"),
    ],
)
def test_explicit_condition_returns_an_original_quote(source: str, kind: str) -> None:
    notice = _notice(source)
    missing = find_missing_action_conditions(_summary(notice), notice)
    assert [(item.kind, item.excerpt) for item in missing] == [(kind, source)]


@pytest.mark.parametrize("source", ["문의: 02-1234-5678", "참여 문의", "참여 희망자는 문의"])
def test_contact_alone_never_becomes_an_action_requirement(source: str) -> None:
    notice = _notice(source)
    assert find_missing_action_conditions(_summary(notice), notice) == ()


@pytest.mark.parametrize(
    "source",
    [
        "사전 신청 필요 없음",
        "사전 신청 불필요",
        "사전 신청 없이 참여하세요",
        "사전 신청이 필요하지 않습니다",
        "사전 신청 필수가 아닙니다",
        "신청 필수 아님",
        "사전 신청이 가능한지 문의해 주세요",
        "사전 신청 여부는 추후 안내",
        "신청 필수서류: 주민등록등본",
        "사전 접수는 받지 않습니다",
        "현장 접수 불가",
        "현장 접수 없음",
        "예시: 사전 신청 필수",
        "변경 전\n사전 접수",
        "사전 접수 | 현장 접수",
    ],
)
def test_negated_historical_and_ambiguous_conditions_are_not_inferred(source: str) -> None:
    notice = _notice(source)
    assert find_missing_action_conditions(_summary(notice), notice) == ()


def test_negated_preregistration_does_not_hide_current_onsite_registration() -> None:
    notice = _notice("사전 신청 없이 현장 접수로 참여하세요")
    assert [item.kind for item in find_missing_action_conditions(_summary(notice), notice)] == [
        "onsite_registration"
    ]


def test_explicit_current_heading_ends_historical_scope() -> None:
    notice = _notice("변경 전\n사전 접수\n변경 후\n현장 접수")
    assert find_missing_action_conditions(_summary(notice), notice) == (
        MissingActionCondition("onsite_registration", "현장 접수"),
    )


@pytest.mark.parametrize("field", ["action", "notes", "card_action", "card_notes"])
def test_retained_source_field_or_card_prose_covers_condition(field: str) -> None:
    notice = _notice(ECO_SIGNUP)
    text = "가을 상상데이는 사전 신청으로 참여해요."
    changes: dict[str, object] = {}
    if field == "action":
        changes["action"] = text
    elif field == "notes":
        changes["notes"] = [text]
    else:
        cards = {"audience": None, "deadline": None, "action": None, "notes": None}
        cards[field.removeprefix("card_")] = text
        changes["card_summaries"] = cards
    assert find_missing_action_conditions(_summary(notice, **changes), notice) == ()


def test_evidence_only_does_not_count_as_retained_content() -> None:
    notice = _notice(ECO_SIGNUP)
    summary = _summary(notice, evidence=[{"field": "action", "excerpt": ECO_SIGNUP}])
    assert find_missing_action_conditions(summary, notice)


def test_a_different_program_signup_cannot_cover_the_original_program() -> None:
    notice = _notice(ECO_SIGNUP)
    summary = _summary(notice, action="농부 체험은 사전 신청해요.")
    assert find_missing_action_conditions(summary, notice)


def test_negated_signup_in_summary_is_not_coverage() -> None:
    notice = _notice("사전 신청: 홈페이지")
    summary = _summary(notice, action="사전 신청 없이 참여해요.")
    assert find_missing_action_conditions(summary, notice)


def test_attachment_text_is_checked_without_media_guessing() -> None:
    notice = _notice("", attachment=ECO_SIGNUP)
    assert find_missing_action_conditions(_summary(notice), notice) == (
        MissingActionCondition("preregistration", ECO_SIGNUP),
    )
    empty = _notice("")
    assert find_missing_action_conditions(_summary(empty), empty) == ()


def test_duplicate_quotes_are_deduplicated() -> None:
    notice = _notice(ECO_SIGNUP, attachment=ECO_SIGNUP)
    assert len(find_missing_action_conditions(_summary(notice), notice)) == 1


def test_long_quote_stays_bounded_and_is_an_original_contiguous_slice() -> None:
    source = "안내 " * 200 + "사전 신청: 홈페이지 " + "안내 " * 200
    notice = _notice(source)
    missing = find_missing_action_conditions(_summary(notice), notice)
    assert len(missing) == 1
    assert len(missing[0].excerpt) <= MAX_CONDITION_EXCERPT
    assert "사전 신청" in missing[0].excerpt
    assert missing[0].excerpt in source


def test_feedback_size_is_bounded() -> None:
    notice = _notice("\n".join(f"체험 {index}: 사전 접수" for index in range(100)))
    assert len(find_missing_action_conditions(_summary(notice), notice)) == (
        MAX_MISSING_ACTION_CONDITIONS
    )


def test_frozen_feedback_does_not_log_the_source_quote() -> None:
    condition = MissingActionCondition("preregistration", "사전 접수 문의: 담당자 연락처")
    assert "담당자" not in repr(condition)
    with pytest.raises(FrozenInstanceError):
        condition.excerpt = "different"  # type: ignore[misc]


def test_mixed_notices_are_not_automatically_inferred() -> None:
    notice = _notice(ECO_SIGNUP)
    assert find_missing_action_conditions(_summary(notice, category="mixed"), notice) == ()


@pytest.mark.parametrize(
    "source",
    [
        "사전 신청: 없음",
        "사전 접수 (불필요)",
        "사전 접수를 별도로 받지 않습니다",
        "현장 접수는 일절 받지 않습니다",
        "사전 신청을 할 필요가 없습니다",
        "사전 신청을 안 해도 참여할 수 있습니다",
        "사전 접수가 필수인지 문의해 주세요",
        "문의: 사전 신청이 필요한가요?",
    ],
)
def test_labelled_negations_and_explicit_questions_do_not_trigger_retry(source: str) -> None:
    notice = _notice(source)
    assert find_missing_action_conditions(_summary(notice), notice) == ()


def test_inevitable_is_not_a_registration_ban() -> None:
    notice = _notice("안전상 사전 신청 불가피")
    assert find_missing_action_conditions(_summary(notice), notice)


@pytest.mark.parametrize(
    ("source", "retained"),
    [
        ("행사 참여 프로그램 사전 접수", "프로그램은 사전 접수로 참여해요."),
        ("선착순 15명 체험 사전 접수", "체험은 사전 접수로 참여해요."),
    ],
)
def test_generic_program_descriptions_do_not_require_literal_full_prefix(
    source: str, retained: str
) -> None:
    notice = _notice(source)
    assert find_missing_action_conditions(_summary(notice, action=retained), notice) == ()


def test_unrelated_sentence_cannot_attach_signup_to_a_program_name() -> None:
    notice = _notice(ECO_SIGNUP)
    summary = _summary(
        notice,
        action="사전 신청은 농부 체험에만 필요해요. 가을 상상데이는 자유 참여예요.",
    )
    assert find_missing_action_conditions(summary, notice)


def test_program_particle_does_not_lose_the_specific_program_context() -> None:
    notice = _notice("가을 상상데이만 사전 접수")
    summary = _summary(notice, action="농부 체험은 사전 신청해요.")
    assert find_missing_action_conditions(summary, notice)


@pytest.mark.parametrize("condition", ["사전접수를", "사전 신청은", "현장접수로"])
def test_korean_particles_and_spacing_keep_an_explicit_condition(condition: str) -> None:
    notice = _notice(f"{condition} 참여해 주세요")
    assert find_missing_action_conditions(_summary(notice), notice)


@pytest.mark.parametrize("particle", ["만", "은", "는", "도", "에만"])
def test_program_scope_survives_common_particles(particle: str) -> None:
    notice = _notice(f"가을 상상데이{particle} 사전 접수")
    summary = _summary(notice, action="농부 체험은 사전 신청해요.")
    assert find_missing_action_conditions(summary, notice)


def test_negated_source_field_cannot_cover_the_original_positive_condition() -> None:
    notice = _notice("사전 접수: 홈페이지")
    summary = _summary(notice, action="사전 접수: 없음")
    assert find_missing_action_conditions(summary, notice)


def test_card_keeps_two_program_names_with_one_shared_signup_clause() -> None:
    notice = _notice(ECO_SIGNUP)
    summary = _summary(notice, action="농부 체험과 가을 상상데이는 사전 신청해요.")
    assert find_missing_action_conditions(summary, notice) == ()


@pytest.mark.parametrize(
    "source",
    [
        "사전 신청하실 필요가 없습니다",
        "사전 접수하실 필요는 없어요",
        "사전신청하실 필요까지는 없습니다",
        "사전 신청하실 의무가 없습니다",
        "현장 접수하실 수 없습니다",
        "현장 접수하실 수는 없습니다",
        "사전 신청하시지 않아도 됩니다",
        "사전 신청하시지 않으셔도 됩니다",
        "사전 신청하셔도 되지 않습니다",
        "사전 접수하셔도 되지는 않습니다",
        "사전 신청하셔도 안 됩니다",
        "현장 접수하시면 안 됩니다",
        "사전 신청하시는 것은 필수가 아닙니다",
    ],
)
def test_explicit_honorific_negations_are_not_missing_signup_conditions(source: str) -> None:
    notice = _notice(source)
    assert find_missing_action_conditions(_summary(notice), notice) == ()


@pytest.mark.parametrize(
    "source",
    [
        "사전신청하셔야 합니다",
        "사전 접수하셔야 참여하실 수 있습니다",
        "사전 신청하실 분은 홈페이지를 확인하세요",
        "사전 신청하셔도 됩니다",
        "현장 접수하실 수 있습니다",
        "사전 신청하실 필요가 있으시면 연락해 주세요",
    ],
)
def test_positive_honorific_signup_still_reports_a_missing_condition(source: str) -> None:
    notice = _notice(source)
    assert find_missing_action_conditions(_summary(notice), notice)


def test_optional_preregistration_does_not_hide_required_onsite_registration() -> None:
    notice = _notice("사전 신청하실 필요는 없지만 현장 접수하셔야 합니다")
    assert [item.kind for item in find_missing_action_conditions(_summary(notice), notice)] == [
        "onsite_registration"
    ]


def test_honorific_negated_card_cannot_cover_a_positive_source_condition() -> None:
    notice = _notice("사전 신청하셔야 합니다")
    summary = _summary(notice, action="사전 신청하실 필요가 없어요.")
    assert find_missing_action_conditions(summary, notice)


@pytest.mark.parametrize(
    "source",
    [
        "사전 신청하시지 않으면 참여하실 수 없습니다.",
        "사전 신청하시지 않으시면 참여하실 수 없습니다.",
        "사전 신청하지 않으면 참여할 수 없습니다.",
        "사전 신청하지 않을 경우 참여할 수 없습니다.",
        "사전 접수하지 않은 경우 참여할 수 없습니다.",
        "사전 신청하시지 않으실 경우 참여하실 수 없습니다.",
        "사전 신청하지 않는 분은 참여할 수 없습니다.",
        "사전 신청 안 하시면 참여할 수 없습니다.",
        "사전 신청 안 하실 경우 참여할 수 없습니다.",
        "사전 신청 안 하신 경우 참여할 수 없습니다.",
        "사전 신청 안 하셨을 경우 참여할 수 없습니다.",
        "사전 접수 안 하면 참여할 수 없습니다.",
        "현장 접수가 불가하면 온라인으로 신청하세요.",
    ],
)
def test_conditional_negation_preserves_the_signup_condition_as_an_original_quote(
    source: str,
) -> None:
    notice = _notice(source)
    summary = _summary(notice)
    missing = find_missing_action_conditions(summary, notice)
    assert len(missing) == 1
    assert missing[0].excerpt == source
    assert summary.action is None
    assert summary.action_requirement == "none"


def test_conditionally_denied_preregistration_keeps_onsite_alternative_in_original_quote() -> None:
    source = "사전 신청하시지 않으면 현장 접수하셔야 참여할 수 있습니다."
    notice = _notice(source)
    missing = find_missing_action_conditions(_summary(notice), notice)
    assert [item.kind for item in missing] == ["preregistration", "onsite_registration"]
    assert all(item.excerpt == source for item in missing)


def test_retained_conditional_signup_instruction_is_not_mistaken_for_no_signup() -> None:
    source = "사전 신청하시지 않으면 참여하실 수 없습니다."
    notice = _notice(source)
    summary = _summary(notice, action=source)
    assert find_missing_action_conditions(summary, notice) == ()
