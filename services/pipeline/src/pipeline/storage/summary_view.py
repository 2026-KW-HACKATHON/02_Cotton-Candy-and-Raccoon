"""Build public card data from a persisted row, without querying the database."""

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from pipeline.storage.summary_record import (
    ATTACHMENT_STATUSES,
    SUMMARY_STATUSES,
    AttachmentStatus,
    SummaryStatus,
    summary_requires_review,
)
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_cards import (
    CardModel,
    SummaryCardError,
    SummaryCards,
    build_summary_cards,
    summary_snapshot,
)
from pipeline.transform.summary_highlights import (
    SummaryTextHighlights,
    build_summary_text_highlights,
)
from pipeline.transform.summary_schema import NoticeSummary


class NoticeSummaryView(CardModel):
    status: SummaryStatus
    message: str | None
    content: SummaryCards | None


class NoticeSummaryTextView(NoticeSummaryView):
    text_highlights: SummaryTextHighlights | None


def build_notice_summary_view(
    *,
    status: SummaryStatus,
    result: NoticeSummary | Mapping[str, Any] | None,
    attachment_status: AttachmentStatus,
    notice: NoticeInput | None = None,
) -> NoticeSummaryView:
    """Read persisted status/public result, never a job's transient outcome.

    A failed retry can leave the DB row summarized; use that row's preserved
    result. pending/failed do not inspect or expose a supplied result. A valid
    review result keeps its four cards and adds an original-notice instruction
    only to the display headline. Historical review rows with no result contain
    guidance alone. An optional notice adds plain-text evidence positions to a
    NoticeSummaryTextView; positions refer only to its returned UTF-16 sources,
    never raw HTML. Without that option the existing response shape is retained.
    This is no API or DB integration.
    """
    if not isinstance(status, str) or status not in SUMMARY_STATUSES:
        raise SummaryCardError("invalid_summary_status")
    if not isinstance(attachment_status, str) or attachment_status not in ATTACHMENT_STATUSES:
        raise SummaryCardError("invalid_attachment_status")
    messages = {
        "needs_review": "원문 확인 요함",
        "pending": "요약을 준비하고 있습니다.",
        "failed": "요약을 생성하지 못했습니다. 원문을 확인하세요.",
    }
    if status in {"pending", "failed"}:
        return _with_text_highlights(
            NoticeSummaryView(status=status, message=messages[status], content=None), notice, None
        )
    if result is None:
        if status == "needs_review":
            return _with_text_highlights(
                NoticeSummaryView(status=status, message=messages[status], content=None),
                notice,
                None,
            )
        raise SummaryCardError("summary_result_required")
    if isinstance(result, NoticeSummary):
        checked = summary_snapshot(result)
    elif isinstance(result, Mapping):
        try:
            checked = NoticeSummary.model_validate(dict(result))
        except (TypeError, ValueError, ValidationError):
            raise SummaryCardError("invalid_summary_result") from None
        checked = summary_snapshot(checked)
    else:
        raise SummaryCardError("invalid_summary_result")
    content = build_summary_cards(checked)
    if status == "needs_review" or summary_requires_review(
        checked, attachment_status=attachment_status
    ):
        headline = content.headline.model_copy(
            update={"text": f"{content.headline.text} (원문 확인 요함)"}
        )
        return _with_text_highlights(
            NoticeSummaryView(
                status="needs_review",
                message=messages["needs_review"],
                content=content.model_copy(update={"headline": headline}),
            ), notice, checked,
        )
    return _with_text_highlights(
        NoticeSummaryView(status="summarized", message=None, content=content), notice, checked
    )


def _with_text_highlights(
    view: NoticeSummaryView, notice: NoticeInput | None, summary: NoticeSummary | None
) -> NoticeSummaryView:
    if notice is None:
        return view
    highlights = build_summary_text_highlights(summary, notice) if summary is not None else None
    return NoticeSummaryTextView(**view.model_dump(mode="python"), text_highlights=highlights)
