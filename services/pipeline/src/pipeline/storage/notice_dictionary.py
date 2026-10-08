"""Attach dictionary queries to one exact saved easy-text snapshot."""

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from pipeline.glossary.notice_dictionary import map_dictionary_candidates
from pipeline.storage.dictionary_cache import FAILURE_CODES
from pipeline.storage.notice_easy_text import (
    EasyTextStorageError,
    get_notice_easy_text,
    get_notice_easy_text_cache_token,
)

_FALLBACK_FIELDS = frozenset({"lookup_status", "error_code", "retryable"})
_FALLBACK_ERRORS = FAILURE_CODES | {
    "dictionary_lookup_busy", "dictionary_storage_error", "dictionary_lease_lost",
    "invalid_dictionary_cached_result", "dictionary_cache_identity_mismatch",
    "invalid_dictionary_query",
}


class NoticeDictionaryStorageError(RuntimeError):
    """Safe persistence errors, without database messages or credentials."""


def _notice_id(value: int) -> int:
    if type(value) is not int or value < 1:
        raise NoticeDictionaryStorageError("invalid_notice_dictionary_id")
    return value


def _validate_candidates(candidates: list[dict]) -> None:
    if not isinstance(candidates, list):
        raise NoticeDictionaryStorageError("invalid_notice_dictionary_candidates")
    for item in candidates:
        if not isinstance(item, dict) or not _FALLBACK_FIELDS.issubset(item):
            raise NoticeDictionaryStorageError("invalid_notice_dictionary_candidates")
        status, error, retryable = (item[name] for name in (
            "lookup_status", "error_code", "retryable",
        ))
        if (
            not isinstance(status, str) or status not in {"pending", "failed"}
            or type(retryable) is not bool
            or (status == "failed" and (
                not isinstance(error, str) or error not in _FALLBACK_ERRORS
            ))
            or (status == "pending" and error not in (None, "dictionary_lookup_busy"))
        ):
            raise NoticeDictionaryStorageError("invalid_notice_dictionary_candidates")


def save_notice_dictionary(
    conn: psycopg.Connection,
    notice_id: int,
    *,
    expected_easy_text_token: str,
    candidates: list[dict],
) -> bool:
    """Commit a short guarded write after all external requests have finished.

    False means the source or easy-text snapshot changed while dictionary work
    was running. The parent lock follows easy-text writers' lock order; the row
    lock additionally protects against direct updates of the easy-text row.
    """
    notice_id = _notice_id(notice_id)
    if (
        not isinstance(expected_easy_text_token, str)
        or len(expected_easy_text_token) != 64
        or any(char not in "0123456789abcdef" for char in expected_easy_text_token)
    ):
        raise NoticeDictionaryStorageError("invalid_notice_dictionary_token")
    _validate_candidates(candidates)
    if conn.closed or not conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
        raise NoticeDictionaryStorageError("notice_dictionary_requires_idle_autocommit")
    try:
        with conn.transaction(), conn.cursor(row_factory=tuple_row) as cursor:
            cursor.execute("select id from public.notices where id=%s for update", (notice_id,))
            if cursor.fetchone() is None:
                return False
            cursor.execute(
                "select notice_id from public.notice_easy_texts where notice_id=%s for update",
                (notice_id,),
            )
            if cursor.fetchone() is None:
                return False
            if get_notice_easy_text_cache_token(conn, notice_id) != expected_easy_text_token:
                return False
            result = get_notice_easy_text(conn, notice_id)
            if result is None:
                return False
            base = [{key: value for key, value in item.items() if key not in _FALLBACK_FIELDS}
                    for item in candidates]
            if result.dictionary_candidates is None or base != map_dictionary_candidates(result):
                raise NoticeDictionaryStorageError("invalid_notice_dictionary_candidates")
            cursor.execute(
                "insert into public.notice_dictionary_links "
                "(notice_id,easy_text_token,candidates) values (%s,%s,%s) "
                "on conflict (notice_id) do update set "
                "easy_text_token=excluded.easy_text_token,candidates=excluded.candidates,"
                "generated_at=clock_timestamp()",
                (notice_id, expected_easy_text_token, Jsonb(candidates)),
            )
        return True
    except (psycopg.Error, EasyTextStorageError):
        raise NoticeDictionaryStorageError("notice_dictionary_storage_error") from None


def get_notice_dictionary(conn: psycopg.Connection, notice_id: int) -> dict | None:
    """Return the same restricted, current snapshot exposed to the app RPC."""
    notice_id = _notice_id(notice_id)
    try:
        with conn.cursor(row_factory=tuple_row) as cursor:
            cursor.execute("select public.get_notice_dictionary(%s)", (notice_id,))
            row = cursor.fetchone()
        return row[0] if row is not None else None
    except psycopg.Error:
        raise NoticeDictionaryStorageError("notice_dictionary_storage_error") from None
