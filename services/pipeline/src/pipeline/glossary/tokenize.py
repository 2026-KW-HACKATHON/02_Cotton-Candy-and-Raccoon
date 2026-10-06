"""Produce conservative dictionary probes from exact words in notice text."""

import re
import unicodedata
from collections.abc import Iterator
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pipeline.glossary.models import normalize_query
from pipeline.glossary.source import (
    _KOREAN_PARTICLES,
    CONTEXT_CHARACTERS,
    NoticeGlossaryInput,
    SourceOccurrence,
    TermCandidate,
    _is_word_character,
)

_PARTICLES_LONGEST_FIRST = tuple(sorted(_KOREAN_PARTICLES, key=lambda item: (-len(item), item)))
_INTERNAL_SEPARATORS = frozenset({"-", "·"})
_PARTICLE_PATTERN = "(?:" + "|".join(_PARTICLES_LONGEST_FIRST) + ")?"
_DOMAIN_LABEL = r"[^\W_][\w\u0300-\u036f-]*"
_ADDRESS_END = _PARTICLE_PATTERN + r"(?=$|[^\w-])"
_PATH_TAIL = r"(?:[/?#][^\s<>\"'\[\]{}(),;]*)?"
_DOCUMENT_EXTENSIONS = "pdf|hwp|hwpx|doc|docx|xls|xlsx|ppt|pptx|txt|csv|tsv|rtf|odt|ods|odp|zip|7z"
_FILE_END = _PARTICLE_PATTERN + r"(?=$|[^\w.-]|\.(?=$|\s|[가-힣\u1100-\u11ff]))"
_MONTHLY_AMOUNT = r"(?<!\w)월[ \t]*\d+(?:,\d{3})*(?:\.\d+)?(?:천|만|억)?[ \t]*원"
# Start guards prevent rescanning the same long dotted run at every character.
_EXCLUDED_SPANS = re.compile(
    r"(?<![A-Za-z0-9+.-])(?:[A-Za-z][A-Za-z0-9+.-]*://|www\.)"
    r"[^\s<>\"'\[\]{}(),;]+"
    r"|(?<![^\s<>()\[\]{}\"',;:])[^\s<>()\[\]{}\"',;:]+@[^\s<>()\[\]{}\"',;:]+"
    r"|(?<=[\"'])(?:[A-Za-z]:[\\/]|\\\\)[^\r\n\"']+"
    r"|(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)[^\s<>\"'\[\]{}(),;]+"
    r"|(?<![^\s<>\"'\[\]{}(),;:!?=])(?:[A-Za-z]:[\\/])?"
    r"[^\s<>\"'\[\]{}(),;:!?=]+?\."
    + "(?:"
    + _DOCUMENT_EXTENSIONS
    + ")"
    + _FILE_END
    + r"|(?<![\w.@-])(?:"
    + _DOMAIN_LABEL
    + r"\.)+"
    + r"(?:[A-Za-z]{2,63}|xn--[A-Za-z0-9-]{2,59}|한국)"
    + _ADDRESS_END
    + _PATH_TAIL
    + "|"
    + _MONTHLY_AMOUNT
    + _ADDRESS_END,
    re.IGNORECASE,
)


