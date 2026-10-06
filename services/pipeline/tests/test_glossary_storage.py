"""Atomic dictionary storage; DB cases require a dedicated local test database."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from pipeline.glossary.client import DictionaryClient
from pipeline.glossary.models import GlossaryEntry, GlossaryLookup, NormInfo
from pipeline.glossary.service import lookup_glossary
from pipeline.storage.glossary import GlossaryStorageError, get_glossary, save_glossary

_TIME = datetime(2026, 10, 5, 9, tzinfo=UTC)
_MIGRATION = Path(__file__).resolve().parents[3] / "supabase/migrations/20261005000000_glossary.sql"


def _entry(provider: str = "opendict", sense_id: str = "1", **changes) -> GlossaryEntry:
    return GlossaryEntry.model_validate(
        {
            "provider": provider,
            "entry_id": "123",
            "sense_id": sense_id,
            "headword": "익일",
            "definition": "일정한 날의 바로 다음 날.",
            "part_of_speech": "명사",
            "original_language": "翌日",
            "easy_terms": ("다음 날", "이튿날"),
            "norm_info": (NormInfo(type="순화", description="다음 날로 순화한다."),),
            "source_url": f"https://{provider}.korean.go.kr/word/123",
            **changes,
        }
    )


def _onterm_entry(**changes) -> GlossaryEntry:
    return GlossaryEntry.model_validate(
        {
            "provider": "onterm",
            "entry_id": "sha256:" + "a" * 64,
            "sense_id": "content",
            "entry_id_kind": "content_hash",
            "headword": "익일",
            "definition": None,
            "easy_terms": ("다음 날",),
            "source_url": "https://kli.korean.go.kr/term/record",
            "source_institution": "서울특별시",
            "source_glossary": "알기 쉬운 행정 용어",
            **changes,
        }
    )


def _lookup(*entries: GlossaryEntry, **changes) -> GlossaryLookup:
    return GlossaryLookup.model_validate(
        {
            "query": "익일",
            "status": "found" if entries else "not_found",
            "entries": entries,
            "providers_checked": ("opendict", "krdict"),
            "queried_at": _TIME,
            **changes,
        }
    )


def _mock_conn(*fetchone_results):
    conn = MagicMock()
    conn.autocommit = False
    conn.info.transaction_status = TransactionStatus.INTRANS
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = fetchone_results
    return conn, cursor


def test_absent_cache_uses_bound_normalized_query() -> None:
    conn, cursor = _mock_conn()
    cursor.fetchall.return_value = []
    query = "  익일'; delete from glossary_entries; --  "
    assert get_glossary(conn, query) is None
    statement, values = cursor.execute.call_args.args
    assert query.strip() not in statement
    assert values == (query.strip(),)
    conn.commit.assert_not_called()
    conn.close.assert_not_called()


def test_read_preserves_meaning_identity_norm_info_and_attribution() -> None:
    entry = _entry(source_institution="국립국어원", source_glossary="우리말샘")
    other = _entry("krdict", "2", easy_terms=(), norm_info=())
    onterm = _onterm_entry()
    conn, cursor = _mock_conn()
    cursor.fetchall.return_value = [
        (
            "found",
            ["onterm", "opendict", "krdict"],
            _TIME,
            *value.identity,
            value.headword,
            value.definition,
            value.part_of_speech,
            value.original_language,
            list(value.easy_terms),
            [info.model_dump() for info in value.norm_info],
            value.source_url,
            value.source_name,
            value.license,
            value.license_url,
            value.source_institution,
            value.source_glossary,
            value.entry_id_kind,
        )
        for value in (entry, other, onterm)
    ]
    result = get_glossary(conn, " 익일 ")
    assert result is not None
    assert result.from_cache is True
    assert result.entries == (entry, other, onterm)
    assert result.providers_checked == ("onterm", "opendict", "krdict")
    assert result.queried_at == _TIME


def test_read_not_found_is_cached_success_distinct_from_absence() -> None:
    conn, cursor = _mock_conn()
    cursor.fetchall.return_value = [("not_found", ["opendict", "krdict"], _TIME, *([None] * 16))]
    result = get_glossary(conn, "익일")
    assert result is not None
    assert result.status == "not_found"
    assert result.entries == ()
    assert result.from_cache


def test_save_binds_json_and_never_commits() -> None:
    conn, cursor = _mock_conn(("익일",), ("found", _TIME))
    entry = _entry()
    save_glossary(conn, _lookup(entry))
    entry_calls = [
        call
        for call in cursor.execute.call_args_list
        if "insert into public.glossary_entries" in call.args[0]
    ]
    assert len(entry_calls) == 1
    _, values = entry_calls[0].args
    assert values[:3] == entry.identity
    assert isinstance(values[7], Jsonb)
    assert values[7].obj == ["다음 날", "이튿날"]
    assert values[8].obj == [entry.norm_info[0].model_dump()]
    assert entry.definition not in entry_calls[0].args[0]
    assert conn.transaction.called
    conn.commit.assert_not_called()
    conn.close.assert_not_called()


def test_save_onterm_preserves_null_definition_content_identity_and_attribution() -> None:
    conn, cursor = _mock_conn(("익일",), ("found", _TIME))
    entry = _onterm_entry()
    save_glossary(conn, _lookup(entry, providers_checked=("onterm",)))
    entry_call = next(
        call
        for call in cursor.execute.call_args_list
        if "insert into public.glossary_entries" in call.args[0]
    )
    statement, values = entry_call.args
    assert values[:7] == (*entry.identity, entry.headword, None, None, None)
    assert values[7].obj == ["다음 날"]
    assert values[8].obj == []
    assert values[9:] == (
        "https://kli.korean.go.kr/term/record",
        "국립국어원 온용어",
        "KOGL 1",
        "https://www.kogl.or.kr/info/licenseType1.do",
        "서울특별시",
        "알기 쉬운 행정 용어",
        "content_hash",
        _TIME,
    )
    for field in ("source_institution", "source_glossary", "entry_id_kind"):
        assert f"{field} = excluded.{field}" in statement
    conn.commit.assert_not_called()
    conn.close.assert_not_called()


def test_empty_refresh_preserves_existing_meanings() -> None:
    conn, cursor = _mock_conn(None, ("found", _TIME))
    with pytest.raises(GlossaryStorageError, match="기존 용어 설명"):
        save_glossary(conn, _lookup(queried_at=_TIME + timedelta(days=1)))
    assert not any("delete from" in call.args[0] for call in cursor.execute.call_args_list)
    assert conn.transaction.return_value.__exit__.call_args.args[0] is GlossaryStorageError


@pytest.mark.parametrize("saved_time", [_TIME, _TIME + timedelta(seconds=1)])
def test_older_or_equal_result_does_not_replace_links(saved_time) -> None:
    conn, cursor = _mock_conn(None, ("found", saved_time))
    save_glossary(conn, _lookup(_entry()))
    assert len(cursor.execute.call_args_list) == 2


def test_write_failure_exits_savepoint_with_exception() -> None:
    conn, cursor = _mock_conn(("익일",), ("found", _TIME))
    cursor.execute.side_effect = [None, None, psycopg.DatabaseError("test failure")]
    with pytest.raises(psycopg.DatabaseError):
        save_glossary(conn, _lookup(_entry()))
    assert conn.transaction.return_value.__exit__.call_args.args[0] is psycopg.DatabaseError
    conn.commit.assert_not_called()


def test_idle_connection_keeps_outer_transaction_open() -> None:
    conn, _ = _mock_conn(("익일",), ("not_found", _TIME))
    conn.info.transaction_status = TransactionStatus.IDLE
    save_glossary(conn, _lookup())
    conn.execute.assert_called_once_with("select 1")
    conn.commit.assert_not_called()


def test_autocommit_is_rejected_before_writing() -> None:
    conn, cursor = _mock_conn()
    conn.autocommit = True
    with pytest.raises(GlossaryStorageError, match="autocommit"):
        save_glossary(conn, _lookup())
    cursor.execute.assert_not_called()
    conn.transaction.assert_not_called()


def _safe_test_database(database_url: str) -> dict[str, str]:
    info = conninfo_to_dict(database_url)
    if (
        info.get("host") not in {"localhost", "127.0.0.1", "::1"}
        or info.get("hostaddr") not in {None, "127.0.0.1", "::1"}
        or not info.get("dbname", "").startswith("pipeline_glossary_test_")
    ):
        raise ValueError("용어 통합 테스트는 전용 로컬 테스트 DB에서만 실행할 수 있습니다.")
    return info


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://localhost/postgres",
        "postgresql://remote.example/pipeline_glossary_test_demo",
        "dbname=pipeline_glossary_test_demo",
        "host=localhost hostaddr=203.0.113.1 dbname=pipeline_glossary_test_demo",
    ],
)
def test_integration_database_requires_dedicated_local_target(database_url) -> None:
    with pytest.raises(ValueError, match="전용 로컬"):
        _safe_test_database(database_url)


@pytest.fixture
def glossary_db():
    database_url = os.getenv("GLOSSARY_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("GLOSSARY_TEST_DATABASE_URL 미설정: 전용 PostgreSQL 테스트 미실행")
    conn = psycopg.connect(**_safe_test_database(database_url))
    try:
        conn.execute("select 1")
        for role in ("anon", "authenticated"):
            exists = conn.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone()
            if exists is None:
                # These constant names are created only in this rollback transaction.
                conn.execute(f"create role {role} nologin")
        conn.execute(_MIGRATION.read_text(encoding="utf-8"))
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_db_roundtrip_distinct_senses_and_refresh(glossary_db) -> None:
    entries = (_entry(sense_id="1"), _entry(sense_id="2"), _entry("krdict"))
    save_glossary(glossary_db, _lookup(*entries))
    cached = get_glossary(glossary_db, " 익일 ")
    assert cached is not None
    assert cached.entries == entries
    newer = _lookup(
        entries[2],
        entries[0].model_copy(update={"definition": "갱신한 뜻풀이"}),
        queried_at=_TIME + timedelta(days=1),
    )
    save_glossary(glossary_db, newer)
    cached = get_glossary(glossary_db, "익일")
    assert cached is not None
    assert cached.entries == newer.entries
    assert glossary_db.execute("select count(*) from public.glossary_entries").fetchone()[0] == 3


def test_db_synthetic_api_to_storage_then_cache_needs_no_more_requests(glossary_db) -> None:
    """Synthetic XML checks the client/service/real DB path, not the live API."""
    calls = []
    search_xml = """
    <channel><total>1</total><start>1</start><num>1</num><item>
      <word>익일</word><sense><target_code>123</target_code><sense_no>001</sense_no>
        <definition>테스트용 뜻풀이.</definition>
        <link>https://opendict.korean.go.kr/dictionary/view?sense_no=123</link>
      </sense></item></channel>
    """
    detail_xml = """
    <channel><total>1</total><item><target_code>123</target_code>
      <word_info><word>익일</word></word_info>
      <sense_info><sense_no>001</sense_no><definition>테스트용 뜻풀이.</definition>
        <pos>명사</pos><norm_info><type>순화</type>
          <desc>‘익일’을 ‘다음 날’로 순화.</desc>
        </norm_info></sense_info>
    </item></channel>
    """

    def handler(request):
        endpoint = request.url.path.rsplit("/", 1)[-1]
        calls.append(endpoint)
        if endpoint not in ("search", "view"):
            raise AssertionError("unexpected dictionary endpoint")
        return httpx.Response(200, text=search_xml if endpoint == "search" else detail_xml)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http,
        DictionaryClient("opendict", "synthetic-test-secret", client=http) as client,
    ):
        onterm_client = MagicMock()
        onterm_client.lookup.return_value = ()
        first = lookup_glossary(
            glossary_db,
            " 익일 ",
            onterm_client=onterm_client,
            opendict_client=client,
            clock=lambda: _TIME,
        )
    # Even this closed client must be unused when a saved result is reused.
    second = lookup_glossary(glossary_db, "익일", opendict_client=client)
    assert calls == ["search", "view"]
    assert first.from_cache is False and second.from_cache is True
    assert first.entries == second.entries
    assert first.entries[0].entry_id == "123" and first.entries[0].sense_id == "001"
    assert first.entries[0].easy_terms == ("다음 날",)
    assert first.entries[0].source_name == "국립국어원 우리말샘"
    assert first.providers_checked == ("onterm", "opendict")
    onterm_client.lookup.assert_called_once_with("익일")
    assert glossary_db.execute("select count(*) from public.glossary_entries").fetchone()[0] == 1


def test_db_onterm_roundtrip_preserves_null_definition_hash_identity_and_license(
    glossary_db,
) -> None:
    entries = (_onterm_entry(), _entry(), _entry("krdict"))
    save_glossary(
        glossary_db,
        _lookup(*entries, providers_checked=("onterm", "opendict", "krdict")),
    )
    cached = get_glossary(glossary_db, "익일")
    assert cached is not None
    assert cached.entries == entries
    assert cached.providers_checked == ("onterm", "opendict", "krdict")
    stored = glossary_db.execute(
        "select entry_id, sense_id, definition, source_institution, source_glossary, "
        "entry_id_kind, license, license_url from public.glossary_entries where provider = %s",
        ("onterm",),
    ).fetchone()
    assert stored == (
        entries[0].entry_id,
        "content",
        None,
        "서울특별시",
        "알기 쉬운 행정 용어",
        "content_hash",
        "KOGL 1",
        "https://www.kogl.or.kr/info/licenseType1.do",
    )


@pytest.mark.parametrize(
    ("column", "invalid_value"),
    [
        ("entry_id", "123"),
        ("sense_id", "1"),
        ("entry_id_kind", "provider_id"),
        ("easy_terms", Jsonb([])),
        ("source_institution", None),
        ("source_glossary", " "),
        ("source_name", "국립국어원 우리말샘"),
        ("source_url", "https://other.example/term/record"),
        ("license", "CC BY-SA 2.0 KR"),
        ("license_url", "https://creativecommons.org/licenses/by-sa/2.0/kr/"),
    ],
)
def test_db_onterm_contract_rejects_invalid_rows(glossary_db, column, invalid_value) -> None:
    entry = _onterm_entry()
    save_glossary(glossary_db, _lookup(entry, providers_checked=("onterm",)))
    with pytest.raises(psycopg.errors.CheckViolation):
        with glossary_db.transaction():
            glossary_db.execute(
                f"update public.glossary_entries set {column} = %s where provider = 'onterm'",
                (invalid_value,),
            )
    assert get_glossary(glossary_db, "익일").entries == (entry,)


@pytest.mark.parametrize("provider", ["opendict", "krdict"])
def test_db_dictionary_definition_remains_required(glossary_db, provider) -> None:
    save_glossary(glossary_db, _lookup(_entry(provider)))
    with pytest.raises(psycopg.errors.CheckViolation):
        with glossary_db.transaction():
            glossary_db.execute(
                "update public.glossary_entries set definition = null where provider = %s",
                (provider,),
            )


@pytest.mark.parametrize(
    "providers",
    [
        ("onterm", "onterm"),
        ("onterm", "opendict", "onterm"),
        ("onterm", "opendict", "opendict"),
    ],
)
def test_db_checked_providers_rejects_duplicates(glossary_db, providers) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        with glossary_db.transaction():
            glossary_db.execute(
                "insert into public.glossary_lookups "
                "(query, status, providers_checked, queried_at) values (%s, %s, %s, %s)",
                ("익일", "not_found", list(providers), _TIME),
            )


def test_db_empty_refresh_preserves_found_and_connection(glossary_db) -> None:
    initial = _lookup(_entry())
    save_glossary(glossary_db, initial)
    with pytest.raises(GlossaryStorageError, match="기존 용어 설명"):
        save_glossary(glossary_db, _lookup(queried_at=_TIME + timedelta(days=1)))
    cached = get_glossary(glossary_db, "익일")
    assert cached is not None and cached.entries == initial.entries
    assert cached.queried_at == _TIME


def test_db_old_response_cannot_overwrite_lookup_or_shared_meaning(glossary_db) -> None:
    new_entry = _entry(definition="최신 설명")
    save_glossary(glossary_db, _lookup(new_entry, queried_at=_TIME + timedelta(days=1)))
    save_glossary(glossary_db, _lookup(_entry()))
    # A different query can refer to the same identity without replacing its newer content.
    save_glossary(glossary_db, _lookup(_entry(), query="이튿날"))
    for query in ("익일", "이튿날"):
        cached = get_glossary(glossary_db, query)
        assert cached is not None and cached.entries == (new_entry,)


def test_db_failure_rolls_back_all_meaning_and_lookup_changes(glossary_db) -> None:
    save_glossary(glossary_db, _lookup(_entry()))
    glossary_db.execute(
        "alter table public.glossary_entries "
        "add constraint test_reject_headword check (headword <> 'BROKEN')"
    )
    incoming = _lookup(
        _entry(definition="이 설명도 롤백되어야 한다."),
        _entry(sense_id="2", headword="BROKEN"),
        queried_at=_TIME + timedelta(days=1),
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        save_glossary(glossary_db, incoming)
    cached = get_glossary(glossary_db, "익일")
    assert cached is not None and cached.entries == (_entry(),)
    assert cached.queried_at == _TIME
    assert glossary_db.execute("select count(*) from public.glossary_entries").fetchone()[0] == 1


def test_db_not_found_can_later_become_found(glossary_db) -> None:
    save_glossary(glossary_db, _lookup())
    assert get_glossary(glossary_db, "익일").status == "not_found"
    save_glossary(glossary_db, _lookup(_entry(), queried_at=_TIME + timedelta(days=1)))
    assert get_glossary(glossary_db, "익일").entries == (_entry(),)


def test_db_excluded_only_cache_can_be_replaced_by_complete_active_empty_lookup(glossary_db):
    historical = _entry("krdict")
    save_glossary(glossary_db, _lookup(historical, providers_checked=("krdict",)))
    onterm, opendict = MagicMock(), MagicMock()
    onterm.lookup.return_value = opendict.lookup.return_value = ()
    stored = lookup_glossary(
        glossary_db,
        "익일",
        onterm_client=onterm,
        opendict_client=opendict,
        clock=lambda: _TIME + timedelta(days=1),
    )
    assert stored.status == "not_found"
    assert stored.providers_checked == ("onterm", "opendict")
    onterm.lookup.assert_called_once_with("익일")
    opendict.lookup.assert_called_once_with("익일")
    onterm.reset_mock()
    opendict.reset_mock()
    cached = lookup_glossary(glossary_db, "익일", onterm_client=onterm, opendict_client=opendict)
    assert cached.status == "not_found" and cached.from_cache
    onterm.lookup.assert_not_called()
    opendict.lookup.assert_not_called()
    assert glossary_db.execute(
        "select headword from public.glossary_entries "
        "where provider = 'krdict' and entry_id = %s and sense_id = %s",
        (historical.entry_id, historical.sense_id),
    ).fetchone() == (historical.headword,)


def test_db_active_found_cache_is_preserved_when_both_active_searches_are_empty(glossary_db):
    save_glossary(glossary_db, _lookup(_entry(), providers_checked=("onterm", "opendict")))
    with pytest.raises(GlossaryStorageError, match="보존"):
        save_glossary(
            glossary_db,
            _lookup(providers_checked=("onterm", "opendict"), queried_at=_TIME + timedelta(days=1)),
        )
    assert get_glossary(glossary_db, "익일").entries == (_entry(),)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("provider", ["opendict", "onterm"])
def test_db_app_roles_only_read_all_three_tables(glossary_db, role, provider) -> None:
    entry = _onterm_entry() if provider == "onterm" else _entry()
    save_glossary(glossary_db, _lookup(entry, providers_checked=(provider,)))
    with glossary_db.transaction():
        glossary_db.execute(f"set local role {role}")
        assert get_glossary(glossary_db, "익일").entries == (entry,)
        for table in ("glossary_entries", "glossary_lookups", "glossary_lookup_entries"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with glossary_db.transaction():
                    glossary_db.execute(f"delete from public.{table}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with glossary_db.transaction():
                    glossary_db.execute(f"truncate public.{table}")
        glossary_db.execute("reset role")
