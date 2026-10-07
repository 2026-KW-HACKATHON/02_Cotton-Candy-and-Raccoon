"""Locate field-level text evidence without changing claims or verification.

Offsets refer to the returned plain text, in JavaScript UTF-16 code units with
exclusive ends. They cannot be applied to HTML, PDF pages, or different text.
Whitespace-only matching is a location aid, never a grounding promotion.
"""

import hashlib
from typing import Literal

from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_cards import CardKey, CardModel, FieldEvidence, summary_snapshot
from pipeline.transform.summary_schema import NoticeSummary

CARD_EVIDENCE_FIELDS = {
    "audience": frozenset({"audience", "audience_scope"}),
    "deadline": frozenset({"dates", "status", "status_detail", "notice_update", "changed_details"}),
    "action": frozenset({"action", "action_requirement", "location"}),
    "notes": frozenset({"notes", "notice_update", "changed_details", "status_detail", "status"}),
}


class HighlightSource(CardModel):
    key: str
    label: str
    text: str
    sha256: str


class TextRange(CardModel):
    source_key: str
    start: int
    end: int
    text: str


class LocatedEvidence(CardModel):
    reference: FieldEvidence
    status: Literal["matched", "ambiguous", "not_found", "file_reference", "invalid_reference"]
    match_kind: Literal["exact", "whitespace_normalized"] | None
    candidates: list[TextRange]


class CardTextHighlights(CardModel):
    key: CardKey
    status: Literal["ready", "partial", "ambiguous", "not_found", "no_evidence", "file_only"]
    guidance: str | None
    # Only uniquely located references are highlighted automatically. Ambiguous
    # candidates remain in references so a client can present a location choice.
    ranges: list[TextRange]
    references: list[LocatedEvidence]


class HighlightCardSet(CardModel):
    audience: CardTextHighlights
    deadline: CardTextHighlights
    action: CardTextHighlights
    notes: CardTextHighlights


class SummaryTextHighlights(CardModel):
    offset_unit: Literal["utf16"] = "utf16"
    sources: list[HighlightSource]
    cards: HighlightCardSet


def _utf16_prefix(text: str) -> list[int]:
    offsets = [0]
    for char in text:
        offsets.append(offsets[-1] + (2 if ord(char) > 0xFFFF else 1))
    return offsets


