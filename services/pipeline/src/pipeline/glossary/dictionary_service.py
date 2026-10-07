"""Query Ourmalsam definitions and reuse complete lookups across notices."""

import os
import re
from collections.abc import Callable
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from psycopg import Connection

from pipeline.glossary.client import DictionaryDefinitionClient as DictionaryClient
from pipeline.glossary.config import (
    DEFAULT_ENV_PATH,
    GlossaryConfigurationError,
    GlossarySettings,
)
from pipeline.glossary.models import GlossaryEntry, GlossaryLookup, normalize_query
from pipeline.storage.glossary import GlossaryStorageError, get_glossary, save_glossary

_QUERY_LOCK = "select pg_advisory_xact_lock(hashtextextended(%s, 2026100701))"
_LOCK_NAMESPACE = "ourmalsam-definition:"


class LookupClient(Protocol):
    """The Ourmalsam API client's interface, also implemented by offline doubles."""

    def lookup(self, query: str) -> tuple[GlossaryEntry, ...]: ...


def _local_opendict_key(dotenv_path: Path) -> str:
    """Read only the Ourmalsam setting; unrelated keys do not affect this flow."""
    try:
        lines = dotenv_path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError):
        raise GlossaryConfigurationError("용어 API 설정 파일을 읽을 수 없습니다.") from None

    key = ""
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != "OPENDICT_API_KEY":
            continue
        value = value.strip()
        if value.startswith(("'", '"')):
            end = value.find(value[0], 1)
            if end < 0 or (suffix := value[end + 1 :].strip()) and not suffix.startswith("#"):
                raise GlossaryConfigurationError(
                    "용어 API 설정의 따옴표 또는 주석이 잘못되었습니다."
                )
            key = value[1:end]
        else:
            key = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    return key.strip()


def _opendict_key(settings: GlossarySettings | None) -> str:
    # The legacy settings loader and key_for() inspect OnTerm settings as well.
    # This direct dictionary flow needs only its own provider's credential.
    key = (
        settings.opendict_api_key.strip()
        if settings is not None
        else os.environ.get("OPENDICT_API_KEY", "").strip() or _local_opendict_key(DEFAULT_ENV_PATH)
    )
    if not key:
        raise GlossaryConfigurationError("services/pipeline/.env에 OPENDICT_API_KEY를 설정하세요.")
    return key


def _dictionary_cached_lookup(cached: GlossaryLookup | None) -> GlossaryLookup | None:
    """Only a completed Ourmalsam search can answer a dictionary cache lookup."""
    if cached is None or "opendict" not in cached.providers_checked:
        return None
    entries = tuple(entry for entry in cached.entries if entry.provider == "opendict")
    return GlossaryLookup(
        query=cached.query,
        status="found" if entries else "not_found",
        entries=entries,
        providers_checked=("opendict",),
        queried_at=cached.queried_at,
        from_cache=True,
    )


def query_dictionary_definition(
    query: str,
    *,
    opendict_client: LookupClient | None = None,
    settings: GlossarySettings | None = None,
    clock: Callable[[], datetime] | None = None,
) -> GlossaryLookup:
    """Explicitly query Ourmalsam and retain every returned official meaning.

    This lower-level operation performs no cache reads or writes. API errors
    propagate, so failures cannot be mistaken for a successful empty search.
    The service does not select a meaning or rewrite the requested word.
    """
    query = normalize_query(query)
    queried_at = clock() if clock is not None else datetime.now(UTC)
    if queried_at.tzinfo is None or queried_at.utcoffset() is None:
        raise ValueError("조회 시각에는 시간대가 필요합니다.")

    with ExitStack() as stack:
        client = opendict_client
        if client is None:
            client = stack.enter_context(DictionaryClient("opendict", _opendict_key(settings)))
        entries = client.lookup(query)
        if any(entry.provider != "opendict" for entry in entries):
            raise ValueError("요청한 사전과 반환된 항목의 출처가 일치하지 않습니다.")

    return GlossaryLookup(
        query=query,
        status="found" if entries else "not_found",
        entries=entries,
        providers_checked=("opendict",),
        queried_at=queried_at,
    )


def lookup_dictionary_definition(
    conn: Connection,
    query: str,
    *,
    refresh: bool = False,
    opendict_client: LookupClient | None = None,
    settings: GlossarySettings | None = None,
    clock: Callable[[], datetime] | None = None,
) -> GlossaryLookup:
    """Reuse complete Ourmalsam results globally until an explicit refresh.

    A transaction advisory lock precedes the cache read, serializing concurrent
    first requests for the same normalized query. Its statement starts an outer
    transaction on an idle psycopg connection before storage uses a savepoint.
    The caller owns that transaction and connection; this function never commits.
    API failures are never saved. Storage preserves existing Ourmalsam meanings
    if a manual refresh succeeds with no results.
    """
    query = normalize_query(query)
    if conn.autocommit:
        raise GlossaryStorageError("용어 조회에는 autocommit이 꺼진 연결이 필요합니다.")
    conn.execute(_QUERY_LOCK, (_LOCK_NAMESPACE + query,))
    if not refresh:
        cached = _dictionary_cached_lookup(get_glossary(conn, query))
        if cached is not None:
            return cached

    result = query_dictionary_definition(
        query,
        opendict_client=opendict_client,
        settings=settings,
        clock=clock,
    )
    save_glossary(conn, result)
    stored = _dictionary_cached_lookup(get_glossary(conn, query))
    if stored is None:
        raise RuntimeError("우리말샘 뜻풀이 저장 후 조회 결과를 확인하지 못했습니다.")
    return stored.model_copy(update={"from_cache": False})
