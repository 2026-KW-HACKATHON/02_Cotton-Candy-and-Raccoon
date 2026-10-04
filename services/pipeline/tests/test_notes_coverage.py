"""Check conservative textual condition coverage without API or DB calls."""

from dataclasses import FrozenInstanceError

import pytest

from pipeline.transform.grounding import unknown_summary
from pipeline.transform.notes_coverage import MissingNoteCondition, find_missing_note_conditions
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_schema import NoticeSummary

COST = "참가비용 : 30,000원"
SUPPORT = "사회적배려대상 본인부담금 전액 노원구 지원"
SELECTION = "모집정원 초과 신청 시, 전산추첨을 통하여 참가자를 선정함"


def _notice(body: str, *, attachments: list[dict[str, str]] | None = None) -> NoticeInput:
    return NoticeInput.model_validate(
        {
            "title": "캠프 참가자 모집",
            "body_text": body,
            "reference_datetime": "2026-10-04T12:00:00+09:00",
            "attachments": attachments or [],
        }
    )


def _summary(
    notice: NoticeInput, notes: list[str] | None = None, **changes: object
) -> NoticeSummary:
    values = unknown_summary(notice).model_dump(mode="json")
    values.update(category="application", summary="캠프 참가자 모집", notes=notes or [])
    return NoticeSummary.model_validate(values | changes)


def test_camp_explicit_cost_support_and_overflow_selection_are_missing() -> None:
    notice = _notice(f"■ {COST}\n- {SUPPORT}\n※ {SELECTION}")
    summary = _summary(
        notice,
        [
            "전산추첨 후, 선정 된 학생은 임의로 취소 또는 포기할 수 없으며",
            "천재지변 등의 재난상황 발생시 캠프가 취소 또는 연기될 수 있음",
        ],
    )
    missing = find_missing_note_conditions(summary, notice)
    assert [condition.kind for condition in missing] == ["cost", "support", "selection"]
    assert [condition.excerpt for condition in missing] == [COST, SUPPORT, SELECTION]


def test_evidence_for_whole_source_is_not_a_displayed_note() -> None:
    notice = _notice(f"{COST}\n{SUPPORT}\n{SELECTION}")
    summary = _summary(notice, evidence=[{"field": "notes", "excerpt": notice.body_text}])
    assert len(find_missing_note_conditions(summary, notice)) == 3


def test_all_complete_literal_notes_cover_their_own_conditions() -> None:
    notice = _notice(f"{COST}\n{SUPPORT}\n{SELECTION}")
    assert find_missing_note_conditions(_summary(notice, [COST, SUPPORT, SELECTION]), notice) == ()


@pytest.mark.parametrize("name", ["안내.hwp", "안내.docx", "안내.txt"])
def test_extracted_attachment_text_has_the_same_coverage_contract(name: str) -> None:
    notice = _notice("", attachments=[{"name": name, "text": COST}])
    missing = find_missing_note_conditions(_summary(notice), notice)
    assert [condition.kind for condition in missing] == ["cost"]


@pytest.mark.parametrize(
    ("source", "note", "kind"),
    [
        (COST, "참가비용", "cost"),
        (COST, "30,000원", "cost"),
        (SUPPORT, "본인부담금 전액 노원구 지원", "support"),
        (SUPPORT, "사회적배려대상 본인부담금 전액", "support"),
        (SELECTION, "전산추첨을 통하여 참가자를 선정함", "selection"),
        (SELECTION, "모집정원 초과 신청 시", "selection"),
    ],
)
def test_the_amount_target_and_overflow_condition_must_be_retained(
    source: str, note: str, kind: str
) -> None:
    notice = _notice(source)
    assert [
        condition.kind
        for condition in find_missing_note_conditions(_summary(notice, [note]), notice)
    ] == [kind]


def test_a_different_award_with_the_same_amount_cannot_cover_the_cost() -> None:
    notice = _notice(f"{COST}\n경품 : 30,000원")
    missing = find_missing_note_conditions(_summary(notice, ["경품 : 30,000원"]), notice)
    assert [condition.kind for condition in missing] == ["cost"]


