"""Write issue #14's summary rows using the caller's PostgreSQL transaction."""

from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from psycopg import Connection, Cursor, Error
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from pipeline.storage.summary_record import (
    FAILURE_CODES,
    SummaryMetadata,
    SummaryRecord,
    SummaryRecordError,
    SummaryStatus,
    build_summary_record,
)
from pipeline.transform.prepared_summary import PreparedSummaryResult
from pipeline.transform.summary_schema import NoticeSummary

UPSERT_SUMMARY = """
insert into public.notice_summaries (
    notice_id, status, result, category, category_code, deadline_on, attachment_status,
    source_hash, model, prompt_version, attempt_count, last_error_code, generated_at, file_manifest
) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (notice_id) do update set
    status = excluded.status,
    result = excluded.result,
    category = excluded.category,
    category_code = excluded.category_code,
    deadline_on = excluded.deadline_on,
    attachment_status = excluded.attachment_status,
    source_hash = excluded.source_hash,
    model = excluded.model,
    prompt_version = excluded.prompt_version,
    attempt_count = notice_summaries.attempt_count + excluded.attempt_count,
    last_error_code = excluded.last_error_code,
    generated_at = excluded.generated_at,
    file_manifest = excluded.file_manifest,
    updated_at = now()
returning notice_id
"""

UPSERT_SUMMARY_FAILURE = """
insert into public.notice_summaries (
    notice_id, status, result, category, category_code, deadline_on, attachment_status,
    source_hash, model, prompt_version, attempt_count, last_error_code, generated_at, file_manifest
) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (notice_id) do update set
    status = case when notice_summaries.status = 'summarized'
      and notice_summaries.source_hash is distinct from excluded.source_hash
      then 'needs_review' else notice_summaries.status end,
    result = case when notice_summaries.source_hash is distinct from excluded.source_hash
      then null else notice_summaries.result end,
    category = case when notice_summaries.source_hash is distinct from excluded.source_hash
      then null else notice_summaries.category end,
    category_code = case when notice_summaries.source_hash is distinct from excluded.source_hash
      then null else notice_summaries.category_code end,
    deadline_on = case when notice_summaries.source_hash is distinct from excluded.source_hash
      then null else notice_summaries.deadline_on end,
    last_error_code = excluded.last_error_code,
    attempt_count = notice_summaries.attempt_count + excluded.attempt_count,
    updated_at = now()
returning notice_id
"""

UPSERT_SUMMARY_PENDING = """
insert into public.notice_summaries (
    notice_id, status, result, category, category_code, deadline_on, attachment_status,
    source_hash, model, prompt_version, attempt_count, last_error_code, generated_at, file_manifest
) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (notice_id) do update set
    attempt_count = notice_summaries.attempt_count + excluded.attempt_count,
    updated_at = now()
returning notice_id
"""


REGISTER_SUMMARY_EXECUTION = """
with source as materialized (
    select id, content_revision from public.notices where id = %s for share
)
insert into public.notice_summary_executions (notice_id, source_revision)
select id, content_revision from source
on conflict (notice_id) do update set
    execution_token = nextval('public.notice_summary_execution_token_seq'),
    source_revision = excluded.source_revision
returning execution_token
"""

UPSERT_SUMMARY_EXECUTION_FAILURE = """
insert into public.notice_summaries (
    notice_id, status, result, category, category_code, deadline_on, attachment_status,
    source_hash, model, prompt_version, attempt_count, last_error_code, generated_at, file_manifest
) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (notice_id) do update set
    last_error_code = excluded.last_error_code,
    attempt_count = notice_summaries.attempt_count + excluded.attempt_count,
    updated_at = now()
returning notice_id
"""


def _correction_fallback_statement(*, guarded: bool) -> str:
    # Execution revisions are authoritative. Legacy callers only supply a hash.
    preserve = "notice_summaries.result is not null"
    if not guarded:
        preserve += " and notice_summaries.source_hash = excluded.source_hash"
    content_columns = (
        "status", "result", "category", "category_code", "deadline_on", "attachment_status",
        "source_hash", "model", "prompt_version", "generated_at", "file_manifest",
    )
    assignments = ",\n".join(
        f"    {name} = case when {preserve} then notice_summaries.{name} "
        f"else excluded.{name} end"
        for name in content_columns
    )
    return (
        "insert into public.notice_summaries (\n"
        "    notice_id, status, result, category, category_code, deadline_on, attachment_status,\n"
        "    source_hash, model, prompt_version, attempt_count, last_error_code, generated_at,"
        " file_manifest\n"
        ") values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)\n"
        "on conflict (notice_id) do update set\n"
        + assignments
        + ",\n    last_error_code = excluded.last_error_code,\n"
        "    attempt_count = notice_summaries.attempt_count + excluded.attempt_count,\n"
        "    updated_at = now()\n"
        "returning notice_id, status, deadline_on, generated_at\n"
    )


