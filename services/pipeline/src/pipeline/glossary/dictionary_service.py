"""Reuse dictionary meanings, preferring Standard Korean Dictionary to Ourmalsam."""

from collections.abc import Callable
from contextlib import ExitStack
from datetime import UTC, datetime
from typing import Protocol

from psycopg import Connection

from pipeline.glossary.client import DictionaryDefinitionClient
from pipeline.glossary.config import GlossarySettings
from pipeline.glossary.models import (
    GlossaryEntry,
    GlossaryLookup,
    canonical_headword,
    normalize_query,
)
from pipeline.storage.glossary import GlossaryStorageError, get_glossary, save_glossary

DICTIONARY_PROVIDERS = ("stdict", "opendict")
_QUERY_LOCK = "select pg_advisory_xact_lock(hashtextextended(%s, 2026100701))"
_LOCK_NAMESPACE = "dictionary-definition:"


class LookupClient(Protocol):
    """The dictionary client's interface, also implemented by offline doubles."""

    def lookup(self, query: str) -> tuple[GlossaryEntry, ...]: ...


def _provider_key(provider: str, settings: GlossarySettings | None) -> str:
    """Inspect only the credential for the provider about to be called."""
    settings = settings if settings is not None else GlossarySettings.from_env(provider=provider)
    return settings.key_for(provider)


def _validate_meanings(query: str, entries: tuple[GlossaryEntry, ...], provider: str) -> None:
    if any(entry.provider != provider for entry in entries):
        raise ValueError("요청한 사전과 반환된 항목의 출처가 일치하지 않습니다.")
    if any(canonical_headword(entry.headword) != canonical_headword(query) for entry in entries):
        raise ValueError("조회한 용어와 반환된 뜻풀이의 표제어가 일치하지 않습니다.")


def _dictionary_cached_lookup(cached: GlossaryLookup | None) -> GlossaryLookup | None:
    """Historical Ourmalsam-only caches cannot bypass the new preferred dictionary."""
    if cached is None or "stdict" not in cached.providers_checked:
        return None
    standard = tuple(entry for entry in cached.entries if entry.provider == "stdict")
    if standard:
        entries, checked = standard, ("stdict",)
    elif "opendict" in cached.providers_checked:
        entries = tuple(entry for entry in cached.entries if entry.provider == "opendict")
        checked = DICTIONARY_PROVIDERS
    else:
        return None
    _validate_meanings(cached.query, entries, checked[-1])
    return GlossaryLookup(
        query=cached.query,
        status="found" if entries else "not_found",
        entries=entries,
        providers_checked=checked,
        queried_at=cached.queried_at,
        from_cache=True,
    )


def _query_dictionary_definition(
    query: str,
    *,
    stdict_client: LookupClient | None,
    opendict_client: LookupClient | None,
    settings: GlossarySettings | None,
    clock: Callable[[], datetime] | None,
    fallback_cache: GlossaryLookup | None = None,
) -> GlossaryLookup:
    queried_at = clock() if clock is not None else datetime.now(UTC)
    if queried_at.tzinfo is None or queried_at.utcoffset() is None:
        raise ValueError("조회 시각에는 시간대가 필요합니다.")
    with ExitStack() as stack:
        client = stdict_client
        if client is None:
            client = stack.enter_context(
                DictionaryDefinitionClient("stdict", _provider_key("stdict", settings))
            )
        entries = client.lookup(query)
        _validate_meanings(query, entries, "stdict")
        checked = ("stdict",)
        if not entries:
            # Only successful empty searches reach this branch. API errors propagate.
            checked = DICTIONARY_PROVIDERS
            if fallback_cache is not None and "opendict" in fallback_cache.providers_checked:
                entries = tuple(
                    entry for entry in fallback_cache.entries if entry.provider == "opendict"
                )
            else:
                client = opendict_client
                if client is None:
                    client = stack.enter_context(
                        DictionaryDefinitionClient("opendict", _provider_key("opendict", settings))
                    )
                entries = client.lookup(query)
            _validate_meanings(query, entries, "opendict")
    return GlossaryLookup(
        query=query,
        status="found" if entries else "not_found",
        entries=entries,
        providers_checked=checked,
        queried_at=queried_at,
    )


def query_dictionary_definition(
    query: str,
    *,
    stdict_client: LookupClient | None = None,
    opendict_client: LookupClient | None = None,
    settings: GlossarySettings | None = None,
    clock: Callable[[], datetime] | None = None,
) -> GlossaryLookup:
    """Retain all official meanings without cache reads, writes or word replacement."""
    return _query_dictionary_definition(
        normalize_query(query),
        stdict_client=stdict_client,
        opendict_client=opendict_client,
        settings=settings,
        clock=clock,
    )


def lookup_dictionary_definition(
    conn: Connection,
    query: str,
    *,
    refresh: bool = False,
    stdict_client: LookupClient | None = None,
    opendict_client: LookupClient | None = None,
    settings: GlossarySettings | None = None,
    clock: Callable[[], datetime] | None = None,
) -> GlossaryLookup:
    """Reuse completed lookups indefinitely, with transaction locks before first calls.

    Legacy Ourmalsam entries are reusable after a successful empty Standard search.
    Explicit refresh queries the APIs again. API errors never create negative caches;
    storage preserves saved meanings when a refresh returns no results. The caller
    owns the transaction and connection, and this service never commits or closes it.
    """
    query = normalize_query(query)
    if conn.autocommit:
        raise GlossaryStorageError("용어 조회에는 autocommit이 꺼진 연결이 필요합니다.")
    conn.execute(_QUERY_LOCK, (_LOCK_NAMESPACE + query,))
    cached = None
    if not refresh:
        cached = get_glossary(conn, query)
        complete = _dictionary_cached_lookup(cached)
        if complete is not None:
            return complete
    result = _query_dictionary_definition(
        query,
        stdict_client=stdict_client,
        opendict_client=opendict_client,
        settings=settings,
        clock=clock,
        fallback_cache=cached,
    )
    save_glossary(conn, result)
    stored = _dictionary_cached_lookup(get_glossary(conn, query))
    if stored is None:
        raise RuntimeError("사전 뜻풀이 저장 후 조회 결과를 확인하지 못했습니다.")
    return stored.model_copy(update={"from_cache": False})
