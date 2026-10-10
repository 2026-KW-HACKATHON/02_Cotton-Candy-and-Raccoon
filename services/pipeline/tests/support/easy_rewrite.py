"""Gemini rewrite responses and legacy replacement results for easy-text tests."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from pipeline.glossary import easy_language
from pipeline.glossary.easy_language import (
    DEFAULT_MODEL,
    EasyLanguageResponse,
    EasyLanguageResult,
    ProposedChange,
    ProposedDictionaryCandidate,
    apply_easy_language_changes,
)
from pipeline.glossary.source import NoticeGlossaryInput, source_hash

__all__ = [
    "LEGACY_PROMPT_VERSION",
    "legacy_result",
    "rewrite",
    "rewrite_request",
    "rewrite_response",
    "sentence",
]

LEGACY_PROMPT_VERSION = "easy-language-v8"
_GENERATED_AT = datetime(2026, 10, 7, 1, 2, 3, tzinfo=UTC)


def sentence(text: str, *evidence: str) -> dict[str, object]:
    return {"text": text, "evidence": list(evidence)}


def rewrite(
    body: str,
    *,
    headline: str = "공지 내용을 알려 드려요.",
    intro: Sequence[dict[str, object]] = (),
    sections: Sequence[dict[str, object]] | None = None,
    attachment_hint: str | None = None,
) -> dict[str, object]:
    """A rewrite that passes local validation for any body unless overridden."""
    if sections is None:
        # Transport tests use long repeated bodies; one exact quote covers repeats.
        lines = list(dict.fromkeys(line.strip() for line in body.splitlines() if line.strip()))
        sentences = [sentence(line, line) for line in lines]
        sections = [{
            "heading": "무엇을 알려 주나요?",
            "style": "paragraph",
            "sentences": sentences[offset:offset + 8],
        } for offset in range(0, len(sentences), 8)]
    return {
        "headline": headline,
        "intro": list(intro),
        "sections": list(sections),
        "attachment_hint": attachment_hint,
    }


def rewrite_response(
    body: str,
    *,
    candidates: Sequence[dict[str, object]] = (),
    rewrite_payload: dict[str, object] | None = None,
) -> str:
    return json.dumps(
        {
            "rewrite": rewrite_payload if rewrite_payload is not None else rewrite(body),
            "dictionary_candidates": list(candidates),
        },
        ensure_ascii=False,
    )


def rewrite_request(**kwargs: str) -> str:
    """A request stub that rewrites whatever body the service sends."""
    return rewrite_response(kwargs["notice_text"])


def legacy_result(
    text: str,
    *changes: dict[str, str],
    candidates: Sequence[dict[str, str]] = (),
    title: str | None = None,
    notice_id: int | None = None,
    notice_revision: str | None = None,
    prompt_version: str = LEGACY_PROMPT_VERSION,
    generated_at: datetime = _GENERATED_AT,
    **extra: object,
) -> EasyLanguageResult:
    """A stored replacement-generation result, built without the removed generation path."""
    response = EasyLanguageResponse(
        changes=tuple(ProposedChange(**change) for change in changes),
        dictionary_candidates=tuple(
            ProposedDictionaryCandidate(**candidate) for candidate in candidates
        ),
    )
    applied = easy_language._resolve_body_changes(text, response, title)
    resolved = easy_language._resolve_dictionary_candidates(
        text, response.dictionary_candidates, title
    )
    NoticeGlossaryInput(notice_id=notice_id, notice_revision=notice_revision, text=text)
    return EasyLanguageResult.model_validate({
        "notice_id": notice_id,
        "notice_revision": notice_revision,
        "original_text": text,
        "original_title": title,
        "easy_text": apply_easy_language_changes(text, applied),
        "source_hash": source_hash(text),
        "model": DEFAULT_MODEL,
        "prompt_version": prompt_version,
        "generated_at": generated_at,
        "changes": [change.model_dump() for change in applied],
        "dictionary_candidates": [candidate.model_dump() for candidate in resolved],
        "attempt_count": 1,
        **extra,
    })
