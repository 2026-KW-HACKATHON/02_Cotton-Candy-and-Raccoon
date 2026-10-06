"""Validate the JSON contract for a resident-facing notice summary."""

import re
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator


def _single_line(value: str) -> str:
    if not value.strip() or "\n" in value or "\r" in value:
        raise ValueError("summary text must be non-empty and on one line")
    return value


FIELD_TEXT_LIMITS = {
    "summary": 40,
    "audience": 40,
    "action": 60,
    "notes": 60,
    "dates.label": 30,
    "dates.text": 30,
    "publisher": 30,
    "applicable_area": 30,
    "location": 30,
    "status_detail": 30,
    "changed_details": 30,
    "topics.title": 20,
    "topics.summary": 40,
    "uncertainties": 15,
}
MAX_NOTES_ITEMS = 5
SingleLineText = Annotated[str, Field(min_length=1), AfterValidator(_single_line)]
NoteText = Annotated[SingleLineText, Field(max_length=FIELD_TEXT_LIMITS["notes"])]
UncertaintyText = Annotated[SingleLineText, Field(max_length=FIELD_TEXT_LIMITS["uncertainties"])]
Category = Literal["application", "event", "living", "obligation", "news", "mixed", "unknown"]
CategoryCode = Literal[21, 22, 23, 24, 25, 26, 27, 30]
CATEGORY_CODE_NAMES: dict[int, str] = {
    21: "교통",
    22: "안전",
    23: "주택",
    24: "경제",
    25: "환경",
    26: "문화",
    27: "복지",
    30: "행정",
}


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
    label: SingleLineText | None = Field(max_length=FIELD_TEXT_LIMITS["dates.label"])
    text: SingleLineText | None = Field(max_length=FIELD_TEXT_LIMITS["dates.text"])
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

    title: SingleLineText = Field(max_length=FIELD_TEXT_LIMITS["topics.title"])
    category: Category
    summary: SingleLineText = Field(max_length=FIELD_TEXT_LIMITS["topics.summary"])


@dataclass(frozen=True, slots=True)
class MediaSource:
    """A reference to one visual block actually included in this request."""

    source_id: str
    source_type: Literal["document", "image"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    field: Literal[
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
    ]
    excerpt: str = Field(min_length=1)
    source_type: Literal["text", "document", "image"] = "text"
    source_id: SingleLineText | None = None
    page: int | None = Field(default=None, ge=1)
    verification: Literal["text_matched", "file_reference_only"] | None = None

    @field_validator("excerpt")
    @classmethod
    def nonblank_excerpt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidence excerpt must not be blank")
        return value


class NoticeSummary(BaseModel):
    """Internal model output; storage exposes only validated summarized results.

    Unknown policy fields use category_code=None. Unverified claims may remain in
    this in-memory contract, but needs_review rows expose only an original-notice
    instruction, never this model output.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    category: Category
    category_code: CategoryCode | None
    summary: SingleLineText = Field(max_length=FIELD_TEXT_LIMITS["summary"])
    publisher: SingleLineText | None = Field(max_length=FIELD_TEXT_LIMITS["publisher"])
    applicable_area: SingleLineText | None = Field(max_length=FIELD_TEXT_LIMITS["applicable_area"])
    audience: SingleLineText | None = Field(max_length=FIELD_TEXT_LIMITS["audience"])
    audience_scope: Literal["general", "conditional", "specific", "unknown"]
    action: SingleLineText | None = Field(max_length=FIELD_TEXT_LIMITS["action"])
    action_requirement: Literal["required", "optional", "recommended", "none", "unknown"]
    location: SingleLineText | None = Field(max_length=FIELD_TEXT_LIMITS["location"])
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
    status_detail: SingleLineText | None = Field(max_length=FIELD_TEXT_LIMITS["status_detail"])
    notice_update: Literal["new", "modified", "extended", "cancelled", "unknown"]
    changed_details: SingleLineText | None = Field(max_length=FIELD_TEXT_LIMITS["changed_details"])
    notes: list[NoteText] = Field(max_length=MAX_NOTES_ITEMS)
    topics: list[Topic]
    uncertainties: list[UncertaintyText]
    evidence: list[Evidence]

    @field_validator("category_code", mode="before")
    @classmethod
    def integer_category_code(cls, value: object) -> object:
        # Numeric literals otherwise accept equal floats, even in strict models.
        if value is not None and type(value) is not int:
            raise ValueError("category_code must be a JSON integer or null")
        return value


class SummaryValidationError(ValueError):
    """The model returned JSON that cannot safely be used as a notice summary."""

    def __init__(self, message: str, *, reason_code: str = "response_validation_failed") -> None:
        self.reason_code = reason_code
        super().__init__(message)


def evidence_reference_valid(
    item: Evidence, *, sources: list[str], media_sources: tuple[MediaSource, ...] = ()
) -> bool:
    """Check text literally, or only the supplied file reference and page format.

    A PDF page number is a positive reference, not a locally verified page count.
    File quotes cannot be checked against the binary contents by this function.
    """
    if item.source_type == "text":
        return (
            item.source_id is None
            and item.page is None
            and any(item.excerpt in source for source in sources)
        )
    if item.source_type == "document" and item.page is None:
        return False
    if item.source_type == "image" and item.page is not None:
        return False
    return any(
        item.source_id == source.source_id and item.source_type == source.source_type
        for source in media_sources
    )


def validate_evidence(
    summary: NoticeSummary,
    *,
    body_text: str,
    attachment_texts: list[str],
    media_sources: tuple[MediaSource, ...] = (),
) -> None:
    """Validate each reference without presenting file quotes as text-matched."""
    sources = [body_text, *attachment_texts]
    for item in summary.evidence:
        if getattr(summary, item.field) in (None, []):
            raise SummaryValidationError(f"Evidence points to an empty field: {item.field}")
        if not evidence_reference_valid(item, sources=sources, media_sources=media_sources):
            raise SummaryValidationError(f"Evidence excerpt not found in source: {item.field}")
        expected = "text_matched" if item.source_type == "text" else "file_reference_only"
        if item.verification is not None and item.verification != expected:
            raise SummaryValidationError(f"Invalid evidence verification: {item.field}")

    if not any(source.strip() for source in sources) and not media_sources:
        if (
            summary.category != "unknown"
            or summary.category_code is not None
            or summary.summary != "공지 확인 불가"
            or summary.action is not None
            or summary.dates
            or summary.notes
            or summary.evidence
        ):
            raise SummaryValidationError("A notice without readable text must remain unknown")
        return

    partial_headline = summary.summary == "원문 확인 필요"
    if partial_headline and "원문 확인 필요" not in summary.uncertainties:
        raise SummaryValidationError("A partial summary must include a review instruction")
    required = set() if partial_headline else {"summary"}
    if partial_headline and summary.category != "unknown":
        required.add("category")
    for field in (
        "category_code", "applicable_area", "audience", "action", "location", "dates", "notes",
        "topics",
    ):
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