UPSERT_SUMMARY_CORRECTION_FALLBACK = _correction_fallback_statement(guarded=False)

REGISTER_SUMMARY_EXECUTION_AT_REVISION = REGISTER_SUMMARY_EXECUTION.replace(
    "where id = %s for share", "where id = %s and content_revision = %s for share", 1
)


def _guarded_statement(statement: str) -> str:
    placeholders = ", ".join(["%s"] * 14)
    values = f") values ({placeholders})"
    assert values in statement
    return (
        "with source as materialized (\n"
        "  select id, content_revision from public.notices where id = %s for share\n"
        "), active_execution as (\n"
        "  select e.notice_id from source n join public.notice_summary_executions e\n"
        "    on e.notice_id = n.id\n"
        "  where e.execution_token = %s and e.source_revision = n.content_revision\n"
        "  for update of e\n"
        ")\n"
        + statement.replace(values, f") select {placeholders} from active_execution", 1)
    )


GUARDED_UPSERT_SUMMARY = _guarded_statement(UPSERT_SUMMARY)
GUARDED_UPSERT_SUMMARY_FAILURE = _guarded_statement(UPSERT_SUMMARY_EXECUTION_FAILURE)
GUARDED_UPSERT_SUMMARY_PENDING = _guarded_statement(UPSERT_SUMMARY_PENDING)
GUARDED_UPSERT_SUMMARY_CORRECTION_FALLBACK = _guarded_statement(
    _correction_fallback_statement(guarded=True)
)


