"""Rewrite a notice as attested question sections and attest dictionary candidates.

The current generation (easy-rewrite-v1) rewrites the body into question-headed
sections whose every sentence quotes its source. Results of the earlier
term-replacement generation remain readable: their changes still reproduce
easy_text exactly, and ProposedChange/apply_easy_language_changes serve only
that legacy validation.
"""

import re
import unicodedata
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import quote, quote_plus

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from pipeline.gemini_execution import (
    ExecutionBudget,
    FailureKind,
    GeminiExecutionError,
    current_execution,
    execution_budget,
)
from pipeline.glossary.source import (
    MAX_SOURCE_CHARACTERS,
    NoticeGlossaryInput,
    StoredNoticeInput,
    source_hash,
)
from pipeline.transform.summary_schema import Evidence, evidence_reference_valid

DEFAULT_MODEL = "gemini-3.5-flash-lite"
PROMPT_VERSION = "easy-rewrite-v1"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "gemini_easy_rewrite.md"
MAX_TERM_CHARACTERS = 100
# Rewrite limits. The prompt asks for 60-character sentences; 80 is the hard cap.
MAX_SENTENCE_CHARACTERS = 80
MAX_EVIDENCE_CHARACTERS = 120
# A shorter quote ("1", "다") attests nothing; a whole body shorter than this is exempt.
MIN_EVIDENCE_CHARACTERS = 5
MAX_HEADING_CHARACTERS = 25
# Copy and length checks are meaningful only for bodies of at least this length.
REWRITE_CHECK_MIN_BODY_CHARACTERS = 300
MAX_COPY_RATIO = 0.8
# The whole-text ratio shrinks as the body grows, so each sentence is also compared
# with its own quotes and, from this length, searched for verbatim in the body.
MAX_SENTENCE_COPY_RATIO = 0.9
MIN_COPIED_SENTENCE_CHARACTERS = 10
_FORBIDDEN_SYMBOLS = ("「", "」", "·", "*", "※")
# Official names such as '청소년 역사·평화·환경 캠프' may keep their symbols in quotes.
_QUOTED_SPAN = re.compile(r"'[^'\n]*'|‘[^’\n]*’")
_NUMBER_TOKEN = re.compile(r"\d+")

