"""Minimal rewrite guards; no claim of complete coverage or semantic equivalence."""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline.glossary.easy_language import EasyRewrite

_NUMBER = re.compile(r"\d+(?:,\d{3})*")
_LINK = re.compile(r"https?://[^\s<>()[\]{}]+|www\.[^\s<>()[\]{}]+", re.IGNORECASE)


def _numbers(text: str) -> set[int]:
    # Compare components, not units/order/occurrence counts. 09:00 and 9시 are allowed.
    return {int(m.group().replace(",", "")) for m in _NUMBER.finditer(text)}


def _links(text: str) -> set[str]:
    return {m.group().rstrip(".,;!?") for m in _LINK.finditer(text)}


def validate_minimal_rewrite(rewrite: "EasyRewrite", body: str) -> None:
    """Keep source quotes and reject newly invented numbers/links only.

    No whole-body character coverage, ordering, unit, date-meaning, or omission
    check. A pass does not establish semantic fidelity. Dictionary candidates
    retain their independent position and query validation in easy_language.
    """
    for sentence in rewrite.sentences():
        for quote in sentence.evidence:
            if quote not in body:
                raise ValueError("근거 문장이 원문에 그대로 있지 않습니다.")
    output = "\n".join([
        rewrite.headline, *(section.heading for section in rewrite.sections),
        *(sentence.text for sentence in rewrite.sentences()),
    ])
    if _numbers(output) - _numbers(body):
        raise ValueError("원문에 없는 숫자가 있습니다.")
    if _links(output) - _links(body):
        raise ValueError("원문과 다른 URL이 있습니다.")
    if rewrite.attachment_hint is not None and rewrite.attachment_hint not in body:
        raise ValueError("첨부 안내는 원문에서 그대로 인용하거나 null이어야 합니다.")