class SummaryExecutionSuperseded(RuntimeError):
    """The execution or its source version is obsolete; do not publish it."""

    reason_code = "summary_execution_superseded"

    def __init__(self) -> None:
        super().__init__(self.reason_code)


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
    result is the generated candidate snapshot. A failed correction can preserve
    an older stored summary, so that snapshot may differ from the persisted JSON.
    status, deadline_on, and generated_at describe the actual stored row. The app
    must read committed storage through the public view, which adds review warnings.
    """

    notice_id: int
    status: SummaryStatus
    result: PreparedSummaryResult = field(repr=False)
    generated_at: datetime
    deadline_on: date | None


def begin_summary_execution(
    conn: Connection, notice_id: int, *, expected_source_revision: int | None = None,
) -> int:
    """Register an execution before Gemini, without committing caller data.

    Autocommit makes this a short durable registration. With a caller-owned
    transaction the source/registry locks last until commit/rollback, so source
    changes and overlapping registrations for the same notice wait. UPDATE allocates its token after
    acquiring that lock, never reusing the earlier INSERT candidate.
    Registration does not count attempts or change public summary content.
    Pass the revision captured with the source input to reject an already stale
    prepared input before Gemini. Without it only post-registration source
    changes and execution ordering are protected.
    """
    if type(notice_id) is not int or not 0 < notice_id <= 2**63 - 1:
        raise SummaryRecordError("invalid_notice_id")
    if expected_source_revision is not None and (
        type(expected_source_revision) is not int or not 0 < expected_source_revision <= 2**63 - 1
    ):
        raise SummaryRecordError("invalid_source_revision")
    try:
        with conn.cursor(row_factory=tuple_row) as cursor:
            if expected_source_revision is None:
                cursor.execute(REGISTER_SUMMARY_EXECUTION, (notice_id,))
            else:
                cursor.execute(
                    REGISTER_SUMMARY_EXECUTION_AT_REVISION, (notice_id, expected_source_revision)
                )
            row = cursor.fetchone()
    except Error:
        raise SummaryStorageError("summary_execution_registration_failed") from None
    if row is None and expected_source_revision is not None:
        raise SummaryExecutionSuperseded()
    if (
        not isinstance(row, (tuple, list))
        or len(row) != 1
        or type(row[0]) is not int
        or not 0 < row[0] <= 2**63 - 1
    ):
        raise SummaryStorageError("summary_execution_missing_token")
    return row[0]


def _validate_manifest_source(cursor: Cursor, record: SummaryRecord) -> None:
    """Bind provenance to a locked parent and its complete committed file set.

    File writers increment the parent revision before committing. Locking only
    the parent prevents a validation/write race without reversing the existing
    file-trigger lock order by also acquiring locks on the child rows.
    """
    manifest = record.file_manifest
    assert manifest is not None
    cursor.execute(
        "select id,content_revision,url from public.notices where id=%s for share",
        (record.notice_id,),
    )
    source = cursor.fetchone()
    if source is None:
        raise SummaryExecutionSuperseded()
    if (
        not isinstance(source, (tuple, list)) or len(source) != 3
        or source[0] != record.notice_id or type(source[1]) is not int
        or not isinstance(source[2], str)
    ):
        raise SummaryStorageError("summary_file_source_invalid")
    if source[1] != manifest.source_revision:
        raise SummaryExecutionSuperseded()
    if source[2] != manifest.original_url:
        raise SummaryRecordError("file_manifest_source_mismatch")
    cursor.execute(
        "select id,file_key,kind,url from public.notice_files where notice_id=%s order by id",
        (record.notice_id,),
    )
    rows = cursor.fetchall()
    if any(
        not isinstance(row, (tuple, list)) or len(row) != 4 or type(row[0]) is not int
        or any(not isinstance(value, str) for value in row[1:])
        for row in rows
    ):
        raise SummaryStorageError("summary_file_source_invalid")
    actual = {tuple(row) for row in rows}
    expected = {
        (item.notice_file_id, item.file_key, item.kind, item.url) for item in manifest.files
    }
    if actual != expected:
        raise SummaryRecordError("file_manifest_source_mismatch")


def save_notice_summary(conn: Connection, record: SummaryRecord) -> int:
    """Store a summary or failure; leave transaction ownership with the caller.

    Database exceptions produce no success result. The caller must roll back an
    aborted transaction. Row status never claims that uncommitted data is durable.
    Guarded failures update only the code, count, and update time. An incomplete
    retry input can have a different hash without changing the notice; that is
    never evidence for clearing its valid public result. Actual source changes
    are invalidated by the source-revision DB trigger. Legacy unguarded failures
    retain #24's hash comparison/invalidation, without that version guarantee.
    Previous input/version metadata survive for retry selection in either path.
    A first failure inserts a failed row without summary data.
    Starting pending never removes an existing public summary or successful hash.
    Concurrent callers register before their API request and pass execution_token.
    Guarded writes check the token and captured source revision under source then
    registry locks, rejecting superseded outcomes. Source updates independently
    invalidate existing public content, without counting a summary execution. Legacy
    writes without a token retain compatibility, without execution-order protection.
    A supplied file manifest is checked against the locked notice revision, URL
    and complete file identities. Autocommit connections use a short transaction
    for that check and write; caller-owned transactions are never committed here.
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
        execution_token=record.execution_token,
        file_manifest=record.file_manifest,
    )
    summary = checked.result
    values = (
        checked.notice_id,
        checked.status,
        Jsonb(summary.model_dump(mode="json")) if summary is not None else None,
        summary.category if summary is not None and summary.category != "unknown" else None,
        summary.category_code if summary is not None else None,
        checked.deadline_on,
        checked.metadata.attachment_status,
        checked.metadata.source_hash,
        checked.metadata.model,
        checked.metadata.prompt_version,
        checked.attempt_increment,
        checked.last_error_code,
        checked.generated_at,
        Jsonb(checked.file_manifest.model_dump(mode="json")) if checked.file_manifest else None,
    )
    try:
        atomic = checked.file_manifest is not None and conn.autocommit
        with conn.transaction() if atomic else nullcontext():
            with conn.cursor(row_factory=tuple_row) as cursor:
                if checked.file_manifest is not None:
                    _validate_manifest_source(cursor, checked)
                sql = {
                    "failed": UPSERT_SUMMARY_FAILURE,
                    "pending": UPSERT_SUMMARY_PENDING,
                }.get(checked.status, UPSERT_SUMMARY)
                if checked.execution_token is not None:
                    sql = {
                        "failed": GUARDED_UPSERT_SUMMARY_FAILURE,
                        "pending": GUARDED_UPSERT_SUMMARY_PENDING,
                    }.get(checked.status, GUARDED_UPSERT_SUMMARY)
                    values = (checked.notice_id, checked.execution_token, *values)
                cursor.execute(sql, values)
                row = cursor.fetchone()
    except Error:
        raise SummaryStorageError() from None
    if row is None and checked.execution_token is not None:
        raise SummaryExecutionSuperseded()
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
    execution_token: int | None = None,
) -> StoredPreparedSummary:
    """Store generated content and its review status; retain caller metadata.

    needs_review preserves the generated result JSON for display with an original-
    notice warning. Its sorting deadline remains NULL; warnings and media_sources
    remain caller data rather than separate database fields. Explicit trusted
    file_manifest data is stored privately with the result and projected into
    public original-file links after source identity validation.
    A failed correction preserves any existing result for the same source and
    records only the failure code/count/time. Without an existing result, its
    usable candidate is stored as needs_review. Returned row metadata describes
    storage, while result remains the caller's generated candidate snapshot.
    The private file manifest follows the stored result; public links are generated
    by PostgreSQL from an explicit whitelist and disappear with invalidated content.
    """
    record = build_summary_record(
        result,
        metadata,
        deadline_on=deadline_on,
        generated_at=generated_at,
        attempt_increment=attempt_increment,
        execution_token=execution_token,
    )
    snapshot = replace(
        result,
        notice_id=record.notice_id,
        summary=NoticeSummary.model_validate(result.summary.model_dump(mode="json")),
        warnings=tuple(result.warnings),
        media_sources=tuple(result.media_sources),
        file_manifest=record.file_manifest,
    )
    reason_code = result.correction_failure_code
    if reason_code is None and record.file_manifest is not None and (
        record.file_manifest.omissions or metadata.attachment_status in {"partial", "unread"}
    ):
        # The same atomic preservation path also protects against less complete
        # preparation on an unchanged source. First partial results remain usable.
        reason_code = "input_preparation_failed"
    if reason_code is not None:
        record = replace(record, status="needs_review", deadline_on=None)
        notice_id, status, stored_deadline, stored_generated_at = _save_summary_correction_fallback(
            conn, record, reason_code=reason_code,
        )
    else:
        notice_id = save_notice_summary(conn, record)
        status, stored_deadline, stored_generated_at = (
            record.status, record.deadline_on, generated_at,
        )
    return StoredPreparedSummary(
        notice_id=notice_id,
        status=status,
        result=snapshot,
        generated_at=stored_generated_at,
        deadline_on=stored_deadline,
    )


