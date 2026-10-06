"""Original notice text, dictionary decisions and reversible term replacements."""

import re
import unicodedata
from datetime import datetime
from typing import Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pipeline.glossary.models import (
    GlossaryEntry,
    GlossaryLookup,
    canonical_headword,
    normalize_query,
)
from pipeline.glossary.source import NoticeGlossaryInput, SourceOccurrence, source_hash
from pipeline.glossary.tokenize import tokenize_notice

RULES_VERSION = "dictionary-replacement-v4"
GlossaryFailureCode = Literal[
    "configuration",
    "authentication",
    "timeout",
    "transport",
    "rate_limit",
    "http",
    "invalid_response",
    "response_too_large",
    "too_many_results",
    "api",
]
GLOSSARY_FAILURE_CODES = frozenset(get_args(GlossaryFailureCode))
_CONFIG = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
_CONDITIONAL_REFINEMENT = re.compile(
    r"(?:에|으로|로)\s*(?:한(?:함|하여|해|한다|하는)|한정|국한)"
    r"|(?<![가-힣])(?:한정|국한)(?:해|하여|해서|한다|함|하[는며고]|된|되는)"
    r"|(?:에서|에)만"
    r"|분야에서(?=$|[^\w]|[는만])"
    r"|(?:[가-힣0-9]+(?:인|일|할|한|된|될|는|은|을|의)"
    r"|이|그|해당|특정|그런|이런|이러한|그러한)\s*경우(?=$|[^\w]|[에는로만])"
    r"|(?<![\w])경우(?:에는|에만|라면|에\s*따라)"
    r"|[가-힣]*(?:할|하는|일|된|될|인|있을|없을)\s*때(?=$|[^\w]|[에는만])"
)
_QUOTED_WORD = re.compile(r"[‘“\"']([^‘’“”\"']+)[’”\"']")


def _has_refinement_condition(entry: GlossaryEntry) -> bool:
    """Recognize scope clauses outside explicit mentions of official alternatives.

    A target such as 경우 or 한정 is a word, not a usage condition. Only its
    quoted mention, a labelled relation or an entire field equal to that target
    is masked; the surrounding clause remains available for condition checks.
    Thus ‘경우’로 다듬음 is unconditional, while ‘경우’인 경우에 한함 still
    keeps its restriction. Unqualified subject categories and source citations
    do not by themselves introduce a conditional clause.
    """
    alternatives = {unicodedata.normalize("NFC", word) for word in entry.easy_terms}
    for record in entry.norm_info:
        if record.type not in ("순화", "순화 정보", "순화어", "다듬은 말", "전문 분야"):
            continue
        fields = []
        for value in (record.role, record.description):
            text = unicodedata.normalize("NFC", value or "")
            if text.strip() in alternatives:
                text = "대체어"

            def mask_target(match: re.Match[str], original: str = text) -> str:
                word = match[1].strip()
                if word not in alternatives:
                    return match[0]
                # Keep a condition spanning the quoted word and its suffix,
                # such as ‘경우’에 따라; a literal ‘특정 경우’ stays masked.
                clause = word + original[match.end() :]
                condition = _CONDITIONAL_REFINEMENT.search(clause)
                while condition is not None and condition.start() < len(word):
                    if condition.end() > len(word):
                        return word
                    # Allow overlapping matches: ‘특정 경우’ contains a literal
                    # case phrase, followed by the condition ‘경우’에 따라.
                    condition = _CONDITIONAL_REFINEMENT.search(clause, condition.start() + 1)
                return "대체어"

            text = _QUOTED_WORD.sub(mask_target, text)
            for word in sorted(alternatives, key=lambda item: (-len(item), item)):
                text = re.sub(
                    r"(\(\s*다듬은\s*말\s*,\s*)" + re.escape(word) + r"(\s*\))",
                    r"\1대체어\2",
                    text,
                )
                text = re.sub(
                    r"(다듬은\s*말\s*[:：]\s*)" + re.escape(word) + r"(?=$|[^\w])",
                    r"\1대체어",
                    text,
                )
            fields.append(text)
        if _CONDITIONAL_REFINEMENT.search(" ".join(fields)):
            return True
    return False


class QueryOutcome(BaseModel):
    """A complete lookup, or a safe error code; never store raw exception messages."""

    model_config = _CONFIG
    query: str
    lookup: GlossaryLookup | None = None
    error_code: GlossaryFailureCode | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.query != normalize_query(self.query):
            raise ValueError("조회어는 정규화된 형태여야 합니다.")
        if (self.lookup is None) == (self.error_code is None):
            raise ValueError("조회 결과와 실패 코드는 하나만 제공해야 합니다.")
        if self.lookup is not None and self.lookup.query != self.query:
            raise ValueError("조회어와 사전 결과가 일치하지 않습니다.")
        return self