# These recognize literal data formats, rather than guessing a Korean sentence's meaning.
_PROTECTED_PATTERNS = (
    re.compile(r"(?:https?://|www\.)[^\s<>]+", re.IGNORECASE),
    re.compile(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+"),
    re.compile(r"\d+(?:[.,:/~\-–]\d+)*(?:\s*[년월일시분초])?"),
    re.compile(r"[월화수목금토일]요일"),
)


class EasyLanguageConfigurationError(ValueError):
    """The local source, prompt or Gemini settings cannot be used."""


class EasyLanguageAPIError(GeminiExecutionError):
    """Gemini failed; remote details and credentials are deliberately excluded."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "api_error",
        failure_kind: FailureKind = "permanent",
        retryable: bool = False,
        retry_at: datetime | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(
            reason_code, failure_kind=failure_kind, retryable=retryable,
            retry_at=retry_at, status_code=status_code,
        )
        self.args = (message,)


class EasyLanguageValidationError(ValueError):
    """Gemini failed JSON or literal source validation on both attempts."""


class NoNoticeBodyError(ValueError):
    """A stored notice has no body text; its title is never converted instead."""


def _validate_term(original: str, replacement: str) -> None:
    if not original.strip() or not replacement.strip():
        raise ValueError("용어와 바꿀 말은 비어 있을 수 없습니다.")
    if original != original.strip() or replacement != replacement.strip():
        raise ValueError("용어 바깥의 공백을 바꿀 수 없습니다.")
    if original == replacement:
        raise ValueError("바꿀 말은 원래 용어와 달라야 합니다.")
    if original.replace(" ", "") == replacement.replace(" ", ""):
        raise ValueError("띄어쓰기만 바꾼 항목은 쉬운말 변경이 아닙니다.")
    if any(character.isnumeric() for character in original + replacement):
        raise ValueError("숫자를 포함한 표기는 바꿀 수 없습니다.")
    if any(
        character != " " and unicodedata.category(character)[0] not in {"L", "M"}
        for character in original + replacement
    ):
        # Term spans contain letters/combining marks and ordinary spaces only.
        # Punctuation, controls, hidden formats and every other space remain
        # outside replacement spans, where source segments preserve them exactly.
        raise ValueError("용어에는 글자와 일반 공백만 사용할 수 있습니다.")
    if any(pattern.search(replacement) for pattern in _PROTECTED_PATTERNS):
        raise ValueError("날짜나 연락처를 새로 넣을 수 없습니다.")


class ProposedChange(BaseModel):
    """One model-proposed term and an exact original context; no model offsets."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    original: str = Field(min_length=1, max_length=MAX_TERM_CHARACTERS, strict=True)
    replacement: str = Field(min_length=1, max_length=MAX_TERM_CHARACTERS, strict=True)
    context: str = Field(min_length=1, max_length=MAX_SOURCE_CHARACTERS, strict=True)

    @model_validator(mode="after")
    def validate_term(self) -> Self:
        _validate_term(self.original, self.replacement)
        return self


def _validate_dictionary_term(value: str) -> None:
    if not value.strip() or value != value.strip():
        raise ValueError("사전 후보와 조회어는 바깥 공백 없이 입력해야 합니다.")
    # Keep lexical punctuation (e.g. 외래어·전문어, e-메일) while excluding
    # URL/query operators, hidden characters and whole sentence fragments.
    if not any(unicodedata.category(character)[0] == "L" for character in value) or any(
        unicodedata.category(character)[0] not in {"L", "M", "N"}
        and unicodedata.category(character) != "Pd"
        and character not in {" ", "·", "ㆍ", "'", "’"}
        for character in value
    ):
        raise ValueError("사전 후보와 조회어에는 실제 용어만 사용할 수 있습니다.")
    if (
        not _is_word_character(value[0])
        or not _is_word_character(value[-1])
        or any(word in {"AND", "OR", "NOT"} for word in value.split())
        or any(pattern.search(value) for pattern in _PROTECTED_PATTERNS)
    ):
        raise ValueError("사전 후보와 조회어에는 검색 연산자나 보호 표기를 넣을 수 없습니다.")


class ProposedDictionaryCandidate(BaseModel):
    """An exact source expression and its proposed dictionary lookup form."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    original: str = Field(min_length=1, max_length=MAX_TERM_CHARACTERS, strict=True)
    query_word: str = Field(min_length=1, max_length=MAX_TERM_CHARACTERS, strict=True)
    context: str = Field(min_length=1, max_length=MAX_SOURCE_CHARACTERS, strict=True)

    @model_validator(mode="after")
    def validate_terms(self) -> Self:
        _validate_dictionary_term(self.original)
        _validate_dictionary_term(self.query_word)
        return self


class EasyLanguageResponse(BaseModel):
    """Require both independently chosen proposal lists, possibly empty."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    changes: tuple[ProposedChange, ...]
    dictionary_candidates: tuple[ProposedDictionaryCandidate, ...]


def _one_line(value: str) -> str:
    if not value.strip() or value != value.strip() or "\n" in value or "\r" in value:
        raise ValueError("쉬운말 문장은 앞뒤 공백 없는 한 줄이어야 합니다.")
    return value


class EasySentence(BaseModel):
    """One rewritten line and the exact body quotes that support it."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    text: str = Field(min_length=1, max_length=MAX_SENTENCE_CHARACTERS, strict=True)
    evidence: tuple[
        Annotated[str, Field(min_length=1, max_length=MAX_EVIDENCE_CHARACTERS, strict=True)], ...
    ] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_line(self) -> Self:
        _one_line(self.text)
        if any(not quote.strip() for quote in self.evidence):
            raise ValueError("근거 문장은 비어 있을 수 없습니다.")
        return self


class EasySection(BaseModel):
    """A question heading and its sentences, read as prose or numbered steps."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    heading: str = Field(min_length=2, max_length=MAX_HEADING_CHARACTERS, strict=True)
    style: Literal["paragraph", "steps"]
    sentences: tuple[EasySentence, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_heading(self) -> Self:
        _one_line(self.heading)
        if not self.heading.endswith("?"):
            raise ValueError("섹션 제목은 물음표로 끝나는 질문이어야 합니다.")
        return self


class EasyRewrite(BaseModel):
    """The whole rewritten body; the notice title stays outside Gemini output."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    headline: str = Field(min_length=1, max_length=MAX_SENTENCE_CHARACTERS, strict=True)
    intro: tuple[EasySentence, ...] = Field(max_length=3)
    sections: tuple[EasySection, ...] = Field(min_length=1, max_length=6)
    attachment_hint: str | None = Field(
        default=None, min_length=1, max_length=MAX_SENTENCE_CHARACTERS, strict=True
    )

    @model_validator(mode="after")
    def validate_lines(self) -> Self:
        _one_line(self.headline)
        if self.attachment_hint is not None:
            _one_line(self.attachment_hint)
        return self

    def sentences(self) -> Iterator[EasySentence]:
        yield from self.intro
        for section in self.sections:
            yield from section.sentences


class EasyRewriteResponse(BaseModel):
    """Gemini output: one rewrite and the original-text dictionary candidates."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    rewrite: EasyRewrite
    dictionary_candidates: tuple[ProposedDictionaryCandidate, ...]


def flatten_easy_rewrite(rewrite: EasyRewrite) -> str:
    """Plain text of a rewrite: headline, intro, then each section after a blank line.

    Steps sections number their sentences "1. ", "2. ". An attachment hint, when
    present, closes the text as its own paragraph.
    """
    blocks = ["\n".join([rewrite.headline, *(item.text for item in rewrite.intro)])]
    for section in rewrite.sections:
        lines = [
            f"{number}. {item.text}" if section.style == "steps" else item.text
            for number, item in enumerate(section.sentences, 1)
        ]
        blocks.append("\n".join([section.heading, *lines]))
    if rewrite.attachment_hint is not None:
        blocks.append(rewrite.attachment_hint)
    return "\n\n".join(blocks)


def _numbers(text: str) -> set[int]:
    # Compare values so "09:00" in a quote supports "9시" in the sentence.
    return {int(token) for token in _NUMBER_TOKEN.findall(text)}


def _display_texts(rewrite: EasyRewrite) -> Iterator[str]:
    yield rewrite.headline
    for section in rewrite.sections:
        yield section.heading
    for item in rewrite.sentences():
        yield item.text
    if rewrite.attachment_hint is not None:
        yield rewrite.attachment_hint


def validate_easy_rewrite(rewrite: EasyRewrite, body: str) -> None:
    """Reject the whole rewrite on any failure; sentences are never accepted one by one.

    Accepting a subset could silently drop a sentence that carries a condition.
    Each message is fixed so retry guidance never echoes model output.
    """
    whole_body = body.strip()
    for item in rewrite.sentences():
        for excerpt in item.evidence:
            if len(excerpt) < MIN_EVIDENCE_CHARACTERS and excerpt != whole_body:
                raise ValueError("근거 구절이 너무 짧습니다.")
            if not evidence_reference_valid(
                Evidence(field="notes", excerpt=excerpt, source_type="text"), sources=[body]
            ):
                raise ValueError("근거 문장이 원문에 그대로 있지 않습니다.")
        if not _numbers(item.text) <= _numbers(" ".join(item.evidence)):
            raise ValueError("근거에 없는 숫자가 문장에 있습니다.")
    body_numbers = _numbers(body)
    others = [rewrite.headline, *(section.heading for section in rewrite.sections)]
    if rewrite.attachment_hint is not None:
        others.append(rewrite.attachment_hint)
    if any(not _numbers(text) <= body_numbers for text in others):
        raise ValueError("근거에 없는 숫자가 문장에 있습니다.")
    for text in _display_texts(rewrite):
        unquoted = _QUOTED_SPAN.sub("", text)
        if any(symbol in unquoted for symbol in _FORBIDDEN_SYMBOLS):
            raise ValueError("쉬운말 문장에 쓰지 않는 기호가 있습니다.")
    if len(body) >= REWRITE_CHECK_MIN_BODY_CHARACTERS:
        rewritten = "\n".join(item.text for item in rewrite.sentences())
        if SequenceMatcher(None, rewritten, body).ratio() >= MAX_COPY_RATIO or any(
            len(item.text) >= MIN_COPIED_SENTENCE_CHARACTERS and item.text in body
            or any(
                SequenceMatcher(None, item.text, excerpt).ratio() >= MAX_SENTENCE_COPY_RATIO
                for excerpt in item.evidence
            )
            for item in rewrite.sentences()
        ):
            raise ValueError("원문을 거의 그대로 옮겼습니다.")
        if len(rewritten) > len(body):
            raise ValueError("다시 쓴 글이 원문보다 깁니다.")


class AppliedChange(BaseModel):
    """A validated replacement at half-open original Unicode code-point offsets."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    original: str = Field(min_length=1, max_length=MAX_TERM_CHARACTERS, strict=True)
    replacement: str = Field(min_length=1, max_length=MAX_TERM_CHARACTERS, strict=True)
    context: str = Field(min_length=1, max_length=MAX_SOURCE_CHARACTERS, strict=True)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        _validate_term(self.original, self.replacement)
        if self.start >= self.end or self.end - self.start != len(self.original):
            raise ValueError("용어의 원문 위치가 올바르지 않습니다.")
        return self


class DictionaryCandidate(ProposedDictionaryCandidate):
    """An attested candidate at half-open original Unicode code-point offsets."""

    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start >= self.end or self.end - self.start != len(self.original):
            raise ValueError("사전 후보의 원문 위치가 올바르지 않습니다.")
        return self


def _unique_start(text: str, excerpt: str) -> int:
    start = text.find(excerpt)
    if start == -1 or text.find(excerpt, start + 1) != -1:
        raise ValueError("원문과 문맥에 하나의 정확한 용어 위치가 필요합니다.")
    return start


def _is_word_character(character: str) -> bool:
    return unicodedata.category(character)[0] in {"L", "M", "N"} or (
        unicodedata.category(character) == "Pc"
    )


def _locate_change(
    text: str, proposed: ProposedChange, protected: tuple[tuple[int, int], ...]
) -> AppliedChange:
    context_start = _unique_start(text, proposed.context)
    start = context_start + _unique_start(proposed.context, proposed.original)
    end = start + len(proposed.original)
    if (
        start > 0
        and _is_word_character(proposed.original[0])
        and _is_word_character(text[start - 1])
        or end < len(text)
        and _is_word_character(proposed.original[-1])
        and _is_word_character(text[end])
    ):
        raise ValueError("단어의 일부만 떼어 바꿀 수 없습니다.")
    if any(
        start < protected_end and protected_start < end
        for protected_start, protected_end in protected
    ):
        raise ValueError("숫자·날짜·주소·연락처는 바꿀 수 없습니다.")
    return AppliedChange(
        start=start,
        end=end,
        original=proposed.original,
        replacement=proposed.replacement,
        context=proposed.context,
    )


def _protected_spans(text: str) -> tuple[tuple[int, int], ...]:
    return tuple(
        match.span() for pattern in _PROTECTED_PATTERNS for match in pattern.finditer(text)
    )


def _validate_change_order(changes: tuple[AppliedChange, ...]) -> None:
    for previous, current in zip(changes, changes[1:], strict=False):
        if previous.end > current.start:
            raise ValueError("중복되거나 겹치는 용어 위치를 바꿀 수 없습니다.")


def apply_easy_language_changes(original_text: str, changes: tuple[AppliedChange, ...]) -> str:
    """Apply only attested source slices; unchanged code points are copied exactly."""
    _validate_change_order(changes)
    segments: list[str] = []
    cursor = 0
    for change in changes:
        if original_text[change.start : change.end] != change.original:
            raise ValueError("바꿀 용어와 원문이 일치하지 않습니다.")
        segments.extend((original_text[cursor : change.start], change.replacement))
        cursor = change.end
    segments.append(original_text[cursor:])
    return "".join(segments)


def _resolve_changes(text: str, response: EasyLanguageResponse) -> tuple[AppliedChange, ...]:
    protected = _protected_spans(text)
    changes = tuple(
        sorted(
            (_locate_change(text, proposed, protected) for proposed in response.changes),
            key=lambda change: change.start,
        )
    )
    _validate_change_order(changes)
    return changes


def _body_text(text: str, title: str | None) -> tuple[str, int]:
    if title is None:
        return text, 0
    prefix = title + "\n"
    if not text.startswith(prefix) or not text[len(prefix) :].strip():
        raise ValueError("쉬운말 변환에는 제목과 구분된 본문이 필요합니다.")
    return text[len(prefix) :], len(prefix)


def _resolve_body_changes(
    text: str, response: EasyLanguageResponse, title: str | None
) -> tuple[AppliedChange, ...]:
    """Validate model excerpts inside the body, retaining complete-original offsets."""
    body, offset = _body_text(text, title)
    return tuple(
        change.model_copy(update={"start": change.start + offset, "end": change.end + offset})
        for change in _resolve_changes(body, response)
    )


def _resolve_dictionary_candidates(
    text: str, proposals: tuple[ProposedDictionaryCandidate, ...], title: str | None
) -> tuple[DictionaryCandidate, ...]:
    body, offset = _body_text(text, title)
    protected = _protected_spans(body)
    candidates: dict[tuple[int, int, str, str], DictionaryCandidate] = {}
    for proposed in proposals:
        start = _unique_start(body, proposed.context) + _unique_start(
            proposed.context, proposed.original
        )
        end = start + len(proposed.original)
        if (
            start > 0
            and _is_word_character(body[start - 1])
            or end < len(body)
            and _is_word_character(body[end])
        ):
            raise ValueError("사전 후보는 조사·어미를 포함한 원문 단어 전체여야 합니다.")
        if any(start < stop and begin < end for begin, stop in protected):
            raise ValueError("숫자·날짜·주소·연락처는 사전 후보가 될 수 없습니다.")
        candidate = DictionaryCandidate(
            **proposed.model_dump(), start=start + offset, end=end + offset
        )
        # Different exact contexts can attest the same occurrence. Keep one,
        # but never collapse separate occurrences of the same dictionary word.
        key = (candidate.start, candidate.end, candidate.original, candidate.query_word)
        candidates.setdefault(key, candidate)
    ordered = tuple(sorted(candidates.values(), key=lambda candidate: candidate.start))
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if previous.end > current.start:
            raise ValueError("사전 후보 위치가 겹치거나 같은 위치의 조회어가 충돌합니다.")
    return ordered


def _credential_forms(api_key: str) -> tuple[str, ...]:
    secret = api_key.strip()
    return tuple({secret, quote(secret, safe=""), quote_plus(secret)})


def _validate_raw_credentials(
    output: str, original_text: str, credentials: tuple[str, ...]
) -> None:
    if any(secret in output and secret not in original_text for secret in credentials):
        raise ValueError("Gemini response failed credential validation.")


def _validate_decoded_credentials(
    response: EasyLanguageResponse, original_text: str, credentials: tuple[str, ...]
) -> None:
    # Contexts may copy an existing source credential, but never add it as
    # replacement/lookup text or select it as a dictionary candidate itself.
    added = tuple(change.replacement for change in response.changes) + tuple(
        text
        for candidate in response.dictionary_candidates
        for text in (candidate.original, candidate.query_word)
    )
    copied = tuple(
        text
        for proposal in (*response.changes, *response.dictionary_candidates)
        for text in (proposal.original, proposal.context)
    )
    if any(secret in text for text in added for secret in credentials) or any(
        secret in text and secret not in original_text for text in copied for secret in credentials
    ):
        raise ValueError("Gemini response failed credential validation.")


def _validate_generated_credentials(
    easy_text: str, changes: tuple[AppliedChange, ...], credentials: tuple[str, ...]
) -> None:
    # Separate replacement fragments can join through untouched punctuation to
    # form a secret. Reject every credential occurrence touched by a replacement;
    # exact source copies remain valid even when preceding edits shift their offset.
    changed_spans: list[tuple[int, int]] = []
    offset = 0
    for change in changes:
        start = change.start + offset
        changed_spans.append((start, start + len(change.replacement)))
        offset += len(change.replacement) - (change.end - change.start)
    for secret in credentials:
        start = easy_text.find(secret)
        while start != -1:
            end = start + len(secret)
            if any(
                start < changed_end and changed_start < end
                for changed_start, changed_end in changed_spans
            ):
                raise ValueError("Gemini response failed credential validation.")
            start = easy_text.find(secret, start + 1)


class EasyLanguageResult(BaseModel):
    """A self-validating result that always keeps the complete, exact original."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    notice_id: int | None = Field(default=None, gt=0, strict=True)
    notice_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$", strict=True)
    original_text: str = Field(min_length=1, max_length=MAX_SOURCE_CHARACTERS, strict=True)
    # Derived from the DB source, never from Gemini. Not a separate DB column.
    original_title: str | None = Field(default=None, strict=True)
    easy_text: str = Field(min_length=1, strict=True)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    model: str = Field(min_length=1, strict=True)
    prompt_version: str = Field(min_length=1, strict=True)
    generated_at: AwareDatetime
    # Legacy replacement generation only. Rewrite results keep an empty tuple.
    changes: tuple[AppliedChange, ...]
    # None is a legacy replacement result; otherwise easy_text is the title, a line
    # break and flatten_easy_rewrite(easy_result).
    easy_result: EasyRewrite | None = None
    # None is legacy/unknown; an empty tuple means extraction completed with no candidates.
    dictionary_candidates: tuple[DictionaryCandidate, ...] | None = None
    attempt_count: int = Field(ge=1, le=2, strict=True)
    # None means provenance is unknown, not that a body/attachment was absent.
    # False for attachment content does not assert that the notice has files.
    body_text_present: bool | None = Field(default=None, strict=True)
    attachment_content_included: bool | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def validate_original_and_changes(self) -> Self:
        if (self.body_text_present is None) != (self.attachment_content_included is None):
            raise ValueError("처리 범위 정보는 함께 확인되거나 함께 알 수 없어야 합니다.")
        source = NoticeGlossaryInput(
            notice_id=self.notice_id, notice_revision=self.notice_revision, text=self.original_text
        )
        if not self.model.strip() or not self.prompt_version.strip():
            raise ValueError("모델과 프롬프트 버전이 필요합니다.")
        if self.source_hash != source_hash(source):
            raise ValueError("보관한 원문과 원문 해시가 일치하지 않습니다.")
        if self.easy_result is not None:
            if self.changes:
                raise ValueError("다시 쓴 결과에는 단어 치환 목록이 없어야 합니다.")
            body, _ = _body_text(source.text, self.original_title)
            validate_easy_rewrite(self.easy_result, body)
            prefix = "" if self.original_title is None else self.original_title + "\n"
            if self.easy_text != prefix + flatten_easy_rewrite(self.easy_result):
                raise ValueError("보관한 재작성 결과로 쉬운 공지를 재현할 수 없습니다.")
        else:
            self._validate_legacy_changes(source)
        if self.dictionary_candidates is not None:
            candidates = _resolve_dictionary_candidates(
                source.text,
                tuple(
                    ProposedDictionaryCandidate(
                        original=candidate.original,
                        query_word=candidate.query_word,
                        context=candidate.context,
                    )
                    for candidate in self.dictionary_candidates
                ),
                self.original_title,
            )
            if self.dictionary_candidates != candidates:
                raise ValueError("보관한 사전 후보와 원문 위치가 일치하지 않습니다.")
        return self

    def _validate_legacy_changes(self, source: NoticeGlossaryInput) -> None:
        resolved = _resolve_body_changes(
            source.text,
            EasyLanguageResponse(
                changes=tuple(
                    ProposedChange(
                        original=change.original,
                        replacement=change.replacement,
                        context=change.context,
                    )
                    for change in self.changes
                ),
                dictionary_candidates=(),
            ),
            self.original_title,
        )
        if (
            self.changes != resolved
            or apply_easy_language_changes(source.text, resolved) != self.easy_text
        ):
            raise ValueError("보관한 변경 목록으로 쉬운 공지를 재현할 수 없습니다.")


def _validate_rewrite_credentials(
    response: EasyRewriteResponse, body: str, credentials: tuple[str, ...]
) -> None:
    # Quotes may copy a credential that the body already contains; written text
    # and lookup words may never contain one.
    written = list(_display_texts(response.rewrite)) + [
        text
        for candidate in response.dictionary_candidates
        for text in (candidate.original, candidate.query_word)
    ]
    copied = [text for item in response.rewrite.sentences() for text in item.evidence] + [
        candidate.context for candidate in response.dictionary_candidates
    ]
    if any(secret in text for text in written for secret in credentials) or any(
        secret in text and secret not in body for text in copied for secret in credentials
    ):
        raise ValueError("Gemini response failed credential validation.")
    if any(secret in flatten_easy_rewrite(response.rewrite) for secret in credentials):
        raise ValueError("Gemini response failed credential validation.")


_RETRY_GENERAL = (
    "\n\n이전 응답은 JSON 형식 또는 원문 검사에 실패했습니다. 같은 전체 원문을 다시 읽고 "
    "rewrite와 dictionary_candidates를 모두 반환하세요. 모든 문장의 evidence에는 원문에서 "
    "글자를 그대로 복사한 구절을 넣고, 원문에 없는 내용이나 예시의 내용을 넣지 마세요."
)
# Fixed local hints keyed by our own validation messages; never model output.
_RETRY_HINTS = {
    "근거 문장이 원문에 그대로 있지 않습니다.": (
        " evidence 중 원문과 글자가 다른 구절이 있었습니다. 공백, 기호, 줄바꿈까지 원문을 "
        "그대로 복사하고 한 구절은 120자 이하로 자르세요."
    ),
    "근거에 없는 숫자가 문장에 있습니다.": (
        " 문장에 그 문장의 evidence에 없는 숫자가 있었습니다. 날짜, 금액, 인원 같은 숫자는 "
        "그 숫자가 들어 있는 원문 구절을 evidence에 넣고, 원문에 없는 숫자는 쓰지 마세요."
    ),
    "쉬운말 문장에 쓰지 않는 기호가 있습니다.": (
        " 문장에 「」, ·, *, ※ 기호가 있었습니다. 이 기호는 쓰지 말고, 공식 이름에 꼭 필요하면 "
        "그 이름 전체를 작은따옴표로 감싸세요."
    ),
    "근거 구절이 너무 짧습니다.": (
        " evidence에 5자보다 짧은 구절이 있었습니다. 숫자나 한 단어만 넣지 말고 "
        "그 문장의 근거가 되는 원문 구절을 5자 이상 그대로 복사하세요."
    ),
    "원문을 거의 그대로 옮겼습니다.": (
        " 문장이 원문을 거의 그대로 옮겨 실패했습니다. 원문 문장을 복사하지 말고 "
        "짧고 쉬운 문장으로 다시 쓰세요. 원문 그대로의 글은 evidence에만 넣으세요."
    ),
    "다시 쓴 글이 원문보다 깁니다.": (
        " 다시 쓴 문장 전체가 원문보다 길었습니다. 같은 내용을 반복하지 말고 "
        "핵심 조건만 짧게 쓰세요."
    ),
}


def _retry_guidance(error: Exception) -> str:
    if isinstance(error, ValidationError):
        details = error.errors(include_input=False)
        messages = [str(detail.get("ctx", {}).get("error", "")) for detail in details]
    else:
        messages = [str(error)]
    return _RETRY_GENERAL + "".join(
        dict.fromkeys(_RETRY_HINTS[message] for message in messages if message in _RETRY_HINTS)
    )


def load_easy_language_prompt() -> str:
    """Load the versioned instructions without altering their contents."""
    try:
        prompt = PROMPT_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        raise EasyLanguageConfigurationError(
            "Gemini easy-language prompt cannot be loaded."
        ) from None
    if not prompt.strip():
        raise EasyLanguageConfigurationError("Gemini easy-language prompt is empty.")
    return prompt


def simplify_notice(
    source: NoticeGlossaryInput,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    request: Callable[..., str] | None = None,
    clock: Callable[[], datetime] | None = None,
    budget: ExecutionBudget | None = None,
) -> EasyLanguageResult:
    """Share one execution deadline across generation, validation and correction."""
    with execution_budget(budget) as active:
        try:
            result = _simplify_notice(
                source, api_key=api_key, model=model, request=request, clock=clock
            )
            active.check()
            return result
        except GeminiExecutionError as error:
            # Preserve scheduling metadata, but never an injected client's message.
            safe_error = EasyLanguageAPIError(
                "Gemini easy-language request failed.",
                reason_code=error.reason_code,
                failure_kind=error.failure_kind,
                retryable=error.retryable,
                retry_at=error.retry_at,
                status_code=error.status_code,
            )
            active.last_failure = safe_error
            raise safe_error from None


def _simplify_notice(
    source: NoticeGlossaryInput,
    *,
    api_key: str,
    model: str,
    request: Callable[..., str] | None,
    clock: Callable[[], datetime] | None,
) -> EasyLanguageResult:
    """Retry an invalid rewrite once; API failures never become successful empty work.

    DB inputs send the same body on both attempts; the title stays outside Gemini.
    Direct text inputs have no known title boundary and are treated as body text.
    Local validation checks quoted evidence, numbers, symbols and copying, but
    cannot prove that a rewritten sentence keeps the source's meaning.
    """
    if not isinstance(source, NoticeGlossaryInput):
        raise EasyLanguageConfigurationError("A validated notice source is required.")
    if isinstance(source, StoredNoticeInput) and not source.body_text_present:
        raise NoNoticeBodyError("변환할 본문 텍스트가 없습니다.")
    title = source.title if isinstance(source, StoredNoticeInput) else None
    request_text = (
        source.text[source.body_start :] if isinstance(source, StoredNoticeInput) else source.text
    )
    if not isinstance(api_key, str) or not api_key.strip():
        raise EasyLanguageConfigurationError("GEMINI_API_KEY is required.")
    if not isinstance(model, str) or not model.strip():
        raise EasyLanguageConfigurationError("Gemini model is required.")
    if request is None:
        from pipeline.glossary.easy_language_client import generate_easy_language_json

        request = generate_easy_language_json
    prompt = load_easy_language_prompt()
    credentials = _credential_forms(api_key)
    for attempt in (1, 2):
        try:
            active = current_execution()
            assert active is not None
            active.check()
            output = request(prompt=prompt, notice_text=request_text, api_key=api_key, model=model)
            active.check()
        except EasyLanguageConfigurationError:
            raise EasyLanguageConfigurationError(
                "Gemini easy-language configuration failed."
            ) from None
        except GeminiExecutionError:
            raise
        except Exception:
            # This is an external request boundary, including injected SDK/network clients.
            # No arbitrary exception strings, response bodies or keys enter public errors.
            raise EasyLanguageAPIError("Gemini easy-language request failed.") from None
        try:
            if not isinstance(output, str):
                raise ValueError("Gemini JSON must be text.")
            _validate_raw_credentials(output, request_text, credentials)
            response = EasyRewriteResponse.model_validate_json(output)
            _validate_rewrite_credentials(response, request_text, credentials)
            validate_easy_rewrite(response.rewrite, request_text)
            dictionary_candidates = _resolve_dictionary_candidates(
                source.text, response.dictionary_candidates, title
            )
        except (ValidationError, ValueError) as error:
            active.check()
            if attempt == 2:
                raise EasyLanguageValidationError(
                    "Gemini easy-language JSON or source validation failed after two attempts."
                ) from None
            prompt += _retry_guidance(error)
            continue
        prefix = "" if title is None else title + "\n"
        return EasyLanguageResult(
            notice_id=source.notice_id,
            notice_revision=source.notice_revision,
            original_text=source.text,
            original_title=title,
            easy_text=prefix + flatten_easy_rewrite(response.rewrite),
            source_hash=source_hash(source),
            model=model,
            prompt_version=PROMPT_VERSION,
            generated_at=clock() if clock else datetime.now(UTC),
            changes=(),
            easy_result=response.rewrite,
            dictionary_candidates=dictionary_candidates,
            attempt_count=attempt,
            body_text_present=source.body_text_present
            if isinstance(source, StoredNoticeInput)
            else None,
            attachment_content_included=False if isinstance(source, StoredNoticeInput) else None,
        )
    raise AssertionError("Unreachable easy-language attempt state.")
