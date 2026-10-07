"""Arrange an internal summary into four named cards without choosing screen order.

This pure transformation preserves claims; it does not choose review guidance.
App responses must use storage.summary_view.build_notice_summary_view with the
persisted status, attachment_status and stored result.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pipeline.transform.summary_schema import (
    CATEGORY_CODE_NAMES,
    Category,
    CategoryCode,
    DateEntry,
    Evidence,
    NoticeSummary,
    Topic,
)

type CardKey = Literal["audience", "deadline", "action", "notes"]

MISSING_CARD_GUIDANCE = "원문을 확인해 주세요"

DATE_KIND_NAMES = {
    "application": "신청 일정",
    "event": "행사 일정",
    "operation": "운영 일정",
    "payment": "납부 일정",
    "submission": "제출 일정",
    "effective": "시행 일정",
    "disruption": "중단 일정",
    "result": "결과 발표 일정",
    "other": "기타 일정",
}
ACTION_REQUIREMENT_NAMES = {
    "required": "필수",
    "optional": "선택",
    "recommended": "권장",
    "none": "할 일 없음",
    "unknown": "확인되지 않음",
}
AUDIENCE_SCOPE_NAMES = {
    "general": "일반 대상",
    "conditional": "조건에 해당하는 대상",
    "specific": "특정 대상",
    "unknown": "확인되지 않음",
}
UPDATE_NAMES = {"modified": "변경", "extended": "연장", "cancelled": "취소"}


class SummaryCardError(ValueError):
    """Expose a safe conversion code, never a summary or validation input."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class CardModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FieldEvidence(CardModel):
    """An original field-level reference, not validation of an array item.

    Evidence.field has no array index. Several dates/notes may therefore carry
    the same reference list without claiming an individual text match.
    """

    source_path: str
    scope: Literal["field"] = "field"
    evidence: Evidence


class CardQualifier(CardModel):
    source_path: str
    value: str
    text: str
    evidence: list[FieldEvidence]


class CardItem(CardModel):
    """Source value and display text, with system guidance kept separate."""

    source_path: str
    label: str
    value: str | DateEntry
    text: str | None
    guidance: str | None = None
    qualifiers: list[CardQualifier] = Field(default_factory=list)
    evidence: list[FieldEvidence]


class SummaryCard(CardModel):
    key: CardKey
    title: Literal["대상", "기한", "할 일", "유의사항"]
    text: str | None = None
    availability: Literal["provided", "not_provided"]
    guidance: str | None
    items: list[CardItem]


class SummaryCardSet(CardModel):
    """Four named slots. Declaration/JSON key order is not a display contract."""

    audience: SummaryCard
    deadline: SummaryCard
    action: SummaryCard
    notes: SummaryCard


class SummaryCardMetadata(CardModel):
    """Keep source facts which cannot be assigned to an audience or action.

    In particular, publisher/applicable_area do not establish eligibility, and
    topics cannot be linked to dates or actions without source relationship IDs.
    """

    category: Category
    category_code: CategoryCode | None
    category_name: str | None
    publisher: str | None
    applicable_area: str | None
    audience_scope: Literal["general", "conditional", "specific", "unknown"]
    action_requirement: Literal["required", "optional", "recommended", "none", "unknown"]
    status: Literal[
        "upcoming",
        "open",
        "ongoing",
        "closed",
        "ended",
        "cancelled",
        "ongoing_intake",
        "check_required",
        "not_applicable",
        "unknown",
    ]
    status_detail: str | None
    notice_update: Literal["new", "modified", "extended", "cancelled", "unknown"]
    changed_details: str | None
    topics: list[Topic]
    uncertainties: list[str]
    evidence: list[FieldEvidence]


class SummaryCards(CardModel):
    headline: CardItem
    cards: SummaryCardSet
    metadata: SummaryCardMetadata


def summary_snapshot(summary: NoticeSummary) -> NoticeSummary:
    """Revalidate and copy even mutable nested models before transformation."""
    if not isinstance(summary, NoticeSummary):
        raise SummaryCardError("invalid_summary_result")
    try:
        return NoticeSummary.model_validate(summary.model_dump(mode="json", warnings="error"))
    except (TypeError, ValueError, ValidationError):
        raise SummaryCardError("invalid_summary_result") from None


def _references(summary: NoticeSummary, field: str | None = None) -> list[FieldEvidence]:
    return [
        FieldEvidence(
            source_path=f"evidence[{index}]",
            evidence=item.model_copy(deep=True),
        )
        for index, item in enumerate(summary.evidence)
        if field is None or item.field == field
    ]


def _text_item(
    summary: NoticeSummary, field: str, label: str, *, text: str | None = None
) -> CardItem:
    value = getattr(summary, field)
    return CardItem(
        source_path=field,
        label=label,
        value=value,
        text=value if text is None else text,
        evidence=_references(summary, field),
    )