class DocumentTerm(BaseModel):
    """One original word, its precise occurrences and dictionary-backed decision."""

    model_config = _CONFIG
    word: str = Field(min_length=1)
    surface: str = Field(min_length=1)
    query: str = Field(min_length=1)
    status: Literal["replaced", "explained", "ambiguous", "not_found", "failed", "pending"]
    occurrences: tuple[SourceOccurrence, ...] = Field(min_length=1)
    entries: tuple[GlossaryEntry, ...] = ()
    replacement: str | None = None
    error_code: GlossaryFailureCode | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.query != normalize_query(self.query) or self.query != normalize_query(self.surface):
            raise ValueError("용어 조회어는 정규화된 형태여야 합니다.")
        if not self.word.startswith(self.surface) or any(
            canonical_headword(entry.headword) != canonical_headword(self.query)
            for entry in self.entries
        ):
            raise ValueError("원문 용어와 사전 표제어가 일치하지 않습니다.")
        if any(item.text != self.surface for item in self.occurrences):
            raise ValueError("용어와 원문 위치의 표현이 일치하지 않습니다.")
        if (self.status == "replaced") != (self.replacement is not None):
            raise ValueError("치환한 용어에만 다듬은 말을 지정할 수 있습니다.")
        if self.status in ("replaced", "explained", "ambiguous") and not self.entries:
            raise ValueError("설명·치환에는 사전 근거가 필요합니다.")
        if self.status in ("not_found", "failed", "pending") and self.entries:
            raise ValueError("확인하지 못한 용어에 사전 근거를 지정할 수 없습니다.")
        if self.replacement is not None and confirmed_replacement(self.entries) != self.replacement:
            raise ValueError("명확한 공식 다듬은 말만 치환할 수 있습니다.")
        if (self.status == "failed") != (self.error_code is not None):
            raise ValueError("실패한 용어에만 실패 코드를 지정해야 합니다.")
        return self


class TextChange(BaseModel):
    """A change in original Unicode-code-point coordinates, including particle adjustment."""

    model_config = _CONFIG
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    original: str = Field(min_length=1)
    replacement: str = Field(min_length=1)
    query: str


def confirmed_replacement(entries: tuple[GlossaryEntry, ...]) -> str | None:
    """Use official alternatives in source order; keep unresolved meanings unchanged.

    A professional field or bibliographic citation describes the source and is
    not by itself a restriction. Actual conditional wording is still respected.
    Multiple meanings must agree on the available alternatives until a meaning
    has been selected from the notice context.
    """
    if not entries or any(not entry.easy_terms for entry in entries):
        return None
    if any(_has_refinement_condition(entry) for entry in entries):
        return None
    alternatives = tuple(dict.fromkeys(entries[0].easy_terms))
    if any(set(entry.easy_terms) != set(alternatives) for entry in entries[1:]):
        return None
    first, *remaining = alternatives
    return first + (f"({', '.join(remaining)})" if remaining else "")


def _particle_change(text: str, end: int, replacement: str) -> tuple[int, str]:
    """Keep particle function; change its spelling only for a known Hangul ending."""
    # Parenthetical alternatives are display text; the particle follows the
    # ending of the primary official expression, not the closing parenthesis.
    primary = re.sub(r"\([^()]*\)$", "", replacement).rstrip()
    if not primary:
        return end, replacement
    last = unicodedata.normalize("NFC", primary)[-1]
    if not "가" <= last <= "힣":
        return end, replacement
    match = re.match(r"(으로|은|는|이|가|을|를|과|와|로)(?=$|[^\w]|부터|까지|도|만)", text[end:])
    if match is None:
        return end, replacement
    original = match[1]
    final = (ord(last) - ord("가")) % 28
    for consonant, vowel in (("은", "는"), ("이", "가"), ("을", "를"), ("과", "와")):
        if original in (consonant, vowel):
            particle = consonant if final else vowel
            return end + len(original), replacement + particle
    particle = "으로" if final not in (0, 8) else "로"
    return end + len(original), replacement + particle


def make_changes(original_text: str, terms: tuple[DocumentTerm, ...]) -> tuple[TextChange, ...]:
    changes = []
    for term in terms:
        if term.replacement is None:
            continue
        for occurrence in term.occurrences:
            end, replacement = _particle_change(original_text, occurrence.end, term.replacement)
            original = original_text[occurrence.start : end]
            if original != replacement:
                changes.append(
                    TextChange(
                        start=occurrence.start,
                        end=end,
                        original=original,
                        replacement=replacement,
                        query=term.query,
                    )
                )
    ordered = tuple(sorted(changes, key=lambda item: (item.start, item.end)))
    if any(left.end > right.start for left, right in zip(ordered, ordered[1:], strict=False)):
        raise ValueError("용어 치환 위치가 겹칩니다.")
    return ordered