def _save_summary_correction_fallback(
    conn: Connection, record: SummaryRecord, *, reason_code: str,
) -> tuple[int, SummaryStatus, date | None, datetime]:
    """Atomically retain an existing result or publish the first usable fallback.

    A guarded write checks source revision and execution order before deciding
    whether to preserve a row; hashes are not source-change evidence there.
    Legacy writes preserve an existing result only when their source hashes match.
    The caller keeps transaction ownership. Failure details stay in a typed code.
    """
    if not isinstance(reason_code, str) or reason_code not in FAILURE_CODES:
        raise SummaryRecordError("invalid_error_code")
    if not isinstance(record, SummaryRecord):
        raise SummaryRecordError("invalid_summary_record")
    checked = replace(record, status="needs_review", deadline_on=None)
    if checked.result is None:
        raise SummaryRecordError("summary_result_required")
    summary = checked.result
    values = (
        checked.notice_id,
        checked.status,
        Jsonb(summary.model_dump(mode="json")),
        summary.category if summary.category != "unknown" else None,
        summary.category_code,
        None,
        checked.metadata.attachment_status,
        checked.metadata.source_hash,
        checked.metadata.model,
        checked.metadata.prompt_version,
        checked.attempt_increment,
        reason_code,
        checked.generated_at,
        Jsonb(checked.file_manifest.model_dump(mode="json")) if checked.file_manifest else None,
    )
    sql = UPSERT_SUMMARY_CORRECTION_FALLBACK
    if checked.execution_token is not None:
        sql = GUARDED_UPSERT_SUMMARY_CORRECTION_FALLBACK
        values = (checked.notice_id, checked.execution_token, *values)
    try:
        atomic = checked.file_manifest is not None and conn.autocommit
        with conn.transaction() if atomic else nullcontext():
            with conn.cursor(row_factory=tuple_row) as cursor:
                if checked.file_manifest is not None:
                    _validate_manifest_source(cursor, checked)
                cursor.execute(sql, values)
                row = cursor.fetchone()
    except Error:
        raise SummaryStorageError() from None
    if row is None and checked.execution_token is not None:
        raise SummaryExecutionSuperseded()
    if (
        not isinstance(row, (tuple, list))
        or len(row) != 4
        or type(row[0]) is not int
        or row[0] != checked.notice_id
        or not isinstance(row[1], str)
        or row[1] not in {"summarized", "needs_review"}
        or (row[2] is not None and type(row[2]) is not date)
        or (row[1] == "needs_review" and row[2] is not None)
        or not isinstance(row[3], datetime)
        or row[3].tzinfo is None
        or row[3].utcoffset() is None
    ):
        raise SummaryStorageError("summary_storage_invalid_outcome")
    return row[0], row[1], row[2], row[3]


def record_summary_failure(
    conn: Connection,
    notice_id: int,
    metadata: SummaryMetadata,
    *,
    reason_code: str,
    attempt_increment: int = 1,
    execution_token: int | None = None,
) -> int:
    """Record an execution failure; guarded writes preserve existing public content.

    Source revisions/DB triggers decide real invalidation. Only legacy callers
    without an execution token still infer source changes from differing hashes.
    """
    return save_notice_summary(
        conn,
        SummaryRecord(
            notice_id=notice_id,
            status="failed",
            metadata=metadata,
            last_error_code=reason_code,
            attempt_increment=attempt_increment,
            execution_token=execution_token,
        ),
    )
