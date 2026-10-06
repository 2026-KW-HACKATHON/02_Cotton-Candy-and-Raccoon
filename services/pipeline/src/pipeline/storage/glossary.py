"""Save complete dictionary lookups atomically in the caller's transaction."""

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from pipeline.glossary.models import (
    ACTIVE_PROVIDERS,
    GlossaryEntry,
    GlossaryLookup,
    normalize_query,
)

_LOOKUP = """
select l.status, l.providers_checked, l.queried_at,
       e.provider, e.entry_id, e.sense_id, e.headword, e.definition, e.pos,
       e.original_language, e.easy_terms, e.norm_info,
       e.source_url, e.source_name, e.license, e.license_url,
       e.source_institution, e.source_glossary, e.entry_id_kind
from public.glossary_lookups l
left join public.glossary_lookup_entries link on link.query = l.query
left join public.glossary_entries e
  on (e.provider, e.entry_id, e.sense_id) =
     (link.provider, link.entry_id, link.sense_id)
where l.query = %s
order by link.position
"""

_UPSERT_ENTRY = """
insert into public.glossary_entries (
    provider, entry_id, sense_id, headword, definition, pos, original_language,
    easy_terms, norm_info, source_url, source_name, license, license_url,
    source_institution, source_glossary, entry_id_kind, queried_at
) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (provider, entry_id, sense_id) do update set
    headword = excluded.headword,
    definition = excluded.definition,
    pos = excluded.pos,
    original_language = excluded.original_language,
    easy_terms = excluded.easy_terms,
    norm_info = excluded.norm_info,
    source_url = excluded.source_url,
    source_name = excluded.source_name,
    license = excluded.license,
    license_url = excluded.license_url,
    source_institution = excluded.source_institution,
    source_glossary = excluded.source_glossary,
    entry_id_kind = excluded.entry_id_kind,
    queried_at = excluded.queried_at
where glossary_entries.queried_at < excluded.queried_at
"""


class GlossaryStorageError(RuntimeError):
    """A refresh cannot safely replace the saved dictionary results."""


def get_glossary(conn: Connection, query: str) -> GlossaryLookup | None:
    """Read one consistent lookup snapshot; never commit or close the connection."""
    query = normalize_query(query)
    with conn.cursor(row_factory=tuple_row) as cursor:
        cursor.execute(_LOOKUP, (query,))
        rows = cursor.fetchall()
    if not rows:
        return None
    status, providers, queried_at = rows[0][:3]
    entries = tuple(
        GlossaryEntry(
            provider=row[3],
            entry_id=row[4],
            sense_id=row[5],
            headword=row[6],
            definition=row[7],
            part_of_speech=row[8],
            original_language=row[9],
            easy_terms=row[10],
            norm_info=row[11],
            source_url=row[12],
            source_name=row[13],
            license=row[14],
            license_url=row[15],
            source_institution=row[16],
            source_glossary=row[17],
            entry_id_kind=row[18],
        )
        for row in rows
        if row[3] is not None
    )
    return GlossaryLookup(
        query=query,
        status=status,
        entries=entries,
        providers_checked=providers,
        queried_at=queried_at,
        from_cache=True,
    )


def _entry_values(entry: GlossaryEntry, result: GlossaryLookup) -> tuple[object, ...]:
    return (
        *entry.identity,
        entry.headword,
        entry.definition,
        entry.part_of_speech,
        entry.original_language,
        Jsonb(list(entry.easy_terms)),
        Jsonb([info.model_dump(mode="json") for info in entry.norm_info]),
        entry.source_url,
        entry.source_name,
        entry.license,
        entry.license_url,
        entry.source_institution,
        entry.source_glossary,
        entry.entry_id_kind,
        result.queried_at,
    )


def save_glossary(conn: Connection, result: GlossaryLookup) -> None:
    """Save a complete successful refresh without committing the caller's work.

    A newer saved lookup wins over an older response. Active dictionary meanings
    are never replaced with an empty result. A historical excluded-only lookup
    can become not_found after both active sources succeed with empty searches;
    its original dictionary entry rows are preserved.
    An idle non-autocommit connection gets an outer transaction so the nested
    transaction below is a savepoint; the caller still owns its final commit.
    """
    if conn.autocommit:
        raise GlossaryStorageError("용어 저장에는 autocommit이 꺼진 연결이 필요합니다.")
    if conn.info.transaction_status == TransactionStatus.IDLE:
        # A regular statement lets psycopg start BEGIN once, rather than issuing
        # an explicit BEGIN after its own automatic BEGIN.
        conn.execute("select 1")

    with conn.transaction(), conn.cursor(row_factory=tuple_row) as cursor:
        # INSERT serializes concurrent first writes through the query primary key.
        cursor.execute(
            "insert into public.glossary_lookups "
            "(query, status, providers_checked, queried_at) values (%s, %s, %s, %s) "
            "on conflict (query) do nothing returning query",
            (result.query, result.status, list(result.providers_checked), result.queried_at),
        )
        is_new = cursor.fetchone() is not None
        cursor.execute(
            "select status, queried_at from public.glossary_lookups where query = %s for update",
            (result.query,),
        )
        stored = cursor.fetchone()
        if stored is None:
            raise GlossaryStorageError("용어 저장 대상 조회를 확인하지 못했습니다.")
        if not is_new and stored[1] >= result.queried_at:
            return
        if stored[0] == "found" and result.status == "not_found":
            if result.providers_checked != ACTIVE_PROVIDERS:
                raise GlossaryStorageError("검색 결과가 없어 기존 용어 설명을 보존했습니다.")
            cursor.execute(
                "select exists (select 1 from public.glossary_lookup_entries "
                "where query = %s and provider = any(%s))",
                (result.query, list(ACTIVE_PROVIDERS)),
            )
            active_meanings = cursor.fetchone()
            if active_meanings is None or active_meanings[0] is not False:
                raise GlossaryStorageError("검색 결과가 없어 기존 용어 설명을 보존했습니다.")

        # Stable locking order avoids deadlocks for shared meanings in two queries.
        for entry in sorted(result.entries, key=lambda value: value.identity):
            cursor.execute(_UPSERT_ENTRY, _entry_values(entry, result))
        cursor.execute(
            "delete from public.glossary_lookup_entries where query = %s", (result.query,)
        )
        for position, entry in enumerate(result.entries):
            cursor.execute(
                "insert into public.glossary_lookup_entries "
                "(query, provider, entry_id, sense_id, position) values (%s, %s, %s, %s, %s)",
                (result.query, *entry.identity, position),
            )
        cursor.execute(
            "update public.glossary_lookups set status = %s, providers_checked = %s, "
            "queried_at = %s where query = %s",
            (result.status, list(result.providers_checked), result.queried_at, result.query),
        )