def _whitespace_view(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Collapse whitespace runs, retaining each normalized character's origin.

    Do not remove word separators or alter case, punctuation, digits or Unicode
    composition. An unmatched near-quote must never point to a different claim.
    """
    chars = []
    origins = []
    for index, char in enumerate(text):
        if char.isspace():
            if chars and chars[-1] == " ":
                origins[-1] = (origins[-1][0], index + 1)
            else:
                chars.append(" ")
                origins.append((index, index + 1))
        else:
            chars.append(char)
            origins.append((index, index + 1))
    return "".join(chars), origins


def _occurrences(text: str, quote: str) -> list[tuple[int, int]]:
    found = []
    start = 0
    while quote:
        index = text.find(quote, start)
        if index < 0:
            break
        found.append((index, index + len(quote)))
        start = index + 1
    return found


def _ranges(
    quote: str,
    sources: list[HighlightSource],
    prefixes: dict[str, list[int]],
    *,
    normalized: bool,
) -> list[TextRange]:
    matches = []
    for source in sources:
        if normalized:
            text, origins = _whitespace_view(source.text)
            target, _ = _whitespace_view(quote)
            target = target.strip()
            locations = [
                (origins[start][0], origins[end - 1][1])
                for start, end in _occurrences(text, target)
            ]
        else:
            locations = _occurrences(source.text, quote)
        for start, end in locations:
            matches.append(TextRange(
                source_key=source.key,
                start=prefixes[source.key][start],
                end=prefixes[source.key][end],
                text=source.text[start:end],
            ))
    return matches


def _locate(
    reference: FieldEvidence,
    sources: list[HighlightSource],
    prefixes: dict[str, list[int]],
) -> LocatedEvidence:
    evidence = reference.evidence
    if evidence.source_type != "text":
        return LocatedEvidence(
            reference=reference, status="file_reference", match_kind=None, candidates=[]
        )
    if evidence.source_id is not None or evidence.page is not None:
        return LocatedEvidence(
            reference=reference, status="invalid_reference", match_kind=None, candidates=[]
        )
    matches = _ranges(evidence.excerpt, sources, prefixes, normalized=False)
    kind = "exact" if matches else "whitespace_normalized"
    if not matches:
        matches = _ranges(evidence.excerpt, sources, prefixes, normalized=True)
    return LocatedEvidence(
        reference=reference,
        status=("matched" if len(matches) == 1 else "ambiguous" if matches else "not_found"),
        match_kind=kind if matches else None,
        candidates=matches,
    )


def _card(key: CardKey, references: list[LocatedEvidence]) -> CardTextHighlights:
    selected = [
        item for item in references if item.reference.evidence.field in CARD_EVIDENCE_FIELDS[key]
    ]
    unique = {
        (span.source_key, span.start, span.end): span
        for item in selected if item.status == "matched"
        for span in item.candidates
    }
    ranges = sorted(unique.values(), key=lambda span: (span.source_key, span.start, span.end))
    if ranges:
        status = "ready" if all(item.status == "matched" for item in selected) else "partial"
    elif any(item.status == "ambiguous" for item in selected):
        status = "ambiguous"
    elif not selected:
        status = "no_evidence"
    elif all(item.status == "file_reference" for item in selected):
        status = "file_only"
    else:
        status = "not_found"
    guidance = {
        "ready": None,
        "partial": "찾은 텍스트 근거를 표시해요. 나머지는 원문을 확인해 주세요",
        "ambiguous": "같은 근거가 여러 위치에 있어요. 원문을 확인해 주세요",
        "no_evidence": "원문을 확인해 주세요",
        "file_only": "파일 근거는 첨부 원문을 확인해 주세요",
        "not_found": "현재 원문에서 근거 문구를 찾지 못했어요. 원문을 확인해 주세요",
    }[status]
    return CardTextHighlights(
        key=key, status=status, guidance=guidance, ranges=ranges, references=selected
    )


def build_summary_text_highlights(
    summary: NoticeSummary, notice: NoticeInput
) -> SummaryTextHighlights:
    """Map original field evidence to the supplied, unchanged plain-text sources.

    Ranges locate quotations, not the meaning of AI card prose. Array references
    remain field-scoped. Preserve every candidate for duplicate quotations and
    never automatically pick a first occurrence. PDF/image references are left
    as file references even if their quoted words also occur in the body.
    Metadata titles are not body text. Callers must obtain the summary and notice
    from the same public row and render these returned sources, not raw HTML.
    Attachment labels use public ordinals, never internal file names that may
    contain masked or otherwise restricted metadata.
    """
    checked = summary_snapshot(summary)
    # Revalidate mutable caller data without changing its source strings.
    if not isinstance(notice, NoticeInput):
        raise TypeError("notice must be a NoticeInput")
    source_notice = NoticeInput.model_validate(notice.model_dump(mode="json"))
    source_values = [("body_text", "본문", source_notice.body_text)] + [
        (f"attachments[{index}].text", f"첨부 텍스트 {index + 1}", attachment.text)
        for index, attachment in enumerate(source_notice.attachments)
    ]
    sources = [
        HighlightSource(
            key=key, label=label, text=text,
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        for key, label, text in source_values if text
    ]
    prefixes = {source.key: _utf16_prefix(source.text) for source in sources}
    references = [
        _locate(
            FieldEvidence(source_path=f"evidence[{index}]", evidence=item.model_copy(deep=True)),
            sources, prefixes,
        )
        for index, item in enumerate(checked.evidence)
    ]
    return SummaryTextHighlights(
        sources=sources,
        cards=HighlightCardSet(**{key: _card(key, references) for key in CARD_EVIDENCE_FIELDS}),
    )
