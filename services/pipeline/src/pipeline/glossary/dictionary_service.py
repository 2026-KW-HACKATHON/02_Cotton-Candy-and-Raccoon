"""Look up one dictionary headword using shared durable results and a bounded lease."""

from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import psycopg

from pipeline.config import ConfigError, DatabaseSettings, DictionarySettings
from pipeline.glossary.dictionary import (
    DictionaryBusy,
    DictionaryError,
    DictionaryLookup,
    DictionaryQuery,
    DictionaryResult,
)
from pipeline.glossary.dictionary_client import DictionaryClient
from pipeline.storage.dictionary_cache import claim_lookup, complete_lookup, fail_lookup

LEASE_SECONDS = 180


@contextmanager
def _connect(database: DatabaseSettings) -> Iterator[psycopg.Connection]:
    try:
        with psycopg.connect(
            database.database_url, autocommit=True, connect_timeout=5,
            options="-c lock_timeout=5000 -c statement_timeout=10000",
        ) as conn:
            yield conn
    except psycopg.Error:
        raise DictionaryError("dictionary_storage_error", retryable=True) from None


def lookup_dictionary(
    database: DatabaseSettings,
    query_word: str,
    *,
    refresh: bool = False,
    api_key: str | None = None,
    client: httpx.Client | None = None,
) -> DictionaryLookup:
    """Return every exact-match entry and sense; never select a contextual meaning.

    This service owns short, separate database connections. The claim is committed
    and its connection closed before any HTTP request. Successful found/not_found
    results have no TTL: refresh=True explicitly requests replacement. During a
    refresh, ordinary readers can still use the last good result. The 180-second
    lease expires only a worker's right to publish, not the stored dictionary data.

    Concurrent cache misses/refreshes raise DictionaryBusy without sending HTTP.
    API failures raise safe DictionaryError codes and keep any existing result.
    Late or expired workers cannot publish or release a newer worker's claim.
    A cache hit does not require an API key. client is caller-owned when supplied.
    """
    if not isinstance(database, DatabaseSettings):
        raise DictionaryError("invalid_dictionary_database")
    if type(refresh) is not bool:
        raise DictionaryError("invalid_dictionary_refresh")
    query = DictionaryQuery(query_word)
    with _connect(database) as conn:
        claim = claim_lookup(conn, query, refresh=refresh, lease_seconds=LEASE_SECONDS)
    if claim.cached is not None:
        return DictionaryLookup(result=claim.cached, cache_hit=True)
    if claim.token is None:
        raise DictionaryBusy(max(1, claim.retry_after_seconds))

    try:
        if api_key is None:
            try:
                api_key = DictionarySettings.from_env().api_key
            except ConfigError:
                raise DictionaryError("dictionary_missing_api_key") from None
        candidate = DictionaryClient(api_key, client=client).lookup(query)
        # Revalidate nested data before publishing even when an injected caller
        # implementation returned a previously mutated model instance.
        candidate = DictionaryResult.model_validate_json(candidate.model_dump_json())
        if candidate.query_word != query.word:
            raise DictionaryError("dictionary_invalid_response")
    except Exception as error:
        if isinstance(error, DictionaryError):
            safe_error = error
        else:
            safe_error = DictionaryError("dictionary_lookup_failed")
        cooldown = 60 if (
            not safe_error.retryable or safe_error.code == "dictionary_rate_limited"
        ) else 5
        try:
            with _connect(database) as conn:
                fail_lookup(
                    conn, query, claim.token, safe_error.code, retry_after_seconds=cooldown,
                )
        except DictionaryError:
            # A failed release cannot make the old result disappear. The lease
            # still bounds recovery if the database is temporarily unavailable.
            raise DictionaryError("dictionary_storage_error", retryable=True) from None
        raise safe_error from None

    with _connect(database) as conn:
        saved = complete_lookup(conn, query, claim.token, candidate)
    if not saved:
        raise DictionaryError("dictionary_lease_lost", retryable=True)
    return DictionaryLookup(result=candidate, cache_hit=False)
