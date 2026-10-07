"""Apply contextual easy-language swaps while retaining the exact notice."""

import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Self
from urllib.parse import quote, quote_plus

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from pipeline.glossary.source import (
    MAX_SOURCE_CHARACTERS,
    NoticeGlossaryInput,
    StoredNoticeInput,
    source_hash,
)

DEFAULT_MODEL = "gemini-3.5-flash-lite"
PROMPT_VERSION = "easy-language-v7"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "gemini_easy_language.md"
MAX_TERM_CHARACTERS = 100

# These recognize literal data formats, rather than guessing a Korean sentence's meaning.
_PROTECTED_PATTERNS = (
    re.compile(r"(?:https?://|www\.)[^\s<>]+", re.IGNORECASE),
    re.compile(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+"),
    re.compile(r"\d+(?:[.,:/~\-–]\d+)*(?:\s*[년월일시분초])?"),
    re.compile(r"[월화수목금토일]요일"),
)


class EasyLanguageConfigurationError(ValueError):
    """The local source, prompt or Gemini settings cannot be used."""


class EasyLanguageAPIError(RuntimeError):
    """Gemini failed; remote details and credentials are deliberately excluded."""


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


class EasyLanguageResponse(BaseModel):
    """Require contextual replacement proposals, possibly empty."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    changes: tuple[ProposedChange, ...]


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


def _resolve_body_changes(
    text: str, response: EasyLanguageResponse, title: str | None
) -> tuple[AppliedChange, ...]:
    """Validate model excerpts inside the body, retaining complete-original offsets."""
    if title is None:
        return _resolve_changes(text, response)
    prefix = title + "\n"
    if not text.startswith(prefix) or not text[len(prefix) :].strip():
        raise ValueError("쉬운말 변환에는 제목과 구분된 본문이 필요합니다.")
    return tuple(
        change.model_copy(
            update={"start": change.start + len(prefix), "end": change.end + len(prefix)}
        )
        for change in _resolve_changes(text[len(prefix) :], response)
    )


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
    # Original/context are later attested source copies, so an existing source
    # credential may remain there. Never add it as replacement text.
    added = tuple(change.replacement for change in response.changes)
    copied = tuple(
        text for change in response.changes for text in (change.original, change.context)
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
    changes: tuple[AppliedChange, ...]
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
            ),
            self.original_title,
        )
        if (
            self.changes != resolved
            or apply_easy_language_changes(source.text, resolved) != self.easy_text
        ):
            raise ValueError("보관한 변경 목록으로 쉬운 공지를 재현할 수 없습니다.")
        return self


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
) -> EasyLanguageResult:
    """Retry invalid JSON/spans once; API failures never become successful empty work.

    DB inputs send the same body on both attempts; the title stays outside Gemini.
    Direct text inputs have no known title boundary and are treated as body text. The model
    chooses contextual equivalents; local validation checks literal spans and
    protected formats, but cannot prove that two Korean expressions mean the same.
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
            output = request(prompt=prompt, notice_text=request_text, api_key=api_key, model=model)
        except EasyLanguageConfigurationError:
            raise EasyLanguageConfigurationError(
                "Gemini easy-language configuration failed."
            ) from None
        except Exception:
            # This is an external request boundary, including injected SDK/network clients.
            # No arbitrary exception strings, response bodies or keys enter public errors.
            raise EasyLanguageAPIError("Gemini easy-language request failed.") from None
        try:
            if not isinstance(output, str):
                raise ValueError("Gemini JSON must be text.")
            _validate_raw_credentials(output, request_text, credentials)
            response = EasyLanguageResponse.model_validate_json(output)
            _validate_decoded_credentials(response, request_text, credentials)
            changes = _resolve_body_changes(source.text, response, title)
            easy_text = apply_easy_language_changes(source.text, changes)
            _validate_generated_credentials(easy_text, changes, credentials)
        except (ValidationError, ValueError) as error:
            if attempt == 2:
                raise EasyLanguageValidationError(
                    "Gemini easy-language JSON or source validation failed after two attempts."
                ) from None
            unchanged_term = isinstance(error, ValidationError) and any(
                str(detail.get("ctx", {}).get("error", ""))
                in {
                    "바꿀 말은 원래 용어와 달라야 합니다.",
                    "띄어쓰기만 바꾼 항목은 쉬운말 변경이 아닙니다.",
                }
                for detail in error.errors(include_input=False)
            )
            prompt += (
                "\n\n이전 응답은 JSON 형식 또는 원문 위치 검사에 실패했습니다. "
                "같은 전체 원문을 다시 읽고, 정확히 복사한 문맥과 용어만 반환하세요. "
                "changes만 반환하세요. 원문에 없는 말이나 예시의 용어를 넣지 마세요. "
                "확신할 수 없는 변경은 제외하고, 앞뒤 문맥에 맞는 조사·어미를 포함한 "
                "최소 구간을 고르세요. 바꾼 문장을 실제로 이어 읽어 확인하세요."
            )
            if unchanged_term:
                # Send a fixed local hint, never model output or exception details.
                prompt += (
                    " original과 replacement가 같거나 띄어쓰기만 다른 항목이 있어 "
                    "실패했습니다. 이 항목은 제외하고, 실제로 쉬워진 다른 변경은 유지하세요."
                )
            if isinstance(error, ValidationError) and any(
                str(detail.get("ctx", {}).get("error", ""))
                == "용어에는 글자와 일반 공백만 사용할 수 있습니다."
                for detail in error.errors(include_input=False)
            ):
                prompt += (
                    " 교체 구간에 문장부호·특수 공백이 들어 있어 실패했습니다. "
                    "괄호·가운뎃점·줄바꿈은 context에만 복사하고 original/replacement에서는 "
                    "빼세요. 실제 원문에서 해당 부호를 포함하지 않는 더 짧은 구간을 "
                    "새로 고르세요. 원문 문자열 자체를 고쳐 복사하지 마세요."
                )
            if not isinstance(error, ValidationError):
                # Explain a known local source failure without echoing its payload.
                prompt += {
                    "단어의 일부만 떼어 바꿀 수 없습니다.": (
                        " 이전 응답의 original이 원문 단어의 일부만 포함해 실패했습니다. "
                        "원문에 붙어 있는 조사·어미·복합어 전체를 original에 포함하세요. "
                        "예를 들어 원문이 공종을이면 original은 공종을, "
                        "replacement는 공사 종류를입니다. 공종만 반환하지 마세요."
                    ),
                    "원문과 문맥에 하나의 정확한 용어 위치가 필요합니다.": (
                        " 이전 응답의 original 또는 context를 원문에서 한 곳으로 "
                        "확정하지 못했습니다. 실제 입력에서 공백과 줄바꿈까지 그대로 "
                        "복사하고, 원문에 없는 말이나 예시의 용어는 제외하세요. "
                        "context 안에는 original이 정확히 한 번 있어야 합니다."
                    ),
                }.get(str(error), "")
            continue
        return EasyLanguageResult(
            notice_id=source.notice_id,
            notice_revision=source.notice_revision,
            original_text=source.text,
            original_title=title,
            easy_text=easy_text,
            source_hash=source_hash(source),
            model=model,
            prompt_version=PROMPT_VERSION,
            generated_at=clock() if clock else datetime.now(UTC),
            changes=changes,
            attempt_count=attempt,
            body_text_present=source.body_text_present
            if isinstance(source, StoredNoticeInput)
            else None,
            attachment_content_included=False if isinstance(source, StoredNoticeInput) else None,
        )
    raise AssertionError("Unreachable easy-language attempt state.")
