"""Compute a public deadline from verified structured dates, never category codes."""

from datetime import date

from pipeline.storage.summary_record import SummaryRecordError
from pipeline.transform.summary_schema import NoticeSummary

DEADLINE_KINDS = frozenset({"application", "submission", "payment"})


def compute_deadline_on(summary: NoticeSummary) -> date | None:
    """Return the latest application/submission/payment end date, or NULL.

    The storage job calls this only for a verified summarized result. Review dates
    remain in their cards but do not establish a sorting deadline.
    Event/operation dates, start-only dates and category_code cannot set a deadline.
    """
    if not isinstance(summary, NoticeSummary):
        raise SummaryRecordError("summary_result_required")
    try:
        checked = NoticeSummary.model_validate(summary.model_dump(mode="json", warnings="error"))
    except (TypeError, ValueError):
        raise SummaryRecordError("invalid_summary_result") from None
    deadlines = [
        date.fromisoformat(item.end_date)
        for item in checked.dates
        if item.kind in DEADLINE_KINDS and item.end_date is not None
    ]
    return max(deadlines, default=None)
