"""Write issue #14's summary rows using the caller's PostgreSQL transaction."""

from dataclasses import dataclass, field
from datetime import date, datetime

from psycopg import Connection, Error
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from pipeline.storage.summary_record import (
    SummaryMetadata,
    SummaryRecord,
    SummaryRecordError,
    SummaryStatus,
    build_summary_record,
)
from pipeline.transform.prepared_summary import PreparedSummaryResult
from pipeline.transform.summary_schema import NoticeSummary

UPSERT_SUMMARY = """
insert into notice_summaries (
    notice_id, status, result, category, deadline_on, attachment_status,
    source_hash, model, prompt_version, attempt_count, last_error_code, generated_at
) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (notice_id) do update set
    status = excluded.status,
    result = excluded.result,
    category = excluded.category,
    deadline_on = excluded.deadline_on,
    attachment_status = excluded.attachment_status,
    source_hash = excluded.source_hash,
    model = excluded.model,
    prompt_version = excluded.prompt_version,
    attempt_count = notice_summaries.attempt_count + excluded.attempt_count,
    last_error_code = excluded.last_error_code,
    generated_at = excluded.generated_at,
    updated_at = now()
returning notice_id
"""

# A failure on the same source keeps the existing summary (model or prompt reruns).
# A failure on a changed source must not leave the old summary public: a summarized
# row becomes needs_review and its public columns are cleared. source_hash and the
# other metadata keep the last successful values, recording that the new source has
# not been summarized yet. Every SET expression reads the pre-update row.
UPSERT_SUMMARY_FAILURE = """
insert into notice_summaries (
    notice_id, status, result, category, deadline_on, attachment_status,
    source_hash, model, prompt_version, attempt_count, last_error_code, generated_at
) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (notice_id) do update set
    status = case
        when notice_summaries.source_hash <> excluded.source_hash
            and notice_summaries.status = 'summarized' then 'needs_review'
        else notice_summaries.status
    end,
    result = case
        when notice_summaries.source_hash = excluded.source_hash then notice_summaries.result
    end,
    category = case
        when notice_summaries.source_hash = excluded.source_hash then notice_summaries.category
    end,
    deadline_on = case
        when notice_summaries.source_hash = excluded.source_hash then notice_summaries.deadline_on
    end,
    last_error_code = excluded.last_error_code,
    attempt_count = notice_summaries.attempt_count + excluded.attempt_count,
    updated_at = now()
returning notice_id
"""


class SummaryStorageError(RuntimeError):
    """Storage failed; provider details and bound summary data stay out of messages."""

    def __init__(self, reason_code: str = "summary_storage_failed") -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class StoredPreparedSummary:
    """A completed write with an in-memory snapshot for the pipeline caller.

    The caller must commit before reporting durable storage. Warnings are only
    retained as data; no message, log, or separate database field is produced.
    result is internal caller data, not the public row's result JSON: needs_review
    rows expose no summary content. Do not use this snapshot as an app response.
    """

    notice_id: int
    status: SummaryStatus
    result: PreparedSummaryResult = field(repr=False)
    generated_at: datetime
    deadline_on: date | None


def save_notice_summary(conn: Connection, record: SummaryRecord) -> int:
    """Store a summary or failure; leave transaction ownership with the caller.

    Database exceptions produce no success result. The caller must roll back an
    aborted transaction. Row status never claims that uncommitted data is durable.
    Failure on an existing row with the same source_hash updates only its code,
    attempt count, and update time. With a different source_hash it also hides the
    stale public summary: summarized becomes needs_review, and result, category, and
    deadline_on become NULL; source_hash and other metadata are kept. A first
    failure inserts a failed row without summary data.
    """
    if not isinstance(record, SummaryRecord):
        raise SummaryRecordError("invalid_summary_record")
    # Revalidate nested Pydantic data in case a caller mutated it after construction.
    checked = SummaryRecord(
        notice_id=record.notice_id,
        status=record.status,
        metadata=record.metadata,
        result=record.result,
        deadline_on=record.deadline_on,
        generated_at=record.generated_at,
        last_error_code=record.last_error_code,
        attempt_increment=record.attempt_increment,
    )
    summary = checked.result
    values = (
        checked.notice_id,
        checked.status,
        Jsonb(summary.model_dump(mode="json")) if summary is not None else None,
        summary.category if summary is not None else None,
        checked.deadline_on,
        checked.metadata.attachment_status,
        checked.metadata.source_hash,
        checked.metadata.model,
        checked.metadata.prompt_version,
        checked.attempt_increment,
        checked.last_error_code,
        checked.generated_at,
    )
    try:
        with conn.cursor(row_factory=tuple_row) as cursor:
            sql = UPSERT_SUMMARY_FAILURE if checked.status == "failed" else UPSERT_SUMMARY
            cursor.execute(sql, values)
            row = cursor.fetchone()
    except Error:
        raise SummaryStorageError() from None
    if (
        not isinstance(row, (tuple, list))
        or len(row) != 1
        or type(row[0]) is not int
        or row[0] != checked.notice_id
    ):
        raise SummaryStorageError("summary_storage_missing_id")
    return row[0]


def save_prepared_summary(
    conn: Connection,
    result: PreparedSummaryResult,
    metadata: SummaryMetadata,
    *,
    deadline_on: date | None,
    generated_at: datetime,
    attempt_increment: int = 1,
) -> StoredPreparedSummary:
    """Store public content only when verified; retain caller data in memory.

    needs_review writes result=NULL and deadline_on=NULL so the app can show
    only an original-notice instruction. No internal review JSON is persisted.
    """
    record = build_summary_record(
        result,
        metadata,
        deadline_on=deadline_on,
        generated_at=generated_at,
        attempt_increment=attempt_increment,
    )
    snapshot = PreparedSummaryResult(
        notice_id=record.notice_id,
        summary=NoticeSummary.model_validate(result.summary.model_dump(mode="json")),
        warnings=tuple(result.warnings),
        media_sources=tuple(result.media_sources),
    )
    notice_id = save_notice_summary(conn, record)
    return StoredPreparedSummary(
        notice_id=notice_id,
        status=record.status,
        result=snapshot,
        generated_at=generated_at,
        deadline_on=record.deadline_on,
    )


def record_summary_failure(
    conn: Connection,
    notice_id: int,
    metadata: SummaryMetadata,
    *,
    reason_code: str,
    attempt_increment: int = 1,
) -> int:
    """Record a known failure code.

    An existing summary for the same source_hash is preserved. If the source
    changed, the stale summary is hidden as needs_review (see save_notice_summary).
    """
    return save_notice_summary(
        conn,
        SummaryRecord(
            notice_id=notice_id,
            status="failed",
            metadata=metadata,
            last_error_code=reason_code,
            attempt_increment=attempt_increment,
        ),
    )
