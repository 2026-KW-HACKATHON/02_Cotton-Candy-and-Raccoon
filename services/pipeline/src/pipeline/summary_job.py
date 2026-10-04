"""Connect a prepared notice to Gemini and issue #14's result table."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

from psycopg import Connection

from pipeline.storage.summaries import (
    StoredPreparedSummary,
    record_summary_failure,
    save_prepared_summary,
)
from pipeline.storage.summary_record import (
    FAILURE_CODES,
    SummaryMetadata,
    SummaryRecord,
    SummaryRecordError,
    build_summary_record,
)
from pipeline.transform.gemini_client import GeminiRequestError
from pipeline.transform.gemini_input import GeminiInputError
from pipeline.transform.gemini_prompt import GeminiConfigurationError
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
    """An unsuccessful execution recorded in the caller's transaction, with no JSON."""

    notice_id: int
    reason_code: str
    warnings: tuple[PreparationIssueLike, ...]
    status: Literal["failed"] = field(default="failed", init=False)
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
    deadline_resolver: DeadlineResolver,
    api_key: str | None = None,
) -> StoredPreparedSummary | StoredSummaryFailure:
    """Run one summary execution and write its completed result or failure.

    The caller owns preparation, source_hash, attachment_status, model/prompt
    version metadata, #14's deadline calculation, and the DB connection/commit.
    The supplied metadata must describe the actual request. This function performs
    no source query, downloads, extraction, SQL migration, or execution scheduling.
    It writes only after summarization finishes, and opens no DB transaction around
    Gemini. Internal shape retries still count as one summary execution.
    A known processing failure returns status=failed/result=None after recording it,
    so raising the API exception does not accidentally roll back the failure row.
    The caller commits both outcome types before reporting durable storage; DB or
    programming failures still raise and must be rolled back.
    """
    checked = SummaryRecord(
        notice_id=prepared.notice_id,
        status="pending",
        metadata=metadata,
    )
    if not isinstance(prepared.warnings, tuple):
        raise SummaryRecordError("invalid_preparation_warnings")
    warnings = tuple(prepared.warnings)
    if not callable(deadline_resolver):
        raise SummaryRecordError("invalid_deadline_resolver")
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
        record_summary_failure(
            conn,
            checked.notice_id,
            checked.metadata,
            reason_code=reason_code,
        )
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
    assert record.result is not None
    deadline_on = deadline_resolver(record.result) if record.status == "summarized" else None
    return save_prepared_summary(
        conn,
        result,
        checked.metadata,
        deadline_on=deadline_on,
        generated_at=generated_at,
    )