def _qualifier(summary: NoticeSummary, field: str, names: dict[str, str]) -> CardQualifier:
    value = getattr(summary, field)
    return CardQualifier(
        source_path=field, value=value, text=names[value], evidence=_references(summary, field)
    )


def _date_text(value: DateEntry) -> str | None:
    """Keep each endpoint together, without inventing dates or interpreting text."""
    parts = [item for item in (value.label, value.text) if item is not None]
    for label, day, time in (
        ("시작", value.start_date, value.start_time),
        ("종료", value.end_date, value.end_time),
    ):
        endpoint = " ".join(item for item in (day.replace("-", ".") if day else None, time) if item)
        if endpoint:
            parts.append(f"{label}: {endpoint}")
    return " · ".join(parts) or None


def _card(
    key: CardKey, title: str, items: list[CardItem], *, text: str | None = None
) -> SummaryCard:
    provided = text is not None or bool(items)
    return SummaryCard(
        key=key,
        title=title,
        text=text if provided else MISSING_CARD_GUIDANCE,
        availability="provided" if provided else "not_provided",
        guidance=None if provided else MISSING_CARD_GUIDANCE,
        items=items,
    )


def build_summary_cards(summary: NoticeSummary) -> SummaryCards:
    """Preserve an internal NoticeSummary in four slots, with no IO or re-summary.

    Gemini card text is passed through verbatim, with original items retained.
    When that text is absent, callers retain the original item-based display;
    this transformation never synthesizes a replacement card summary. Empty
    cards show "원문을 확인해 주세요" as system guidance, while the stored
    summary retains its null values and availability remains not_provided. Missing
    fields do not mean everybody/free/no action. Empty slots contain only system
    guidance. Date kinds, notes and topics keep their input ordering and full
    values. No topic-to-claim relationships or screen order are inferred.
    Unverified evidence is preserved. The persisted-status view returns review
    guidance separately without changing the headline, cards, or source values.
    """
    checked = summary_snapshot(summary)
    card_text = checked.card_summaries
    audience = []
    if checked.audience is not None:
        audience.append(
            _text_item(checked, "audience", "대상").model_copy(
                update={"qualifiers": [_qualifier(checked, "audience_scope", AUDIENCE_SCOPE_NAMES)]}
            )
        )
    dates = []
    for index, entry in enumerate(checked.dates):
        text = _date_text(entry)
        dates.append(
            CardItem(
                source_path=f"dates[{index}]",
                label=DATE_KIND_NAMES[entry.kind],
                value=entry.model_copy(deep=True),
                text=text,
                guidance=None if text else "구체적인 날짜·시간은 원문을 확인하세요.",
                evidence=_references(checked, "dates"),
            )
        )
    actions = []
    if checked.action is not None:
        actions.append(
            _text_item(checked, "action", "할 일").model_copy(
                update={
                    "qualifiers": [
                        _qualifier(checked, "action_requirement", ACTION_REQUIREMENT_NAMES)
                    ]
                }
            )
        )
    elif checked.action_requirement == "none":
        actions.append(_text_item(checked, "action_requirement", "행동 구분", text="할 일 없음"))
    if checked.location is not None:
        # A location remains a place, never an inferred application method.
        actions.append(_text_item(checked, "location", "장소"))
    notes = [
        CardItem(
            source_path=f"notes[{index}]",
            label="유의사항",
            value=value,
            text=value,
            evidence=_references(checked, "notes"),
        )
        for index, value in enumerate(checked.notes)
    ]
    if checked.notice_update in UPDATE_NAMES:
        notes.append(
            _text_item(
                checked, "notice_update", "공지 변경", text=UPDATE_NAMES[checked.notice_update]
            )
        )
    if checked.status == "cancelled":
        notes.append(_text_item(checked, "status", "공지 상태", text="취소됨"))
    for field, label in (("changed_details", "변경 내용"), ("status_detail", "공지 상태 안내")):
        if getattr(checked, field) is not None:
            notes.append(_text_item(checked, field, label))
    return SummaryCards(
        headline=_text_item(checked, "summary", "한 줄 요약"),
        cards=SummaryCardSet(
            audience=_card(
                "audience", "대상", audience, text=card_text.audience if card_text else None
            ),
            deadline=_card(
                "deadline", "기한", dates, text=card_text.deadline if card_text else None
            ),
            action=_card("action", "할 일", actions, text=card_text.action if card_text else None),
            notes=_card("notes", "유의사항", notes, text=card_text.notes if card_text else None),
        ),
        metadata=SummaryCardMetadata(
            **checked.model_dump(
                include={
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
                }
            ),
            category_name=CATEGORY_CODE_NAMES.get(checked.category_code),
            evidence=_references(checked),
        ),
    )