def apply_changes(original_text: str, changes: tuple[TextChange, ...]) -> str:
    parts, cursor = [], 0
    for change in changes:
        if change.start < cursor or original_text[change.start : change.end] != change.original:
            raise ValueError("치환 위치가 원문과 일치하지 않습니다.")
        parts.extend((original_text[cursor : change.start], change.replacement))
        cursor = change.end
    parts.append(original_text[cursor:])
    return "".join(parts)


class NoticeGlossaryResult(BaseModel):
    """Save original and easier versions separately; unfinished work is explicitly partial."""

    model_config = _CONFIG
    notice_id: int | None = Field(default=None, gt=0, strict=True)
    notice_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    original_text: str = Field(min_length=1, max_length=100_000)
    easy_text: str = Field(min_length=1)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rules_version: str = Field(min_length=1)
    generated_at: datetime
    status: Literal["completed", "partial"]
    terms: tuple[DocumentTerm, ...] = ()
    queries: tuple[QueryOutcome, ...] = ()
    changes: tuple[TextChange, ...] = ()
    pending_queries: tuple[str, ...] = ()
    new_query_count: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def source_and_changes(self) -> Self:
        if self.notice_revision is not None and self.notice_id is None:
            raise ValueError("공지 원본 버전에는 공지 식별자가 필요합니다.")
        if self.source_hash != source_hash(self.original_text):
            raise ValueError("원문과 원문 해시가 일치하지 않습니다.")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("생성 시각에는 시간대가 필요합니다.")
        if len({item.query for item in self.queries}) != len(self.queries):
            raise ValueError("사전 조회 결과가 중복됩니다.")
        if len(set(self.pending_queries)) != len(self.pending_queries):
            raise ValueError("남은 조회어가 중복됩니다.")
        unfinished = any(term.status in ("failed", "pending") for term in self.terms)
        if (self.status == "partial") != bool(unfinished or self.pending_queries):
            raise ValueError("처리 상태와 남은 작업이 일치하지 않습니다.")
        for term in self.terms:
            for item in term.occurrences:
                if (
                    self.original_text[item.start : item.end] != item.text
                    or self.original_text[item.context_start : item.context_end] != item.context
                ):
                    raise ValueError("용어 위치 또는 문맥이 원문과 일치하지 않습니다.")
        expected = make_changes(self.original_text, self.terms)
        if self.changes != expected or self.easy_text != apply_changes(
            self.original_text, expected
        ):
            raise ValueError("쉬운말 버전은 확인된 용어 치환으로만 만들어야 합니다.")
        probes = tokenize_notice(NoticeGlossaryInput(text=self.original_text))
        by_word = {probe.surface: probe for probe in probes}
        if len({term.word for term in self.terms}) != len(self.terms) or set(by_word) != {
            term.word for term in self.terms
        }:
            raise ValueError("원문의 단어별 처리 결과가 누락되거나 중복됩니다.")
        by_query = {outcome.query: outcome for outcome in self.queries}
        for term in self.terms:
            probe = by_word[term.word]
            if not any(
                c.surface == term.surface and c.query == term.query for c in probe.candidates
            ):
                raise ValueError("원문에 없는 용어 후보입니다.")
            for candidate in probe.candidates:
                if candidate.surface == term.surface:
                    break
                prior = by_query.get(candidate.query)
                if prior is None or prior.lookup is None or prior.lookup.status != "not_found":
                    raise ValueError("원문 단어 전체의 확인 없이 조사를 제거할 수 없습니다.")
            if tuple(item.start for item in term.occurrences) != tuple(
                item.start for item in probe.occurrences
            ):
                raise ValueError("반복된 용어 위치가 누락되거나 추가되었습니다.")
            if term.status != "pending":
                outcome = by_query.get(term.query)
                if outcome is None:
                    raise ValueError("용어 처리의 사전 조회 기록이 없습니다.")
                if term.entries:
                    if outcome.lookup is None or outcome.lookup.entries != term.entries:
                        raise ValueError("용어의 사전 근거와 조회 기록이 일치하지 않습니다.")
                elif term.status == "failed" and outcome.error_code != term.error_code:
                    raise ValueError("실패 코드와 조회 기록이 일치하지 않습니다.")
                elif term.status == "not_found" and (
                    outcome.lookup is None or outcome.lookup.status != "not_found"
                ):
                    raise ValueError("검색 결과 없음과 조회 기록이 일치하지 않습니다.")
        if set(self.pending_queries) != {
            term.query for term in self.terms if term.status in ("failed", "pending")
        }:
            raise ValueError("남은 조회어와 미완료 용어가 일치하지 않습니다.")
        return self
