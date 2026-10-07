"""Preserve exact notice text and fingerprint its collected source revision."""

import hashlib
import json
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_SOURCE_CHARACTERS = 100_000


class NoticeGlossaryInput(BaseModel):
    """Plain text for one notice; never strip or normalize the source.

    Character limits and replacement offsets count Python Unicode code
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


class StoredNoticeInput(NoticeGlossaryInput):
    """DB-derived title/body input; this metadata is not accepted by the JSON CLI."""

    notice_id: int = Field(gt=0, strict=True)
    notice_revision: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    body_text_present: bool = Field(strict=True)


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
