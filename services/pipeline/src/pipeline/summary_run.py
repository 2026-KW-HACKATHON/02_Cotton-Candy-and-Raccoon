"""Summarize one stored notice: source snapshot, preparation, Gemini, storage, public re-read.

This module only connects existing steps; preparation, validation and guarded storage
live in their own modules. Later reprocessing or schedules should call summarize_one()
per notice instead of re-wiring these steps.

Transaction boundaries (each step uses its own connection):

1. load_summary_source reads body, files and content_revision in one statement on a
   short autocommit connection, then closes it. No lock or transaction remains.
2. prepare_summary_source downloads attachments with no database connection.
3. summarize_and_save_prepared_notice runs on an autocommit connection. Registering the
   execution token commits immediately, so no transaction is open during Gemini, and the
   result or failure write is committed by the storage function's own transaction.
4. load_stored_summary reads the committed row on a new connection; the report and the
   public view come from that row, never from the in-memory candidate.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg

from pipeline import clock
from pipeline.attachments.summary_bundle import prepare_summary_source
from pipeline.config import DatabaseSettings
from pipeline.gemini_execution import ExecutionBudget, capture_execution_stats
from pipeline.storage.summaries import (
    StoredPreparedSummary,
    StoredSummaryRow,
    SummaryStorageError,
    load_stored_summary,
)
from pipeline.storage.summary_metadata import build_summary_metadata_from_manifest
from pipeline.storage.summary_source import load_summary_source
from pipeline.storage.summary_view import NoticeSummaryView, build_notice_summary_view
from pipeline.summary_job import (
    StoredSummaryFailure,
    StoredSummarySuperseded,
    summarize_and_save_prepared_notice,
)
from pipeline.transform.gemini_client import DEFAULT_MODEL, count_summary_requests

ExecutionStatus = Literal[
    "summarized", "needs_review", "failed", "superseded", "not_found", "storage_failed"
]

EXIT_CODES: dict[str, int] = {
    "summarized": 0,
    "needs_review": 0,
    "failed": 1,
    "not_found": 3,
    "superseded": 4,
    "storage_failed": 5,
}
CONNECT_TIMEOUT_SECONDS = 5


@dataclass(frozen=True, slots=True)
class SummaryRunResult:
    """One execution, reported against the committed row.

    execution_status describes this run. stored_status, public_result and view describe
    the row the app reads after the run, which may still be an earlier successful
    result when this run failed. attachment_status is what this run managed to read.
    """

    notice_id: int
    execution_status: ExecutionStatus
    stored_status: str | None
    public_result: bool
    attachment_status: str | None
    reason_code: str | None
    gemini_requests: int
    view: NoticeSummaryView | None = field(default=None, repr=False)
    gemini_http_attempts: int = 0
    execution_failure: dict[str, object] | None = field(default=None, repr=False)

    @property
    def gemini_called(self) -> bool:
        return self.gemini_http_attempts > 0

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.execution_status]

    def report(self) -> dict[str, Any]:
        """JSON-safe fields for the CLI; no secrets, connection strings or source text."""
        return {
            "notice_id": self.notice_id,
            "execution_status": self.execution_status,
            "stored_status": self.stored_status,
            "public_result": self.public_result,
            "attachment_status": self.attachment_status,
            "reason_code": self.reason_code,
            "gemini_called": self.gemini_called,
            "gemini_requests": self.gemini_requests,
            "gemini_http_attempts": self.gemini_http_attempts,
            "execution_failure": self.execution_failure,
            "view": None if self.view is None else self.view.model_dump(mode="json"),
        }


def _connect(database: DatabaseSettings) -> psycopg.Connection:
    return psycopg.connect(
        database.database_url, connect_timeout=CONNECT_TIMEOUT_SECONDS, autocommit=True
    )


def _review_reason(attachment_status: str) -> str:
    if attachment_status in ("partial", "unread"):
        return f"attachments_{attachment_status}"
    return "summary_review_required"


def summarize_one(
    database: DatabaseSettings,
    notice_id: int,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    budget: ExecutionBudget | None = None,
) -> SummaryRunResult:
    """Summarize one visible notice and report the committed public result.

    The caller supplies validated database/key settings. The AI budget reads its timeout
    setting after preparation unless the caller supplies a budget. Connections are owned
    by this function, so the
    caller owns no transaction. A database error at any step returns storage_failed rather
    than raising, and nothing in the report claims a write that was not committed.
    Programming errors and invalid stored data still raise.
    """
    if type(notice_id) is not int or not 0 < notice_id <= 2**63 - 1:
        raise ValueError("invalid_notice_id")

    def stopped(status: ExecutionStatus, reason: str, requests: int = 0, **extra: Any):
        return SummaryRunResult(
            notice_id=notice_id,
            execution_status=status,
            stored_status=extra.get("stored_status"),
            public_result=False,
            attachment_status=extra.get("attachment_status"),
            reason_code=reason,
            gemini_requests=requests,
            execution_failure=extra.get("execution_failure"),
            gemini_http_attempts=extra.get("gemini_http_attempts", 0),
        )

    try:
        with _connect(database) as conn:
            source = load_summary_source(conn, notice_id)
    except psycopg.Error:
        return stopped("storage_failed", "db_unavailable")
    if source is None:
        return stopped("not_found", "notice_not_found_or_hidden")

    # No connection is held while attachments download.
    prepared = prepare_summary_source(source, reference_datetime=clock.now())
    metadata = build_summary_metadata_from_manifest(
        notice=prepared.notice, file_manifest=prepared.file_manifest, model=model
    )
    attachment_status = metadata.attachment_status

    # Observe calls without charging DB connection/registration to the AI budget.
    try:
        with capture_execution_stats() as stats, count_summary_requests() as requests:
            with _connect(database) as conn:
                outcome = summarize_and_save_prepared_notice(
                    conn,
                    prepared,
                    metadata,
                    expected_source_revision=source.content_revision,
                    api_key=api_key,
                    budget=budget,
                )
    except SummaryStorageError as error:
        return stopped(
            "storage_failed",
            error.reason_code,
            requests[0],
            attachment_status=attachment_status,
            gemini_http_attempts=stats.http_attempts,
        )
    except psycopg.Error:
        return stopped(
            "storage_failed",
            "summary_storage_failed",
            requests[0],
            attachment_status=attachment_status,
            gemini_http_attempts=stats.http_attempts,
        )

    if isinstance(outcome, StoredPreparedSummary):
        status: ExecutionStatus = outcome.status
        reason = None if status == "summarized" else _review_reason(attachment_status)
        execution_failure = outcome.result.execution_failure
        if outcome.result.correction_failure_code is not None:
            status, reason = "failed", outcome.result.correction_failure_code
            execution_failure = execution_failure or _failure_details(reason)
    elif isinstance(outcome, StoredSummaryFailure):
        status, reason = "failed", outcome.reason_code
        execution_failure = outcome.execution_failure or _failure_details(reason)
    elif isinstance(outcome, StoredSummarySuperseded):
        status, reason = "superseded", outcome.reason_code
        execution_failure = None
    else:  # pragma: no cover - summary_job returns only the three types above
        raise TypeError("unexpected summary outcome")

    try:
        with _connect(database) as conn:
            row = load_stored_summary(conn, notice_id)
    except (psycopg.Error, SummaryStorageError):
        # The write may have committed, but it cannot be confirmed: never report success.
        return stopped(
            "storage_failed",
            "summary_read_failed",
            requests[0],
            attachment_status=attachment_status,
            execution_failure=execution_failure,
            gemini_http_attempts=stats.http_attempts,
        )
    view = _public_view(row, prepared.notice, metadata.source_hash)
    return SummaryRunResult(
        notice_id=notice_id,
        execution_status=status,
        stored_status=None if row is None else row.status,
        public_result=view is not None and view.content is not None,
        attachment_status=attachment_status,
        reason_code=reason,
        gemini_requests=requests[0],
        view=view,
        execution_failure=execution_failure,
        gemini_http_attempts=stats.http_attempts,
    )


def _failure_details(reason_code: str) -> dict[str, object]:
    """Fill safe legacy diagnostics when an injected/older client has no runtime metadata."""
    retryable = reason_code in {"api_error", "api_timeout", "api_connection_error"}
    return {
        "reason_code": reason_code,
        "failure_kind": "transient" if retryable else "permanent",
        "retryable": retryable,
        "retry_at": None,
        "status_code": None,
    }


def _public_view(
    row: StoredSummaryRow | None, notice: Any, source_hash: str
) -> NoticeSummaryView | None:
    if row is None:
        return None
    # Text highlight positions refer to the notice text; use it only when the stored row
    # was generated from the same source as this run.
    return build_notice_summary_view(
        status=row.status,
        result=row.result,
        attachment_status=row.attachment_status,
        notice=notice if row.source_hash == source_hash else None,
        file_references=row.file_references,
        preparation_omissions=row.preparation_omissions,
    )
