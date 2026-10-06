"""Validate rows for issue #14's summary table without accessing the database."""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from pipeline.transform.prepared_summary import PreparedSummaryResult
from pipeline.transform.summary_schema import NoticeSummary

type SummaryStatus = Literal["pending", "summarized", "needs_review", "failed"]
type AttachmentStatus = Literal["none", "all_read", "partial", "unread"]

SUMMARY_STATUSES = frozenset({"pending", "summarized", "needs_review", "failed"})
ATTACHMENT_STATUSES = frozenset({"none", "all_read", "partial", "unread"})
FAILURE_CODES = frozenset(
    {
        "input_preparation_failed",
        "invalid_prepared_input",
        "summary_source_has_no_content",
        "invalid_input",
        "empty_input",
        "empty_input_block",
        "invalid_input_block",
        "unsupported_mime_type",
        "unsupported_input_block",
        "invalid_media_data",
        "invalid_retry_text",
        "input_too_large",
        "missing_api_key",
        "empty_prompt",
        "api_error",
        "api_timeout",
        "api_connection_error",
        "response_incomplete",
        "empty_response",
        "response_validation_failed",
        "configuration_error",
        "summary_processing_failed",
    }
)


class SummaryRecordError(ValueError):
    """A safe field-level code for an invalid storage contract."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _identifier(value: str, *, reason_code: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", value):
        raise SummaryRecordError(reason_code)


def _summary_snapshot(value: NoticeSummary) -> NoticeSummary:
    if not isinstance(value, NoticeSummary):
        raise SummaryRecordError("summary_result_required")
    try:
        return NoticeSummary.model_validate(value.model_dump(mode="json", warnings="error"))
    except (TypeError, ValueError):
        raise SummaryRecordError("invalid_summary_result") from None


def _requires_review(summary: NoticeSummary, metadata: "SummaryMetadata") -> bool:
    """Only text-matched claims may be published, including every date reference.

    Evidence does not identify individual array entries. A text match for one
    date cannot validate a different date supported only by a file reference.
    Consequently any file-only or unchecked reference requires review.
    """
    if (
        summary.category == "unknown"
        or summary.category_code is None
        or summary.uncertainties
        or metadata.attachment_status in {"partial", "unread"}
        or any(
            item.source_type != "text" or item.verification != "text_matched"
            for item in summary.evidence
        )
    ):
        return True
    required = {"summary", "category_code"}
    required.update(
        name
        for name in (
            "applicable_area",
            "audience",
            "action",
            "location",
            "dates",
            "notes",
            "topics",
        )
        if getattr(summary, name) not in (None, [])
    )
    return bool(required - {item.field for item in summary.evidence})


@dataclass(frozen=True, slots=True)
class SummaryMetadata:
    """Caller-supplied input identity and version information.

    summary_metadata builds the text hash from body text and file_key-sorted
    extracted attachment texts. The input owner supplies original/read file
    counts because #13's preparation result does not retain that manifest.
    PDF/image bytes are outside the current text hash contract.
    Model and prompt_version must describe the request that produced the result.
    """

    source_hash: str
    model: str
    prompt_version: str
    attachment_status: AttachmentStatus

    def __post_init__(self) -> None:
        if not isinstance(self.source_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.source_hash
        ):
            raise SummaryRecordError("invalid_source_hash")
        _identifier(self.model, reason_code="invalid_model")
        _identifier(self.prompt_version, reason_code="invalid_prompt_version")
        if (
            not isinstance(self.attachment_status, str)
            or self.attachment_status not in ATTACHMENT_STATUSES
        ):
            raise SummaryRecordError("invalid_attachment_status")


@dataclass(frozen=True, slots=True)
class SummaryRecord:
    """A summary write or execution failure for a notice_summaries row.

    Only summarized rows contain public result JSON. needs_review tells the app
    to show an original-notice instruction, with no result or deadline. Failure
    writes preserve existing data when the source is unchanged. A changed source
    invalidates the old public summary, retaining its input/version metadata.

    attempt_increment counts summary executions, including their internal API
    retry, rather than HTTP requests. Use 1 on a direct terminal write or when
    starting pending; use 0 to finish that same pending execution.
    summary_deadline supplies #14's deterministic deadline calculation.
    """

    notice_id: int
    status: SummaryStatus
    metadata: SummaryMetadata
    result: NoticeSummary | None = field(default=None, repr=False)
    deadline_on: date | None = None
    generated_at: datetime | None = None
    last_error_code: str | None = None
    attempt_increment: int = 1

    def __post_init__(self) -> None:
        if type(self.notice_id) is not int or not 0 < self.notice_id <= 2**63 - 1:
            raise SummaryRecordError("invalid_notice_id")
        if not isinstance(self.status, str) or self.status not in SUMMARY_STATUSES:
            raise SummaryRecordError("invalid_summary_status")
        if not isinstance(self.metadata, SummaryMetadata):
            raise SummaryRecordError("invalid_summary_metadata")
        metadata = SummaryMetadata(
            source_hash=self.metadata.source_hash,
            model=self.metadata.model,
            prompt_version=self.metadata.prompt_version,
            attachment_status=self.metadata.attachment_status,
        )
        object.__setattr__(self, "metadata", metadata)
        if type(self.attempt_increment) is not int or self.attempt_increment not in {0, 1}:
            raise SummaryRecordError("invalid_attempt_increment")
        if self.deadline_on is not None and type(self.deadline_on) is not date:
            raise SummaryRecordError("invalid_deadline_on")
        if self.generated_at is not None and (
            not isinstance(self.generated_at, datetime)
            or self.generated_at.tzinfo is None
            or self.generated_at.utcoffset() is None
        ):
            raise SummaryRecordError("invalid_generated_at")

        if self.status == "summarized":
            snapshot = _summary_snapshot(self.result)
            object.__setattr__(self, "result", snapshot)
            if self.generated_at is None:
                raise SummaryRecordError("summary_generated_at_required")
            if self.last_error_code is not None:
                raise SummaryRecordError("unexpected_error_code")
            if _requires_review(snapshot, self.metadata):
                raise SummaryRecordError("summary_requires_review")
        elif self.status == "needs_review":
            if self.result is not None:
                raise SummaryRecordError("unexpected_summary_result")
            if self.generated_at is None:
                raise SummaryRecordError("summary_generated_at_required")
            if self.last_error_code is not None:
                raise SummaryRecordError("unexpected_error_code")
        else:
            if self.result is not None or self.generated_at is not None:
                raise SummaryRecordError("unexpected_summary_result")
            if self.status == "failed":
                if (
                    not isinstance(self.last_error_code, str)
                    or self.last_error_code not in FAILURE_CODES
                ):
                    raise SummaryRecordError("invalid_error_code")
            elif self.last_error_code is not None:
                raise SummaryRecordError("unexpected_error_code")

        if self.status != "summarized" and self.deadline_on is not None:
            raise SummaryRecordError("unexpected_deadline_on")


def build_summary_record(
    result: PreparedSummaryResult,
    metadata: SummaryMetadata,
    *,
    deadline_on: date | None,
    generated_at: datetime,
    attempt_increment: int = 1,
) -> SummaryRecord:
    """Map a prepared result to #14's row contract without IO.

    Only results supported by text-matched evidence can be published as summarized.
    File-only, unchecked, unknown/uncertain, or incompletely read results become
    needs_review: the app must show "원문을 확인하세요" instead of summary content.
    Its public result and deadline are NULL. The original prepared result remains
    caller data in memory; this function does not persist internal review content.
    Preparation warnings alone do not change status.
    """
    if not isinstance(result, PreparedSummaryResult):
        raise SummaryRecordError("invalid_prepared_summary_result")
    if not isinstance(result.warnings, tuple):
        raise SummaryRecordError("invalid_preparation_warnings")
    if not isinstance(result.media_sources, tuple):
        raise SummaryRecordError("invalid_media_sources")
    if deadline_on is not None and type(deadline_on) is not date:
        raise SummaryRecordError("invalid_deadline_on")
    snapshot = _summary_snapshot(result.summary)
    checked = SummaryRecord(
        notice_id=result.notice_id,
        status="needs_review",
        metadata=metadata,
        generated_at=generated_at,
        attempt_increment=attempt_increment,
    )
    if _requires_review(snapshot, checked.metadata):
        return checked
    return SummaryRecord(
        notice_id=checked.notice_id,
        status="summarized",
        metadata=checked.metadata,
        result=snapshot,
        deadline_on=deadline_on,
        generated_at=checked.generated_at,
        attempt_increment=checked.attempt_increment,
    )