def test_a_bare_amount_with_award_evidence_does_not_cover_a_direct_fee() -> None:
    notice = _notice(f"{COST}\n경품 : 30,000원")
    summary = _summary(
        notice, ["30,000원"], evidence=[{"field": "notes", "excerpt": "경품 : 30,000원"}]
    )
    assert [condition.kind for condition in find_missing_note_conditions(summary, notice)] == [
        "cost"
    ]


def test_a_contiguous_combined_note_can_cover_two_adjacent_conditions() -> None:
    cost = "참가비: 1만원"
    support = "수급자 본인부담금 전액 지원"
    combined = f"{cost}; {support}"
    notice = _notice(combined)
    assert find_missing_note_conditions(_summary(notice, [combined]), notice) == ()


@pytest.mark.parametrize(
    "cost",
    [
        "수강료: 무료",
        "입장료: 0원",
        "참가비용 : 없음",
        "참가비 없음",
        "이용료는 1만원",
        "참가비 무료",
    ],
)
def test_explicit_free_or_simple_cost_values_are_supported(cost: str) -> None:
    notice = _notice(cost)
    assert [
        condition.kind for condition in find_missing_note_conditions(_summary(notice), notice)
    ] == ["cost"]
    assert find_missing_note_conditions(_summary(notice, [cost]), notice) == ()


@pytest.mark.parametrize(
    "source",
    [
        "참가비용 : 30,000원 아님",
        "사회적배려대상 본인부담금 전액 지원하지 않음",
        "모집정원 초과 신청 시 추첨 없음",
        "모집정원 초과 신청 시 전산추첨을 통하여 선정하지 않습니다",
        "문의: 참가비용 : 30,000원",
        "문의전화: 30,000원",
        "경품: 30,000원",
        "지원금: 30,000원",
        "노원구 본인부담금 전액 지원",
        "사회적배려대상 본인부담금 전액 지원 예정",
        "사회적배려대상 본인부담금 전액 지원 신청 가능",
        "신청방법: 온라인 접수",
        "전산추첨 후 선정된 학생은 취소할 수 없음",
        "정원 초과 시 추첨 행사에 참여",
        "참가비용: 성인 30,000원, 학생 20,000원",
        "참가비용: 30,000원 (사회적배려대상 감면)",
        "참가비용\n30,000원",
        "참가비용\t30,000원",
        "참가비용 | 30,000원",
        "모집정원 초과 신청 시 전산추첨 또는 선착순으로 선정",
    ],
)
def test_ambiguous_irrelevant_or_negated_clauses_are_not_missing_conditions(source: str) -> None:
    notice = _notice(source)
    assert find_missing_note_conditions(_summary(notice), notice) == ()


@pytest.mark.parametrize("header", ["예시", "예제", "변경 전", "종전", "사업별 비교표"])
def test_historical_examples_and_ambiguous_table_sections_are_not_guessed(header: str) -> None:
    notice = _notice(f"{header}\n{COST}")
    assert find_missing_note_conditions(_summary(notice), notice) == ()


def test_a_later_example_does_not_suppress_an_earlier_explicit_current_fee() -> None:
    notice = _notice(f"{COST}\n예시\n참가비용: 10,000원")
    assert find_missing_note_conditions(_summary(notice), notice) == (
        MissingNoteCondition("cost", COST),
    )


@pytest.mark.parametrize("heading", ["변경 후", "현재 금액:", "현행", "최종 안내"])
def test_an_explicit_current_section_ends_the_historical_example_scope(heading: str) -> None:
    notice = _notice(f"변경 전\n참가비용: 10,000원\n{heading}\n{COST}")
    assert find_missing_note_conditions(_summary(notice), notice) == (
        MissingNoteCondition("cost", COST),
    )


def test_an_unmarked_fee_after_an_example_heading_is_not_assumed_current() -> None:
    notice = _notice(f"다음은 예시입니다\n{COST}")
    assert find_missing_note_conditions(_summary(notice), notice) == ()


@pytest.mark.parametrize(
    "family",
    ["다문화가정", "한부모가정", "맞벌이가정", "다문화 가정", "한부모 가정", "맞벌이 가정"],
)
def test_family_notice_titles_do_not_suppress_current_cost_and_conditions(family: str) -> None:
    notice = _notice(f"{family} 캠프 참가자 모집\n{COST}\n{SUPPORT}\n{SELECTION}")
    assert [
        condition.kind for condition in find_missing_note_conditions(_summary(notice), notice)
    ] == ["cost", "support", "selection"]


