"""Read reusable explanations, or query dictionaries and save a complete lookup."""

from collections.abc import Callable
from contextlib import ExitStack
from datetime import UTC, datetime
from typing import Protocol

from psycopg import Connection

from pipeline.glossary.client import DictionaryClient, reparse_refinements
from pipeline.glossary.config import GlossarySettings
from pipeline.glossary.models import (
    GlossaryEntry,
    GlossaryLookup,
    Provider,
    active_cached_lookup,
    normalize_query,
)
from pipeline.glossary.onterm_client import OnTermClient
from pipeline.storage.glossary import get_glossary, save_glossary


class LookupClient(Protocol):
    """The small interface shared by API clients and offline test doubles."""

    def lookup(self, query: str) -> tuple[GlossaryEntry, ...]: ...


def query_glossary(
    query: str,
    *,
    onterm_client: LookupClient | None = None,
    opendict_client: LookupClient | None = None,
    settings: GlossarySettings | None = None,
    clock: Callable[[], datetime] | None = None,
) -> GlossaryLookup:
    """Query OnTerm refinements first, then Ourmalsam after an empty search.

    OnTerm returns only exact matches with a labelled refinement relationship
    and an explicit KOGL type 1 licence. Synonyms and translations never become
    easier terms. A request failure does not count as a successful empty search.
    """
    query = normalize_query(query)
    queried_at = clock() if clock is not None else datetime.now(UTC)
    if queried_at.tzinfo is None or queried_at.utcoffset() is None:
        raise ValueError("조회 시각에는 시간대가 필요합니다.")

    with ExitStack() as stack:
        checked: list[Provider] = []
        entries: tuple[GlossaryEntry, ...] = ()
        for provider, supplied_client in (
            ("onterm", onterm_client),
            ("opendict", opendict_client),
        ):
            client = supplied_client
            if client is None:
                if settings is None:
                    settings = GlossarySettings.from_env()
                if provider == "onterm":
                    client = stack.enter_context(OnTermClient(settings.key_for(provider)))
                else:
                    client = stack.enter_context(
                        DictionaryClient(provider, settings.key_for(provider))
                    )
            entries = client.lookup(query)
            if any(entry.provider != provider for entry in entries):
                raise ValueError("요청한 사전과 반환된 항목의 출처가 일치하지 않습니다.")
            checked.append(provider)
            if entries:
                break

    return GlossaryLookup(
        query=query,
        status="found" if entries else "not_found",
        entries=entries,
        providers_checked=tuple(checked),
        queried_at=queried_at,
    )


def lookup_glossary(
    conn: Connection,
    query: str,
    *,
    refresh: bool = False,
    onterm_client: LookupClient | None = None,
    opendict_client: LookupClient | None = None,
    settings: GlossarySettings | None = None,
    clock: Callable[[], datetime] | None = None,
) -> GlossaryLookup:
    """Reuse until manual refresh, then save and reread a complete API result.

    The caller owns the connection and transaction. Neither network failures nor
    incomplete searches are saved. Save errors leave the previous cache intact
    and propagate as failures. This function never commits the caller's work.
    """
    query = normalize_query(query)
    cached = active_cached_lookup(get_glossary(conn, query))
    if cached is not None and not refresh:
        return reparse_refinements(cached)
    result = reparse_refinements(
        query_glossary(
            query,
            onterm_client=onterm_client,
            opendict_client=opendict_client,
            settings=settings,
            clock=clock,
        )
    )
    save_glossary(conn, result)
    stored = active_cached_lookup(get_glossary(conn, query))
    if stored is None:
        raise RuntimeError("용어 저장 후 조회 결과를 확인하지 못했습니다.")
    return reparse_refinements(stored).model_copy(update={"from_cache": False})
