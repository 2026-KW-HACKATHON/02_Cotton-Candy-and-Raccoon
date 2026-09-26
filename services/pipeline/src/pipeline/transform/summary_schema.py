"""Validate the JSON contract for a resident-facing notice summary."""

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator


def _single_line(value: str) -> str:
    if not value.strip() or "\n" in value or "\r" in value:
        raise ValueError("summary text must be non-empty and on one line")
    return value


MAX_SHORT_TEXT_LENGTH = 15
ShortText = Annotated[
    str, Field(min_length=1, max_length=MAX_SHORT_TEXT_LENGTH), AfterValidator(_single_line)
]
Category = Literal["application", "event", "living", "obligation", "news", "mixed", "unknown"]


class DateEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal[
        "application",
        "event",
        "operation",
        "payment",
        "submission",
        "effective",
        "disruption",
        "result",
        "other",
    ]
    label: ShortText | None
    text: ShortText | None
    start_date: str | None
    end_date: str | None
    start_time: str | None
    end_time: str | None

    @field_validator("start_date", "end_date")
    @classmethod
    def valid_date(cls, value: str | None) -> str | None:
        if value is not None:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("date must use YYYY-MM-DD")
            date.fromisoformat(value)
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def valid_time(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("time must use HH:MM")
        return value


class Topic(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: ShortText
    category: Category
    summary: ShortText


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    field: Literal[
        "category",
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
    ]
    excerpt: str = Field(min_length=1)

    @field_validator("excerpt")
    @classmethod
    def nonblank_excerpt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidence excerpt must not be blank")
        return value


class NoticeSummary(BaseModel):
    """The complete output shape required by the checked-in prompt."""

    model_config = ConfigDict(extra="forbid", strict=True)

    category: Category
    summary: ShortText
    publisher: ShortText | None
    applicable_area: ShortText | None
    audience: ShortText | None
    audience_scope: Literal["general", "conditional", "specific", "unknown"]
    action: ShortText | None
    action_requirement: Literal["required", "optional", "recommended", "none", "unknown"]
    location: ShortText | None
    dates: list[DateEntry]
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
    status_detail: ShortText | None
    notice_update: Literal["new", "modified", "extended", "cancelled", "unknown"]
    changed_details: ShortText | None
    notes: list[ShortText]
    topics: list[Topic]
    uncertainties: list[ShortText]
    evidence: list[Evidence]


class SummaryValidationError(ValueError):
    """The model returned JSON that cannot safely be used as a notice summary."""


def validate_evidence(
    summary: NoticeSummary, *, body_text: str, attachment_texts: list[str]
) -> None:
    """Check that each quoted excerpt actually occurs in the supplied source text."""
    sources = [body_text, *attachment_texts]
    for item in summary.evidence:
        if getattr(summary, item.field) in (None, []):
            raise SummaryValidationError(f"Evidence points to an empty field: {item.field}")
        if not any(item.excerpt in source for source in sources):
            raise SummaryValidationError(f"Evidence excerpt not found in source: {item.field}")

    if not any(source.strip() for source in sources):
        if (
            summary.category != "unknown"
            or summary.summary != "공지 확인 불가"
            or summary.action is not None
            or summary.dates
            or summary.notes
            or summary.evidence
        ):
            raise SummaryValidationError("A notice without readable text must remain unknown")
        return

    if summary.category == "unknown":
        if summary.action is not None or summary.dates or summary.notes:
            raise SummaryValidationError("Unknown notice cannot claim actions or dates")

    required = set() if summary.category == "unknown" else {"summary"}
    for field in ("applicable_area", "audience", "action", "location", "dates", "notes", "topics"):
        if getattr(summary, field) not in (None, []):
            required.add(field)
    missing = required - {item.field for item in summary.evidence}
    if missing:
        raise SummaryValidationError(f"Missing evidence for: {', '.join(sorted(missing))}")

    if (
        summary.action
        and any(verb in summary.action for verb in ("신청", "접수", "예약"))
        and any(method in summary.action for method in ("홈페이지", "누리집", "온라인", "인터넷"))
    ):
        action_excerpts = [item.excerpt for item in summary.evidence if item.field == "action"]
        if not any(
            any(method in excerpt for method in ("홈페이지", "누리집", "온라인", "인터넷"))
            and any(verb in excerpt for verb in ("신청", "접수", "예약"))
            for excerpt in action_excerpts
        ):
            raise SummaryValidationError(
                "Action evidence does not show an online application route"
            )
