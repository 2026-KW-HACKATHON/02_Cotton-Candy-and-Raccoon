"""Committed shared cache operations; no DB transaction spans a dictionary request."""

import json
from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from pipeline.glossary.dictionary import (
    CONTRACT_VERSION,
    SEARCH_CONDITIONS,
    DictionaryError,
    DictionaryQuery,
    DictionaryResult,
)

MAX_WAIT_SECONDS = 3600
FAILURE_CODES = frozenset({
    "dictionary_authentication_failed", "dictionary_rate_limited", "dictionary_timeout",
    "dictionary_connection_error", "dictionary_upstream_error", "dictionary_invalid_response",
    "dictionary_result_limit", "dictionary_invalid_request", "dictionary_missing_api_key",
    "dictionary_lookup_failed",
})


class DictionaryStorageError(DictionaryError):
    """Safe storage boundary: database messages and connection strings never escape."""


@dataclass(frozen=True, slots=True)
class DictionaryClaim:
    cached: DictionaryResult | None = None
    token: UUID | None = None
    retry_after_seconds: int = 0


def _validate_connection(conn: psycopg.Connection) -> None:
    if conn.closed:
        raise DictionaryStorageError("dictionary_storage_error", retryable=True)
    if not conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
        raise DictionaryStorageError("dictionary_storage_requires_idle_autocommit")


def _validate_query(query: DictionaryQuery) -> None:
    if not isinstance(query, DictionaryQuery) or DictionaryQuery(query.word).word != query.word:
        raise DictionaryStorageError("invalid_dictionary_query")


def _duration(value: int, *, allow_zero: bool = False) -> None:
    if type(value) is not int or not (0 if allow_zero else 1) <= value <= MAX_WAIT_SECONDS:
        raise DictionaryStorageError("invalid_dictionary_wait")


def _result(raw: object, query: DictionaryQuery) -> DictionaryResult:
    try:
        # JSON validation reconstructs nested frozen models and rejects model_construct bypasses.
        encoded = raw.model_dump_json() if isinstance(raw, DictionaryResult) else json.dumps(raw)
        result = DictionaryResult.model_validate_json(encoded)
        if result.query_word != query.word or result.contract_version != CONTRACT_VERSION:
            raise ValueError("result query mismatch")
        return result
    except (ValidationError, ValueError, TypeError, DictionaryError):
        raise DictionaryStorageError("invalid_dictionary_cached_result") from None


def _lock(cursor: psycopg.Cursor[dict], query: DictionaryQuery) -> dict | None:
    row = cursor.execute(
        "select * from public.standard_dictionary_cache where cache_key=%s for update",
        (query.cache_key,),
    ).fetchone()
    if row is not None and (
        row["query_word"] != query.word
        or row["contract_version"] != CONTRACT_VERSION
        or row["search_conditions"] != SEARCH_CONDITIONS
    ):
        raise DictionaryStorageError("dictionary_cache_identity_mismatch")
    return row


def claim_lookup(
    conn: psycopg.Connection,
    query: DictionaryQuery,
    *,
    refresh: bool = False,
    lease_seconds: int = 180,
) -> DictionaryClaim:
    """Return permanent cache, a committed lease, or a bounded retry delay.

    Normal readers keep receiving a valid prior result during a refresh/failure.
    Explicit refresh callers must wait if a different worker owns the query.
    """
    _validate_query(query)
    _validate_connection(conn)
    _duration(lease_seconds)
    if type(refresh) is not bool:
        raise DictionaryStorageError("invalid_dictionary_refresh")
    try:
        with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "insert into public.standard_dictionary_cache "
                "(cache_key,query_word,search_conditions,contract_version) values (%s,%s,%s,%s) "
                "on conflict do nothing",
                (query.cache_key, query.word, Jsonb(SEARCH_CONDITIONS), CONTRACT_VERSION),
            )
            row = _lock(cursor, query)
            if row is None:
                raise DictionaryStorageError("dictionary_cache_identity_mismatch")
            cached = _result(row["result"], query) if row["result"] is not None else None
            if cached is not None and not refresh:
                return DictionaryClaim(cached=cached)
            # Read the clock AFTER row-lock acquisition, including any contention wait.
            waiting = cursor.execute(
                "select greatest(0,least(%s,coalesce(ceil(extract(epoch from "
                "(greatest(lease_expires_at,retry_after_at) - clock_timestamp()))),0)))::int "
                "as seconds from public.standard_dictionary_cache where cache_key=%s",
                (MAX_WAIT_SECONDS, query.cache_key),
            ).fetchone()["seconds"]
            if waiting:
                return DictionaryClaim(retry_after_seconds=waiting)
            token = uuid4()
            cursor.execute(
                "update public.standard_dictionary_cache set lease_token=%s,"
                "lease_expires_at=clock_timestamp() + %s * interval '1 second',"
                "retry_after_at=null,updated_at=clock_timestamp() where cache_key=%s",
                (token, lease_seconds, query.cache_key),
            )
            return DictionaryClaim(token=token)
    except psycopg.Error:
        raise DictionaryStorageError("dictionary_storage_error", retryable=True) from None


def complete_lookup(
    conn: psycopg.Connection, query: DictionaryQuery, token: UUID, result: DictionaryResult,
) -> bool:
    """Replace the complete result only while this unexpired lease is still owned."""
    _validate_query(query)
    _validate_connection(conn)
    if not isinstance(token, UUID):
        raise DictionaryStorageError("invalid_dictionary_token")
    checked = _result(result, query)
    try:
        with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
            if _lock(cursor, query) is None:
                return False
            # Separate lock and UPDATE so a long lock wait cannot reuse an expired deadline check.
            changed = cursor.execute(
                "update public.standard_dictionary_cache set result=%s,"
                "result_updated_at=clock_timestamp(),lease_token=null,lease_expires_at=null,"
                "retry_after_at=null,last_error_code=null,updated_at=clock_timestamp() "
                "where cache_key=%s and lease_token=%s and lease_expires_at>clock_timestamp() "
                "returning cache_key",
                (Jsonb(checked.model_dump(mode="json")), query.cache_key, token),
            ).fetchone()
            return changed is not None
    except psycopg.Error:
        raise DictionaryStorageError("dictionary_storage_error", retryable=True) from None


def fail_lookup(
    conn: psycopg.Connection, query: DictionaryQuery, token: UUID, reason_code: str,
    *, retry_after_seconds: int = 0,
) -> bool:
    """Release only the current unexpired lease, preserving every prior usable result."""
    _validate_query(query)
    _validate_connection(conn)
    _duration(retry_after_seconds, allow_zero=True)
    if not isinstance(token, UUID):
        raise DictionaryStorageError("invalid_dictionary_token")
    if not isinstance(reason_code, str) or reason_code not in FAILURE_CODES:
        raise DictionaryStorageError("invalid_dictionary_failure_code")
    try:
        with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
            if _lock(cursor, query) is None:
                return False
            changed = cursor.execute(
                "update public.standard_dictionary_cache set lease_token=null,"
                "lease_expires_at=null,"
                "retry_after_at=case when %s > 0 then clock_timestamp() + %s * interval '1 second' "
                "else null end,last_error_code=%s,updated_at=clock_timestamp() "
                "where cache_key=%s and lease_token=%s and lease_expires_at>clock_timestamp() "
                "returning cache_key",
                (retry_after_seconds, retry_after_seconds, reason_code, query.cache_key, token),
            ).fetchone()
            return changed is not None
    except psycopg.Error:
        raise DictionaryStorageError("dictionary_storage_error", retryable=True) from None
