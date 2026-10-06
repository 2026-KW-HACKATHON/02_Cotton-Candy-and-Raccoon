"""Keep exact notice text and validate literal glossary term occurrences."""

import hashlib
import json
import unicodedata
from collections.abc import Iterable
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pipeline.glossary.models import normalize_query

MAX_SOURCE_CHARACTERS = 100_000
CONTEXT_CHARACTERS = 80

# This conservative allowlist recognizes a complete suffix, not an arbitrary
# Korean ending. Unlisted compound particles must be included in the surface.
_KOREAN_PARTICLES = frozenset(
    {
        "은",
        "는",
        "이",
        "가",
        "을",
        "를",
        "에",
        "의",
        "와",
        "과",
        "도",
        "만",
        "로",
        "으로",
        "에서",
        "에게",
        "한테",
        "께",
        "부터",
        "까지",
        "처럼",
        "보다",
    }
)


class NoticeGlossaryInput(BaseModel):
    """Plain text for one notice; never strip or normalize the source.

    Character limits and all occurrence offsets count Python Unicode code
    points. Offsets are not UTF-8 byte offsets or JavaScript UTF-16 offsets.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    notice_id: int | None = Field(default=None, gt=0, strict=True)
    notice_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    text: str = Field(min_length=1, max_length=MAX_SOURCE_CHARACTERS)

    @model_validator(mode="after")
    def revision_has_notice(self) -> Self:
        if self.notice_revision is not None and self.notice_id is None:
            raise ValueError("공지 원문 버전에는 공지 ID가 필요합니다.")
        return self

    @field_validator("text")
    @classmethod
    def require_nonblank_text(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("공지 원문은 비어 있을 수 없습니다.")
        return text


class TermCandidate(BaseModel):
    """An exact, attested source surface and a separate normalized lookup query."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    surface: str = Field(min_length=1)
    query: str

    @field_validator("surface")
    @classmethod
    def require_nonblank_surface(cls, surface: str) -> str:
        if not surface.strip():
            raise ValueError("용어 표기는 비어 있을 수 없습니다.")
        return surface

    _normalize_query = field_validator("query")(normalize_query)


class SourceOccurrence(BaseModel):
    """One exact source slice with bounded original context.

    Every interval is half-open: ``source[start:end]`` is ``text`` and
    ``source[context_start:context_end]`` is ``context``. All indices count
    Python Unicode code points, including each decomposed combining character.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    text: str = Field(min_length=1)
    context_start: int = Field(ge=0, strict=True)
    context_end: int = Field(gt=0, strict=True)
    context: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_slices(self) -> Self:
        if not self.context_start <= self.start < self.end <= self.context_end:
            raise ValueError("원문 위치와 문맥 범위가 일치하지 않습니다.")
        if (
            len(self.text) != self.end - self.start
            or len(self.context) != self.context_end - self.context_start
            or self.context[self.start - self.context_start : self.end - self.context_start]
            != self.text
        ):
            raise ValueError("원문 위치와 원문 문자열이 일치하지 않습니다.")
        return self


class LocatedTerm(BaseModel):
    """A candidate and all accepted occurrences in ascending source order."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    candidate: TermCandidate
    occurrences: tuple[SourceOccurrence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_occurrences(self) -> Self:
        starts = [occurrence.start for occurrence in self.occurrences]
        if starts != sorted(set(starts)) or any(
            occurrence.text != self.candidate.surface for occurrence in self.occurrences
        ):
            raise ValueError("용어 표기와 원문 위치 목록이 일치하지 않습니다.")
        return self


def source_hash(source: NoticeGlossaryInput | str) -> str:
    """Return lowercase SHA-256 hex of the exact source text encoded as UTF-8.

    Notice identifiers are excluded; whitespace and Unicode normalization
    differences remain significant.
    """
    text = source.text if isinstance(source, NoticeGlossaryInput) else source
    if not isinstance(text, str):
        raise TypeError("공지 원문은 문자열이어야 합니다.")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def notice_content_revision(title: str, body_html: str | None) -> str:
    """Fingerprint exact collected title/HTML, separately from prepared plain text.

    A DB-loaded input carries this revision so an older task cannot replace a
    result after the collector has changed the parent notice. Even HTML changes
    that render to the same plain text remain distinct source revisions.
    """
    if not isinstance(title, str) or body_html is not None and not isinstance(body_html, str):
        raise ValueError("공지 원문 버전을 계산할 제목·본문 형식이 올바르지 않습니다.")
    encoded = json.dumps([title, body_html], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _is_word_character(character: str) -> bool:
    """Letters, marks, numbers and connector punctuation continue a word."""
    return unicodedata.category(character)[0] in {"L", "M", "N"} or (
        unicodedata.category(character) == "Pc"
    )


def _has_term_boundaries(text: str, surface: str, start: int, end: int) -> bool:
    if _is_word_character(surface[0]) and start > 0 and _is_word_character(text[start - 1]):
        return False
    if not _is_word_character(surface[-1]) or end == len(text):
        return True
    if not _is_word_character(text[end]):
        return True
    if not surface[-1].isalpha():
        return False
    suffix_end = end
    while suffix_end < len(text) and _is_word_character(text[suffix_end]):
        suffix_end += 1
    return text[end:suffix_end] in _KOREAN_PARTICLES


def locate_candidates(
    source: NoticeGlossaryInput, candidates: Iterable[TermCandidate]
) -> tuple[LocatedTerm, ...]:
    """Locate literal, case-sensitive surfaces without modifying the source.

    A surface beginning with a word character needs a left word boundary. A
    surface ending with a word character needs a right boundary, except an
    alphabetic ending may precede one complete, explicitly allowed Korean
    particle. The allowlist is 은/는/이/가/을/를/에/의/와/과/도/만/로/으로/에서/
    에게/한테/께/부터/까지/처럼/보다. This deliberately does not infer stems,
    strip other endings or recognize unlisted compound particles. Numeric
    endings always require a right boundary.

    Repeated identical candidates collapse in first supplied order. Different
    queries for the same exact surface, and surfaces with no accepted occurrence,
    raise ``ValueError``. Every accepted repeat is retained, including overlaps.
    Context includes at most 80 original code points before and after the term.
    """
    unique: dict[str, TermCandidate] = {}
    for candidate in candidates:
        if not isinstance(candidate, TermCandidate):
            raise TypeError("후보 용어는 TermCandidate여야 합니다.")
        previous = unique.get(candidate.surface)
        if previous is not None and previous.query != candidate.query:
            raise ValueError("같은 용어 표기에 서로 다른 조회어를 사용할 수 없습니다.")
        unique.setdefault(candidate.surface, candidate)

    located: list[LocatedTerm] = []
    for candidate in unique.values():
        occurrences: list[SourceOccurrence] = []
        search_from = 0
        while (start := source.text.find(candidate.surface, search_from)) != -1:
            end = start + len(candidate.surface)
            search_from = start + 1
            if not _has_term_boundaries(source.text, candidate.surface, start, end):
                continue
            context_start = max(0, start - CONTEXT_CHARACTERS)
            context_end = min(len(source.text), end + CONTEXT_CHARACTERS)
            occurrences.append(
                SourceOccurrence(
                    start=start,
                    end=end,
                    text=source.text[start:end],
                    context_start=context_start,
                    context_end=context_end,
                    context=source.text[context_start:context_end],
                )
            )
        if not occurrences:
            raise ValueError("후보 용어 표기가 공지 원문에 독립된 용어로 나타나지 않습니다.")
        located.append(LocatedTerm(candidate=candidate, occurrences=tuple(occurrences)))
    return tuple(located)