@pytest.mark.parametrize(
    "assumption",
    [
        "가정",
        "가정: 아래 조건으로 계산",
        "가정 : 아래 조건으로 계산",
        "참가비가 30,000원이라고 가정",
        "참가비가 30,000원이라고 가정하여 계산합니다",
        "참가비를 30,000원으로가정한 경우",
        "참가비를 30,000원으로 가정합니다",
        "가정한다면 다음과 같습니다",
    ],
)
def test_explicit_hypothetical_assumptions_still_exclude_following_conditions(
    assumption: str,
) -> None:
    notice = _notice(f"{assumption}\n{COST}\n{SUPPORT}\n{SELECTION}")
    assert find_missing_note_conditions(_summary(notice), notice) == ()


def test_multiple_businesses_or_different_group_costs_are_outside_initial_scope() -> None:
    notice = _notice("A 사업\n참가비: 30,000원\nB 사업\n참가비: 20,000원")
    assert find_missing_note_conditions(_summary(notice), notice) == ()
    assert (
        find_missing_note_conditions(_summary(_notice(COST), category="mixed"), _notice(COST)) == ()
    )


def test_identical_costs_in_different_businesses_are_not_a_single_target_condition() -> None:
    notice = _notice(f"A 사업\n{COST}\nB 사업\n{COST}")
    assert find_missing_note_conditions(_summary(notice), notice) == ()


@pytest.mark.parametrize(
    "support",
    [
        "차상위계층 수강료 50% 감면",
        "수급자 본인부담금 3만원 지원",
        "모든 참가자 본인부담금 전액 지원",
    ],
)
def test_direct_amount_and_percentage_support_rules_are_checked(support: str) -> None:
    notice = _notice(support)
    assert [
        condition.kind for condition in find_missing_note_conditions(_summary(notice), notice)
    ] == ["support"]
    assert find_missing_note_conditions(_summary(notice, [support]), notice) == ()


@pytest.mark.parametrize(
    "target",
    [
        "신청 대상은 홈페이지 참고",
        "지원 대상은 별도 확인",
        "지원 대상은 추후 안내",
        "지원 대상 미정",
    ],
)
def test_unconfirmed_or_external_reference_targets_are_not_definite_support_conditions(
    target: str,
) -> None:
    notice = _notice(f"{target} 본인부담금 전액 지원")
    assert find_missing_note_conditions(_summary(notice), notice) == ()


def test_explicit_overflow_first_come_selection_is_checked() -> None:
    selection = "정원 초과 시 선착순으로 선정"
    notice = _notice(selection)
    assert [
        condition.kind for condition in find_missing_note_conditions(_summary(notice), notice)
    ] == ["selection"]
    assert find_missing_note_conditions(_summary(notice, [selection]), notice) == ()


def test_duplicate_identical_clauses_do_not_duplicate_feedback() -> None:
    notice = _notice(f"{COST}\n{COST}")
    missing = find_missing_note_conditions(_summary(notice), notice)
    assert missing == (MissingNoteCondition("cost", COST),)


def test_conditions_in_one_document_cannot_be_assembled_from_other_documents() -> None:
    notice = _notice(
        "사회적배려대상 본인부담금", attachments=[{"name": "다른 문서", "text": "전액 지원"}]
    )
    assert find_missing_note_conditions(_summary(notice), notice) == ()


def test_pdf_and_image_evidence_is_not_locally_extracted_text() -> None:
    notice = _notice("")
    summary = _summary(
        notice,
        evidence=[
            {
                "field": "notes",
                "excerpt": COST,
                "source_type": "document",
                "source_id": "media_1",
                "page": 1,
            },
            {"field": "notes", "excerpt": SUPPORT, "source_type": "image", "source_id": "media_2"},
        ],
    )
    assert find_missing_note_conditions(summary, notice) == ()


def test_missing_condition_is_immutable_and_does_not_print_private_source_text() -> None:
    condition = MissingNoteCondition("cost", "private source excerpt")
    assert "private source excerpt" not in repr(condition)
    assert "private source excerpt" not in str(condition)
    with pytest.raises(FrozenInstanceError):
        condition.kind = "support"  # type: ignore[misc]
