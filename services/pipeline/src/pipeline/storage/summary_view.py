"""Build public card data from a persisted row, without querying the database."""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import TypeAdapter, ValidationError

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
from pipeline.transform.summary_files import PublicFileReference
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


class NoticeSummaryFileView(NoticeSummaryView):
    file_references: tuple[PublicFileReference, ...]


class NoticeSummaryTextFileView(NoticeSummaryTextView, NoticeSummaryFileView):
    pass


_FILE_REFERENCES = TypeAdapter(tuple[PublicFileReference, ...])


def build_notice_summary_view(
    *,
    status: SummaryStatus,
    result: NoticeSummary | Mapping[str, Any] | None,
    attachment_status: AttachmentStatus,
    notice: NoticeInput | None = None,
    file_references: Sequence[PublicFileReference | Mapping[str, Any]] | None = None,
) -> NoticeSummaryView:
    """Read persisted status/public result, never a job's transient outcome.

    A failed retry can leave the DB row summarized; use that row's preserved
    result. pending/failed do not inspect or expose a supplied result. A valid
    review result keeps its headline and four cards unchanged. Review guidance
    is returned separately in message for the caller to display as needed.
    Historical review rows with no result contain guidance alone. An optional
    notice adds plain-text evidence positions to a
    NoticeSummaryTextView; positions refer only to its returned UTF-16 sources,
    never raw HTML. Without that option the existing response shape is retained.
    Optional persisted file references expose only approved public columns and
    only sources named by this result's evidence. Card evidence uses the same
    source_id/source_type pair to refer to that list. Unregistered body images
    keep the explicit original-notice fallback, never an inferred file URL or ID.
    With no result, file links are withheld as well as text highlights.
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
            NoticeSummaryView(status=status, message=messages[status], content=None), notice, None,
            file_references,
        )
    if result is None:
        if status == "needs_review":
            return _with_text_highlights(
                NoticeSummaryView(status=status, message=messages[status], content=None),
                notice,
                None,
                file_references,
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
        return _with_text_highlights(
            NoticeSummaryView(
                status="needs_review",
                message=messages["needs_review"],
                content=content,
            ), notice, checked, file_references,
        )
    return _with_text_highlights(
        NoticeSummaryView(status="summarized", message=None, content=content), notice, checked,
        file_references,
    )


def _with_text_highlights(
    view: NoticeSummaryView, notice: NoticeInput | None, summary: NoticeSummary | None,
    file_references: Sequence[PublicFileReference | Mapping[str, Any]] | None = None,
) -> NoticeSummaryView:
    references = ()
    if file_references is not None and summary is not None:
        checked = _file_reference_snapshot(file_references)
        sources = {
            (item.source_id, item.source_type) for item in summary.evidence
            if item.source_type in {"document", "image"} and item.source_id is not None
        }
        references = tuple(
            item for item in checked if (item.source_id, item.source_type) in sources
        )
    if notice is None:
        return view if file_references is None else NoticeSummaryFileView(
            **view.model_dump(mode="python"), file_references=references,
        )
    highlights = build_summary_text_highlights(summary, notice) if summary is not None else None
    data = {**view.model_dump(mode="python"), "text_highlights": highlights}
    return NoticeSummaryTextView(**data) if file_references is None else NoticeSummaryTextFileView(
        **data, file_references=references,
    )


def _file_reference_snapshot(
    references: Sequence[PublicFileReference | Mapping[str, Any]],
) -> tuple[PublicFileReference, ...]:
    """Revalidate JSON-shaped DB arrays without relaxing strict field types.

    Serialize nested model subclasses as their actual type so restricted fields
    cannot silently disappear before the public schema checks extra keys.
    """
    try:
        if not isinstance(references, (list, tuple)) or len(references) > 1024:
            raise ValueError("invalid_file_references")
        values = []
        for item in references:
            if isinstance(item, PublicFileReference):
                values.append(item.model_dump(
                    mode="json", serialize_as_any=True, warnings="error",
                ))
            elif isinstance(item, Mapping):
                values.append(dict(item))
            else:
                raise ValueError("invalid_file_references")
        checked = _FILE_REFERENCES.validate_json(
            json.dumps(values, ensure_ascii=False, allow_nan=False), strict=True,
        )
        if len({item.source_id for item in checked}) != len(checked):
            raise ValueError("invalid_file_references")
        return checked
    except (TypeError, ValueError, ValidationError):
        raise SummaryCardError("invalid_file_references") from None
