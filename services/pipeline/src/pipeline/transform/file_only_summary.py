"""Preserve visual-only summaries while checking shape, order, and provenance."""

from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_schema import (
    FIELD_TEXT_LIMITS,
    DateEntry,
    MediaSource,
    NoticeSummary,
    evidence_reference_valid,
)

REVIEW_NOTE = "원문 확인 필요"


def is_file_only_notice(notice: NoticeInput, media_sources: tuple[MediaSource, ...]) -> bool:
    """Metadata alone does not turn a visual-only notice into a mixed input."""
    return bool(media_sources) and not notice.body_text.strip() and not notice.attachments


def _metadata_sources(notice: NoticeInput) -> list[str]:
    """Only literal metadata supplied by the caller can match a text quote."""
    return [value for value in (notice.title, notice.publisher, notice.department) if value]


def reversed_end_fields(entry: DateEntry) -> tuple[str, ...]:
    """Compare dates before clocks, allowing a next-day end with an earlier clock."""
    if entry.start_date and entry.end_date:
        if entry.start_date > entry.end_date:
            return ("end_date", "end_time") if entry.end_time else ("end_date",)
        if entry.start_date < entry.end_date:
            return ()
    if entry.start_time and entry.end_time and entry.start_time > entry.end_time:
        return ("end_time",)
    return ()


def file_reference_problems(
    summary: NoticeSummary, notice: NoticeInput, media_sources: tuple[MediaSource, ...]
) -> tuple[str, ...]:
    """Return safe field paths for one retry, without claiming to inspect pixels."""
    sources = _metadata_sources(notice)
    problems = []
    cited_fields = set()
    for index, item in enumerate(summary.evidence):
        if item.field == "publisher" and notice.publisher:
            continue
        if getattr(summary, item.field) in (None, []):
            problems.append(f"evidence.{index}.field: empty_field")
        elif not evidence_reference_valid(item, sources=sources, media_sources=media_sources):
            problems.append(f"evidence.{index}: invalid_file_or_text_reference")
        else:
            cited_fields.add(item.field)
    required = {"summary"} if summary.summary != REVIEW_NOTE else set()
    for field in ("applicable_area", "audience", "action", "location", "dates", "notes", "topics"):
        if getattr(summary, field) not in (None, []):
            required.add(field)
    problems.extend(
        f"evidence.{field}: missing_reference" for field in sorted(required - cited_fields)
    )
    for index, entry in enumerate(summary.dates):
        problems.extend(
            f"dates.{index}.{field}: reversed_order" for field in reversed_end_fields(entry)
        )
    return tuple(problems)


def preserve_file_only_summary(
    summary: NoticeSummary, notice: NoticeInput, media_sources: tuple[MediaSource, ...]
) -> NoticeSummary:
    """Keep model claims; mark references honestly and repair impossible end ordering.

    File quotes and page counts are not compared with binary contents here. A
    missing reference remains unverified, never inferred from the available file.
    The caller owns the one-retry budget; this function performs no API calls.
    """
    problems = file_reference_problems(summary, notice, media_sources)
    data = summary.model_dump()
    sources = _metadata_sources(notice)
    evidence = []
    for item in summary.evidence:
        if getattr(summary, item.field) in (None, []):
            continue
        if item.field == "publisher" and notice.publisher:
            continue
        valid = evidence_reference_valid(item, sources=sources, media_sources=media_sources)
        verification = (
            ("text_matched" if item.source_type == "text" else "file_reference_only")
            if valid
            else None
        )
        evidence.append(item.model_copy(update={"verification": verification}).model_dump())
    data["evidence"] = evidence
    if notice.publisher and len(notice.publisher) <= FIELD_TEXT_LIMITS["publisher"]:
        data["publisher"] = notice.publisher
    reversed_order = False
    for index, entry in enumerate(summary.dates):
        for field in reversed_end_fields(entry):
            data["dates"][index][field] = None
            reversed_order = True
    if reversed_order:
        data["status"] = "unknown"
        data["status_detail"] = None
        data["evidence"] = [
            item for item in data["evidence"] if item["field"] not in ("status", "status_detail")
        ]
    if problems or summary.summary == REVIEW_NOTE:
        data["uncertainties"] = list(dict.fromkeys([*summary.uncertainties, REVIEW_NOTE]))
    return NoticeSummary.model_validate(data)
