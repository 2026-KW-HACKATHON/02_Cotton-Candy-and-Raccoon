"""Validate the JSON contract for a resident-facing notice summary."""

import re
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)


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
TITLE_EVIDENCE_FIELDS = frozenset({"summary", "category", "category_code"})
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


class CardSummaries(BaseModel):
    """Gemini-written prose for the four resident-facing summary cards."""

    model_config = ConfigDict(extra="forbid", strict=True)

    audience: SingleLineText | None
    deadline: SingleLineText | None
    action: SingleLineText | None
    notes: SingleLineText | None


_QUOTED_CARD_TEXT = re.compile(
    r'"[^"\n]*"|\'[^\'\n]*\'|“[^”\n]*”|‘[^’\n]*’|「[^」\n]*」|『[^』\n]*』'
)
_DACHE_CARD_ENDING = re.compile(
    r"(?:니다|한다|된다|이다|있다|없다|하다|바란다|받는다|따른다|본다|쓴다|"
    r"간다|준다|온다|갖는다|않는다|싶다)(?=요?(?:[.!?;；]|$))"
)
_NON_YOCHE_NOUNS = ("필요", "중요", "주요", "개요", "수요", "동요", "소요", "민요")


def card_text_uses_yoche(value: str) -> bool:
    """Check a literal 요 ending and obvious unquoted 다-style sentences.

    This conservative style check is not a Korean grammar parser. Quoted source
    wording and embedded clauses are allowed; the original text is never changed.
    """
    if not value.strip() or "\n" in value or "\r" in value:
        return False
    ending = value.rstrip().rstrip(".!?").rstrip()
    if (
        not ending.endswith("요")
        or ending.split()[-1] == "요"
        or ending.endswith(_NON_YOCHE_NOUNS)
    ):
        return False
    unquoted = _QUOTED_CARD_TEXT.sub("", ending)
    return _DACHE_CARD_ENDING.search(unquoted) is None


class GeminiCardSummaries(CardSummaries):
    """Require 요-style prose only in freshly generated Gemini card text."""

    @field_validator("audience", "deadline", "action", "notes")
    @classmethod
    def yoche_card_text(cls, value: str | None) -> str | None:
        if value is not None and not card_text_uses_yoche(value):
            raise ValueError(
                "fresh card text must end in polite Korean 요 style "
                "without mixed 다-style sentences"
            )
        return value


class NoticeSummary(BaseModel):
    """Schema-valid AI output with evidence and review information.

    Unknown policy fields use category_code=None. Unverified claims are retained
    in needs_review rows and displayed with an original-notice warning. The
    warning belongs to the public view rather than the 40-character summary.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    # Local execution outcome, never a Gemini field or resident-facing JSON.
    _correction_failure_code: str | None = PrivateAttr(default=None)

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
    card_summaries: CardSummaries | None = None

    @field_validator("category_code", mode="before")
    @classmethod
    def integer_category_code(cls, value: object) -> object:
        # Numeric literals otherwise accept equal floats, even in strict models.
        if value is not None and type(value) is not int:
            raise ValueError("category_code must be a JSON integer or null")
        return value


class GeminiNoticeSummary(NoticeSummary):
    """Require fresh prose for known card facts while retaining truly missing slots.

    A news summary can use policy-default action_requirement='none' without an
    explicit source statement. That classification alone never requires new
    prose claiming there is no action. A cited no-action statement does require
    prose; grounding checks whether the citation belongs to the supplied source.
    """

    card_summaries: GeminiCardSummaries

    @model_validator(mode="after")
    def known_fields_have_card_text(self) -> Self:
        required = {
            "audience": self.audience is not None,
            "deadline": bool(self.dates),
            "action": (
                self.action is not None
                or self.location is not None
                or (
                    self.action_requirement == "none"
                    and any(item.field == "action_requirement" for item in self.evidence)
                )
            ),
            "notes": (
                bool(self.notes)
                or self.changed_details is not None
                or self.status_detail is not None
                or self.notice_update in {"modified", "extended", "cancelled"}
                or self.status == "cancelled"
            ),
        }
        errors = [
            {
                "type": "value_error",
                "loc": ("card_summaries", key),
                "input": None,
                "ctx": {"error": ValueError("fresh card text is missing for known source fields")},
            }
            for key, needed in required.items()
            if needed and getattr(self.card_summaries, key) is None
        ]
        if errors:
            # Keep a slot-specific location so the shared correction merge can
            # repair only that card and preserve first-response source facts.
            raise ValidationError.from_exception_data(type(self).__name__, errors)
        return self


class SummaryValidationError(ValueError):
    """The model returned JSON that cannot safely be used as a notice summary."""

    def __init__(self, message: str, *, reason_code: str = "response_validation_failed") -> None:
        self.reason_code = reason_code
        super().__init__(message)


def evidence_reference_valid(
    item: Evidence,
    *,
    sources: list[str],
    media_sources: tuple[MediaSource, ...] = (),
    title: str | None = None,
) -> bool:
    """Check text literally, or only the supplied file reference and page format.

    A PDF page number is a positive reference, not a locally verified page count.
    File quotes cannot be checked against the binary contents by this function.
    A supplied metadata title supports only headlines and classification, never
    eligibility, actions or schedules. Omitting title retains body/file checks.
    """
    if item.source_type == "text":
        return (
            item.source_id is None
            and item.page is None
            and (
                any(item.excerpt in source for source in sources)
                or item.field in TITLE_EVIDENCE_FIELDS
                and title is not None
                and item.excerpt in title
            )
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
    title: str | None = None,
) -> None:
    """Validate each reference without presenting file quotes as text-matched."""
    sources = [body_text, *attachment_texts]
    for item in summary.evidence:
        if getattr(summary, item.field) in (None, []):
            raise SummaryValidationError(f"Evidence points to an empty field: {item.field}")
        if not evidence_reference_valid(
            item, sources=sources, media_sources=media_sources, title=title
        ):
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