class WordProbe(BaseModel):
    """One exact original word, ordered lookup alternatives and token positions.

    The first candidate always uses the complete word. Callers may use stripped
    alternatives only after a successful ``not_found`` lookup for that word.
    Occurrences refer only to this exact complete token, so an independent word
    or a different particle form cannot bypass its own complete-word lookup.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    surface: str = Field(min_length=1)
    candidates: tuple[TermCandidate, ...] = Field(min_length=1)
    occurrences: tuple[SourceOccurrence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_probe(self) -> Self:
        surfaces = [candidate.surface for candidate in self.candidates]
        starts = [occurrence.start for occurrence in self.occurrences]
        if (
            surfaces[0] != self.surface
            or len(set(surfaces)) != len(surfaces)
            or any(
                not self.surface.startswith(candidate.surface)
                or candidate.query != normalize_query(candidate.surface)
                for candidate in self.candidates
            )
            or starts != sorted(set(starts))
            or any(occurrence.text != self.surface for occurrence in self.occurrences)
        ):
            raise ValueError("원문 단어와 조회 후보 또는 단어 위치가 일치하지 않습니다.")
        return self


def _word_spans(text: str) -> Iterator[tuple[int, int]]:
    """Read whole Unicode word runs, retaining only internal '-' and '·'."""
    cursor = 0
    while cursor < len(text):
        if not _is_word_character(text[cursor]):
            cursor += 1
            continue
        start = cursor
        while cursor < len(text):
            if _is_word_character(text[cursor]):
                cursor += 1
            elif (
                text[cursor] in _INTERNAL_SEPARATORS
                and cursor + 1 < len(text)
                and _is_word_character(text[cursor + 1])
            ):
                cursor += 1
            else:
                break
        yield start, cursor


def _candidates_for_word(surface: str) -> tuple[TermCandidate, ...]:
    candidates = [TermCandidate(surface=surface, query=surface)]
    for particle in _PARTICLES_LONGEST_FIRST:
        if not surface.endswith(particle):
            continue
        base = surface[: -len(particle)]
        if len(unicodedata.normalize("NFC", base)) < 2 or not base[-1].isalpha():
            continue
        candidates.append(TermCandidate(surface=base, query=base))
    return tuple(candidates)


def tokenize_notice(source: NoticeGlossaryInput) -> tuple[WordProbe, ...]:
    """Return distinct original words in first-occurrence order, without I/O.

    Words start with a Unicode letter and may contain Unicode letters, combining
    marks, digits and internal hyphens or middle dots. Numeric-leading runs
    (including dates, amounts and unit values), monthly won amounts beginning
    with 월 and digits, URLs (including bare Unicode
    domains), email addresses, known document filenames/paths, connector
    punctuation words and punctuation are excluded. A normalized query longer
    than the existing dictionary query limit of 200 code points is skipped whole.

    Query normalization never changes the original surface or source offsets.
    Each complete word precedes alternatives obtained by one literal removal
    from the source module's particle allowlist, longest suffix first. Bases need
    at least two NFC query code points and an alphabetic ending. Removal is not
    recursive; verb stems, unlisted particle combinations and multiword phrases
    are not inferred. Query limits and lookup outcomes belong to the caller.

    Exact complete-token occurrences retain every repeat and at most 80 source
    code points of surrounding context. Indices are half-open Python Unicode
    code point offsets, as in ``SourceOccurrence``.
    """
    excluded = tuple(
        (match.start(), match.end()) for match in _EXCLUDED_SPANS.finditer(source.text)
    )
    excluded_index = 0
    grouped: dict[str, list[SourceOccurrence]] = {}
    for start, end in _word_spans(source.text):
        while excluded_index < len(excluded) and excluded[excluded_index][1] <= start:
            excluded_index += 1
        if excluded_index < len(excluded) and excluded[excluded_index][0] < end:
            continue
        surface = source.text[start:end]
        if (
            unicodedata.category(surface[0])[0] != "L"
            or any(unicodedata.category(character) == "Pc" for character in surface)
            or len(unicodedata.normalize("NFC", surface)) > 200
        ):
            continue
        context_start = max(0, start - CONTEXT_CHARACTERS)
        context_end = min(len(source.text), end + CONTEXT_CHARACTERS)
        grouped.setdefault(surface, []).append(
            SourceOccurrence(
                start=start,
                end=end,
                text=surface,
                context_start=context_start,
                context_end=context_end,
                context=source.text[context_start:context_end],
            )
        )
    return tuple(
        WordProbe(
            surface=surface,
            candidates=_candidates_for_word(surface),
            occurrences=tuple(occurrences),
        )
        for surface, occurrences in grouped.items()
    )
