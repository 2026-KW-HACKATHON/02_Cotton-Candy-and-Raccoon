"""Store exact notice glossary snapshots without discarding successful progress."""

import re
from dataclasses import dataclass
from datetime import datetime

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from pipeline.glossary.document import NoticeGlossaryResult
from pipeline.glossary.source import notice_content_revision

_SELECT = """
select notice_id, source_hash, rules_version, generated_at, status, result
from public.notice_glossary_results
where notice_id = %s
"""


class NoticeGlossaryStorageError(RuntimeError):
    """A notice result cannot be safely read or replace its saved snapshot."""


@dataclass(frozen=True)
class _SnapshotMetadata:
    notice_id: int
    source_hash: str
    rules_version: str
    generated_at: datetime
    status: str


def _notice_id(value: int | None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise NoticeGlossaryStorageError("공지 용어 저장에는 양의 공지 ID가 필요합니다.")
    return value


def _validated(result: NoticeGlossaryResult) -> NoticeGlossaryResult:
    try:
        validated = NoticeGlossaryResult.model_validate(
            result.model_dump(mode="python", warnings=False)
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise NoticeGlossaryStorageError("공지 용어 결과가 올바르지 않습니다.") from error
    _notice_id(validated.notice_id)
    if not validated.rules_version.strip():
        raise NoticeGlossaryStorageError("공지 용어 처리 규칙 버전이 비어 있습니다.")
    return validated


def _metadata(row: tuple[object, ...]) -> _SnapshotMetadata:
    """Read stable SQL metadata without applying current rules to an old payload."""
    if len(row) != 6:
        raise NoticeGlossaryStorageError("저장된 공지 용어 메타데이터를 확인하지 못했습니다.")
    notice_id, fingerprint, rules_version, generated_at, status = row[:5]
    if (
        isinstance(notice_id, bool)
        or not isinstance(notice_id, int)
        or notice_id <= 0
        or not isinstance(fingerprint, str)
        or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
        or not isinstance(rules_version, str)
        or not rules_version.strip()
        or not isinstance(generated_at, datetime)
        or generated_at.tzinfo is None
        or generated_at.utcoffset() is None
        or not isinstance(status, str)
        or status not in ("completed", "partial")
    ):
        raise NoticeGlossaryStorageError("저장된 공지 용어 메타데이터를 확인하지 못했습니다.")
    return _SnapshotMetadata(notice_id, fingerprint, rules_version, generated_at, status)


def _read_row(row: tuple[object, ...]) -> NoticeGlossaryResult:
    metadata = _metadata(row)
    try:
        result = NoticeGlossaryResult.model_validate(row[5])
        if (
            result.notice_id != metadata.notice_id
            or result.source_hash != metadata.source_hash
            or result.rules_version != metadata.rules_version
            or result.generated_at != metadata.generated_at
            or result.status != metadata.status
        ):
            raise ValueError("metadata mismatch")
        return _validated(result)
    except (IndexError, TypeError, ValueError) as error:
        raise NoticeGlossaryStorageError("저장된 공지 용어 결과를 확인하지 못했습니다.") from error


def get_notice_glossary(
    conn: Connection,
    notice_id: int,
    *,
    source_hash: str | None = None,
    rules_version: str | None = None,
) -> NoticeGlossaryResult | None:
    """Read a validated snapshot, excluding stale source or rule generations.

    The caller owns the connection and transaction. Corrupt stored snapshots
    raise a safe storage error instead of being treated as absent results.
    """
    notice_id = _notice_id(notice_id)
    with conn.cursor(row_factory=tuple_row) as cursor:
        cursor.execute(_SELECT, (notice_id,))
        row = cursor.fetchone()
    if row is None:
        return None
    metadata = _metadata(row)
    if metadata.notice_id != notice_id:
        raise NoticeGlossaryStorageError("저장된 공지 용어 결과의 공지 ID가 일치하지 않습니다.")
    if (
        source_hash is not None
        and metadata.source_hash != source_hash
        or rules_version is not None
        and metadata.rules_version != rules_version
    ):
        return None
    return _read_row(row)


def _preserves_progress(stored: NoticeGlossaryResult, incoming: NoticeGlossaryResult) -> None:
    if (stored.source_hash, stored.rules_version, stored.notice_revision) != (
        incoming.source_hash,
        incoming.rules_version,
        incoming.notice_revision,
    ):
        return
    if stored.status == "completed" and incoming.status == "partial":
        raise NoticeGlossaryStorageError("완료된 공지 용어 결과를 미완료 결과로 바꿀 수 없습니다.")
    if stored.status != "partial":
        return
    # Cache provenance changes when the same successful meaning is reread.
    # Every other lookup field still belongs to the evidence we must preserve.
    incoming_queries = {
        item.query: item.model_dump(exclude={"lookup": {"from_cache"}}) for item in incoming.queries
    }
    if any(
        incoming_queries.get(item.query) != item.model_dump(exclude={"lookup": {"from_cache"}})
        for item in stored.queries
        if item.lookup is not None
    ):
        raise NoticeGlossaryStorageError("기존에 성공한 사전 조회 결과를 보존해야 합니다.")
    incoming_changes = {item.model_dump_json() for item in incoming.changes}
    if any(item.model_dump_json() not in incoming_changes for item in stored.changes):
        raise NoticeGlossaryStorageError("기존에 확인된 용어 치환을 보존해야 합니다.")
    incoming_terms = {item.model_dump_json() for item in incoming.terms}
    if any(
        item.model_dump_json() not in incoming_terms
        for item in stored.terms
        if item.status not in ("failed", "pending")
    ):
        raise NoticeGlossaryStorageError("기존에 확인된 용어 설명을 보존해야 합니다.")


def save_notice_glossary(conn: Connection, result: NoticeGlossaryResult) -> None:
    """Save a snapshot atomically without committing or closing the connection.

    The notice primary key serializes concurrent first writes. A verified
    current parent revision wins over older revisions; otherwise newer saved
    results win. Resuming one source/rule generation must preserve its
    successful queries, replacements and explanations. Any failure rolls back
    this savepoint while leaving the caller's earlier work intact.
    """
    result = _validated(result)
    if conn.autocommit:
        raise NoticeGlossaryStorageError("공지 용어 저장에는 autocommit이 꺼진 연결이 필요합니다.")
    if conn.info.transaction_status == TransactionStatus.IDLE:
        conn.execute("select 1")
    values = (
        result.notice_id,
        result.source_hash,
        result.rules_version,
        result.generated_at,
        result.status,
        Jsonb(result.model_dump(mode="json")),
    )
    with conn.transaction(), conn.cursor(row_factory=tuple_row) as cursor:
        if result.notice_revision is not None:
            # Lock the parent before the derived row, preventing collector
            # updates between the revision check and the caller's commit.
            cursor.execute(
                "select title, body_html from public.notices where id = %s for share",
                (result.notice_id,),
            )
            parent = cursor.fetchone()
            if parent is None or notice_content_revision(*parent) != result.notice_revision:
                raise NoticeGlossaryStorageError(
                    "공지 원문이 바뀌어 이전 결과를 저장하지 않았습니다."
                )
        cursor.execute(
            "insert into public.notice_glossary_results "
            "(notice_id, source_hash, rules_version, generated_at, status, result) "
            "values (%s, %s, %s, %s, %s, %s) "
            "on conflict (notice_id) do nothing returning notice_id",
            values,
        )
        is_new = cursor.fetchone() is not None
        cursor.execute(_SELECT + " for update", (result.notice_id,))
        row = cursor.fetchone()
        if row is None:
            raise NoticeGlossaryStorageError("공지 용어 저장 대상을 확인하지 못했습니다.")
        metadata = _metadata(row)
        if metadata.notice_id != result.notice_id:
            raise NoticeGlossaryStorageError("공지 용어 저장 대상의 공지 ID가 일치하지 않습니다.")
        if is_new:
            _read_row(row)
            return
        same_generation = (metadata.source_hash, metadata.rules_version) == (
            result.source_hash,
            result.rules_version,
        )
        stored = _read_row(row) if same_generation else None
        # Old rule generations may no longer validate, so read only their
        # revision marker. The incoming revision has already matched the
        # locked parent; a timestamp must not keep a superseded source alive.
        stored_revision = (
            stored.notice_revision
            if stored is not None
            else row[5].get("notice_revision")
            if isinstance(row[5], dict)
            else None
        )
        same_revision = stored_revision == result.notice_revision
        if stored is not None and not same_revision:
            stored = None
        if (
            result.notice_revision is None or same_revision
        ) and metadata.generated_at >= result.generated_at:
            return
        if stored is not None:
            _preserves_progress(stored, result)
        cursor.execute(
            "update public.notice_glossary_results "
            "set source_hash = %s, rules_version = %s, generated_at = %s, "
            "status = %s, result = %s where notice_id = %s",
            (*values[1:], result.notice_id),
        )
