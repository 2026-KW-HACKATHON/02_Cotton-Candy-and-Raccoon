"""Connect a prepared notice to Gemini and issue #14's result table."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

from psycopg import Connection

from pipeline.storage.summaries import (
    StoredPreparedSummary,
    SummaryExecutionSuperseded,
    begin_summary_execution,
    record_summary_failure,
    save_prepared_summary,
)
from pipeline.storage.summary_deadline import compute_deadline_on
from pipeline.storage.summary_record import (
    FAILURE_CODES,
    SummaryMetadata,
    SummaryRecord,
    SummaryRecordError,
    build_summary_record,
)
from pipeline.transform.gemini_client import GeminiRequestError
from pipeline.transform.gemini_input import GeminiInputError
from pipeline.transform.gemini_prompt import SUMMARY_PROMPT_VERSION, GeminiConfigurationError
from pipeline.transform.prepared_summary import (
    PreparationIssueLike,
    PreparedSummaryLike,
    SummaryPreparationError,
)
from pipeline.transform.summarize import summarize_prepared_notice
from pipeline.transform.summary_schema import NoticeSummary, SummaryValidationError

type DeadlineResolver = Callable[[NoticeSummary], date | None]


@dataclass(frozen=True, slots=True)
class StoredSummaryFailure:
    """An execution failure, not the status of any previously stored summary.

    Same-source public data stays intact; changed-source summaries become review
    instructions. status describes this execution, not the persisted row status.
    """

    notice_id: int
    reason_code: str
    warnings: tuple[PreparationIssueLike, ...]
    status: Literal["failed"] = field(default="failed", init=False)
    result: None = field(default=None, init=False)


@dataclass(frozen=True, slots=True)
class StoredSummarySuperseded:
    """A later registered execution owns the row; this outcome was not saved."""

    notice_id: int
    warnings: tuple[PreparationIssueLike, ...]
    reason_code: str = field(default="summary_execution_superseded", init=False)
    status: Literal["superseded"] = field(default="superseded", init=False)
    result: None = field(default=None, init=False)


def _failure_code(error: Exception) -> str:
    if isinstance(error, GeminiConfigurationError):
        return "configuration_error"
    code = getattr(error, "reason_code", None)
    return code if isinstance(code, str) and code in FAILURE_CODES else "summary_processing_failed"


def summarize_and_save_prepared_notice(
    conn: Connection,
    prepared: PreparedSummaryLike,
    metadata: SummaryMetadata,
    *,
    deadline_resolver: DeadlineResolver = compute_deadline_on,
    api_key: str | None = None,
    attempt_increment: int = 1,
    expected_source_revision: int | None = None,
) -> StoredPreparedSummary | StoredSummaryFailure | StoredSummarySuperseded:
    """Run one summary execution and write its completed result or failure.

    The caller owns preparation, source_hash, attachment_status, model/prompt
    version metadata and the DB connection/commit. #14's deadline calculation is
    the default resolver; tests can inject one without calling it on review rows.
    The supplied metadata must describe the actual request. This function performs
    no source-content fetch, downloads, extraction, SQL migration, or execution scheduling.
    It registers an execution token before Gemini without committing caller data.
    Use autocommit (or explicitly commit low-level registration before Gemini) for
    short token registration. A caller transaction otherwise holds the per-notice
    source/registry locks until commit/rollback, blocking source edits and overlapping executions.
    Guarded completion must match the captured source revision as well as its token.
    Source changes invalidate previously published summaries immediately. A caller
    must capture notices.content_revision with the source and pass it as
    expected_source_revision to reject an already stale prepared input before
    Gemini. Legacy callers omitting it retain compatibility, without protection
    against changes that occurred before registration.
    Internal shape retries still count as one summary execution. An external
    pending start uses increment=1; finish that execution here with increment=0.
    A known processing failure returns status=failed/result=None after recording it,
    so raising the API exception does not accidentally roll back the failure row.
    Same-source summary rows retain their public data and status on failure.
    Changed-source failures withhold stale public data and mark needs_review.
    needs_review retains generated content for display with a warning and never
    calls the sorting deadline resolver.
    The caller commits both outcome types before reporting durable storage; DB or
    programming failures still raise and must be rolled back.
    """
    checked = SummaryRecord(
        notice_id=prepared.notice_id,
        status="pending",
        metadata=metadata,
        attempt_increment=attempt_increment,
    )
    if checked.metadata.prompt_version != SUMMARY_PROMPT_VERSION:
        raise SummaryRecordError("prompt_version_mismatch")
    if not isinstance(prepared.warnings, tuple):
        raise SummaryRecordError("invalid_preparation_warnings")
    warnings = tuple(prepared.warnings)
    if not callable(deadline_resolver):
        raise SummaryRecordError("invalid_deadline_resolver")
    try:
        if expected_source_revision is None:
            execution_token = begin_summary_execution(conn, checked.notice_id)
        else:
            execution_token = begin_summary_execution(
                conn, checked.notice_id, expected_source_revision=expected_source_revision
            )
    except SummaryExecutionSuperseded:
        return StoredSummarySuperseded(notice_id=checked.notice_id, warnings=warnings)
    try:
        result = summarize_prepared_notice(prepared, model=checked.metadata.model, api_key=api_key)
    except (
        SummaryPreparationError,
        GeminiInputError,
        GeminiRequestError,
        GeminiConfigurationError,
        SummaryValidationError,
    ) as error:
        # Persist only a fixed reason code. A storage error propagates instead of
        # pretending this failure record was saved; ordinary programming bugs are
        # not mistaken for model failures.
        reason_code = _failure_code(error)
        try:
            record_summary_failure(
                conn,
                checked.notice_id,
                checked.metadata,
                reason_code=reason_code,
                attempt_increment=checked.attempt_increment,
                execution_token=execution_token,
            )
        except SummaryExecutionSuperseded:
            return StoredSummarySuperseded(notice_id=checked.notice_id, warnings=warnings)
        return StoredSummaryFailure(
            notice_id=checked.notice_id,
            reason_code=reason_code,
            warnings=warnings,
        )

    if result.notice_id != checked.notice_id:
        raise SummaryRecordError("summary_notice_id_mismatch")
    generated_at = datetime.now(UTC)
    record = build_summary_record(
        result, checked.metadata, deadline_on=None, generated_at=generated_at
    )
    deadline_on = None
    if record.status == "summarized":
        assert record.result is not None
        deadline_on = deadline_resolver(record.result)
    try:
        return save_prepared_summary(
            conn,
            result,
            checked.metadata,
            deadline_on=deadline_on,
            generated_at=generated_at,
            attempt_increment=checked.attempt_increment,
            execution_token=execution_token,
        )
    except SummaryExecutionSuperseded:
        return StoredSummarySuperseded(notice_id=checked.notice_id, warnings=warnings)
