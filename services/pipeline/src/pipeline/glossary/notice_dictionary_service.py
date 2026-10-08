"""Enrich one saved candidate snapshot without keeping a transaction over HTTP."""

from collections.abc import Callable

import httpx
import psycopg

from pipeline.config import DatabaseSettings
from pipeline.glossary.dictionary import DictionaryBusy, DictionaryError
from pipeline.glossary.dictionary_service import lookup_dictionary
from pipeline.glossary.easy_language import NoNoticeBodyError
from pipeline.glossary.notice_dictionary import map_dictionary_candidates
from pipeline.glossary.notice_service import load_notice_glossary_input
from pipeline.storage.dictionary_cache import FAILURE_CODES
from pipeline.storage.notice_dictionary import get_notice_dictionary, save_notice_dictionary
from pipeline.storage.notice_easy_text import (
    EasyTextStorageError,
    get_notice_easy_text,
    get_notice_easy_text_cache_token,
)

SAFE_LOOKUP_ERRORS = FAILURE_CODES | {
    "dictionary_lookup_busy", "dictionary_storage_error", "dictionary_lease_lost",
    "invalid_dictionary_cached_result", "dictionary_cache_identity_mismatch",
    "invalid_dictionary_query",
}


def enrich_notice_dictionary(
    database: DatabaseSettings,
    notice_id: int,
    *,
    api_key: str | None = None,
    client: httpx.Client | None = None,
    lookup: Callable | None = None,
) -> dict[str, object] | None:
    """Link every occurrence to one lookup per normalized query; preserve easy text.

    The shared service controls permanent cache reuse, retry cooldown and leases.
    No immediate busy-loop or refresh is performed here. Re-running this function
    retries eligible failed queries; public reads only call the SQL projection.
    A stale source or changed easy-text snapshot is never published.
    """
    if isinstance(notice_id, bool) or not isinstance(notice_id, int) or notice_id < 1:
        raise ValueError("공지 ID는 양의 정수여야 합니다.")
    with psycopg.connect(database.database_url, autocommit=True, connect_timeout=5) as conn:
        with conn.transaction():
            parent = conn.execute(
                "select is_visible from public.notices where id=%s for share", (notice_id,),
            ).fetchone()
            if parent is None or not parent[0]:
                return None
            source = load_notice_glossary_input(conn, notice_id)
            if not source.body_text_present:
                raise NoNoticeBodyError("사전 후보를 추출할 본문이 없습니다.")
            result = get_notice_easy_text(conn, notice_id, notice_revision=source.notice_revision)
            if result is None or result.dictionary_candidates is None:
                raise EasyTextStorageError("최신 쉬운말과 단어 후보를 먼저 저장하세요.")
            token = get_notice_easy_text_cache_token(conn, notice_id)
            if token is None:
                raise EasyTextStorageError("단어 후보의 저장 상태를 확인하지 못했습니다.")
    candidates = map_dictionary_candidates(result)
    outcomes: dict[str, dict[str, object]] = {}
    request = lookup if lookup is not None else lookup_dictionary
    for candidate in candidates:
        cache_key = str(candidate["cache_key"])
        if cache_key in outcomes:
            continue
        outcome: dict[str, object] = {
            "lookup_status": "pending", "error_code": None, "retryable": True,
        }
        try:
            request(database, str(candidate["query_word"]), api_key=api_key, client=client)
        except DictionaryBusy:
            # The live lease/cooldown in the shared cache supplies the wait time.
            outcome["error_code"] = "dictionary_lookup_busy"
        except DictionaryError as error:
            outcome.update(
                lookup_status="failed",
                error_code=(error.code if error.code in SAFE_LOOKUP_ERRORS
                            else "dictionary_lookup_failed"),
                retryable=error.retryable,
            )
        except Exception:
            # Never expose a request URL/key or cancel already committed easy text.
            outcome.update(
                lookup_status="failed", error_code="dictionary_lookup_failed", retryable=False,
            )
        outcomes[cache_key] = outcome
    linked = [{**candidate, **outcomes[str(candidate["cache_key"])]} for candidate in candidates]
    with psycopg.connect(database.database_url, autocommit=True, connect_timeout=5) as conn:
        if not save_notice_dictionary(
            conn, notice_id, expected_easy_text_token=token, candidates=linked,
        ):
            raise EasyTextStorageError("사전 처리 중 원문이나 쉬운말 결과가 바뀌었습니다.")
        return get_notice_dictionary(conn, notice_id)
