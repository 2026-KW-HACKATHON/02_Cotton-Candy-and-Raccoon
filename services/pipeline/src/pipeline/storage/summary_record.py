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


@dataclass(frozen=True, slots=True)
class SummaryMetadata:
    """Caller-supplied input identity and version information.

    The input owner computes source_hash and attachment_status: #13's current
    preparation result does not retain all file IDs or the original file count.
    Include PDF/image contents when the owners extend #14's text hash contract.
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
    """One full replacement of a notice_summaries row.

    attempt_increment counts summary executions, including their internal API
    retry, rather than HTTP requests. Use 1 on a direct terminal write or when
    starting pending; use 0 to finish that same pending execution.
    The caller supplies #14's computed deadline_on; this module does not compute it.
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

        if self.status in {"summarized", "needs_review"}:
            if not isinstance(self.result, NoticeSummary):
                raise SummaryRecordError("summary_result_required")
            try:
                snapshot = NoticeSummary.model_validate(
                    self.result.model_dump(mode="json", warnings="error")
                )
            except (TypeError, ValueError):
                raise SummaryRecordError("invalid_summary_result") from None
            object.__setattr__(self, "result", snapshot)
            if self.generated_at is None:
                raise SummaryRecordError("summary_generated_at_required")
            if self.last_error_code is not None:
                raise SummaryRecordError("unexpected_error_code")
            if self.status == "summarized" and (
                snapshot.category == "unknown"
                or snapshot.uncertainties
                or self.metadata.attachment_status in {"partial", "unread"}
            ):
                raise SummaryRecordError("summary_requires_review")
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

    Preparation warnings remain caller data: they are not emitted, inserted into
    NoticeSummary, or used alone to change status. File-reference-only evidence
    likewise does not imply review. Unknown/uncertain or incompletely read results
    do require review, and must not publish a deadline.
    """
    if not isinstance(result, PreparedSummaryResult):
        raise SummaryRecordError("invalid_prepared_summary_result")
    if not isinstance(result.warnings, tuple):
        raise SummaryRecordError("invalid_preparation_warnings")
    if not isinstance(result.media_sources, tuple):
        raise SummaryRecordError("invalid_media_sources")
    if deadline_on is not None and type(deadline_on) is not date:
        raise SummaryRecordError("invalid_deadline_on")
    checked = SummaryRecord(
        notice_id=result.notice_id,
        status="needs_review",
        metadata=metadata,
        result=result.summary,
        generated_at=generated_at,
        attempt_increment=attempt_increment,
    )
    assert checked.result is not None
    if (
        checked.result.category == "unknown"
        or checked.result.uncertainties
        or checked.metadata.attachment_status in {"partial", "unread"}
    ):
        return checked
    return SummaryRecord(
        notice_id=checked.notice_id,
        status="summarized",
        metadata=checked.metadata,
        result=checked.result,
        deadline_on=deadline_on,
        generated_at=checked.generated_at,
        attempt_increment=checked.attempt_increment,
    )
