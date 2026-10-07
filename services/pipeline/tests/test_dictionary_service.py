"""Verify the direct Ourmalsam cache flow without credentials or live APIs."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pipeline.glossary import dictionary_service as service
from pipeline.glossary.client import GlossaryAPIError
from pipeline.glossary.config import GlossaryConfigurationError, GlossarySettings
from pipeline.glossary.models import GlossaryEntry, GlossaryLookup, NormInfo
from pipeline.storage.glossary import GlossaryStorageError

NOW = datetime(2026, 10, 7, 2, tzinfo=UTC)


def _entry(provider="opendict", entry_id="10", sense_id="001", **changes) -> GlossaryEntry:
    host = "opendict" if provider == "opendict" else "krdict"
    return GlossaryEntry(
        **{
            "provider": provider,
            "entry_id": entry_id,
            "sense_id": sense_id,
            "headword": "익일",
            "definition": "어느 날의 다음 날.",
            "source_url": f"https://{host}.korean.go.kr/entry/{entry_id}",
            **changes,
        }
    )


def _onterm_entry() -> GlossaryEntry:
    return GlossaryEntry(
        provider="onterm",
        entry_id="sha256:" + "a" * 64,
        sense_id="content",
        headword="익일",
        definition=None,
        easy_terms=("다음 날",),
        source_url="https://kli.korean.go.kr/term/record",
        source_institution="서울특별시",
        source_glossary="알기 쉬운 행정 용어",
        entry_id_kind="content_hash",
    )


def _result(entries=(), providers=("opendict",), **changes) -> GlossaryLookup:
    return GlossaryLookup(
        **{
            "query": "익일",
            "status": "found" if entries else "not_found",
            "entries": entries,
            "providers_checked": providers,
            "queried_at": NOW - timedelta(days=1),
            "from_cache": True,
            **changes,
        }
    )


@pytest.fixture
def connection():
    conn = MagicMock()
    conn.autocommit = False
    return conn


@pytest.fixture
def store(monkeypatch):
    rows = {}
    saves = []
    events = []

    def get(conn, query):
        events.append(("read", query))
        return rows.get(query)

    def save(conn, result):
        events.append(("save", result.query))
        saves.append(result)
        prior = rows.get(result.query)
        if prior is not None and prior.queried_at >= result.queried_at:
            return
        if (
            prior is not None
            and result.status == "not_found"
            and any(entry.provider == "opendict" for entry in prior.entries)
        ):
            raise GlossaryStorageError("검색 결과가 없어 기존 용어 설명을 보존했습니다.")
        rows[result.query] = result.model_copy(update={"from_cache": True})

    monkeypatch.setattr(service, "get_glossary", get)
    monkeypatch.setattr(service, "save_glossary", save)
    return rows, saves, events


def _forbid_credentials(monkeypatch):
    forbidden = MagicMock(side_effect=AssertionError("must not inspect credentials"))
    monkeypatch.setattr(service, "_opendict_key", forbidden)
    monkeypatch.setattr(service, "DictionaryClient", forbidden)
    monkeypatch.setattr(GlossarySettings, "from_env", forbidden)
    return forbidden


def test_repeated_cached_query_needs_no_credentials_network_or_clock(
    store, connection, monkeypatch
) -> None:
    rows, saves, _ = store
    meanings = (_entry(sense_id="001"), _entry(sense_id="002"))
    rows["익일"] = _result(meanings, queried_at=datetime(2000, 1, 1, tzinfo=UTC))
    credentials = _forbid_credentials(monkeypatch)
    client = MagicMock()
    client.lookup.side_effect = AssertionError("cached queries must not make requests")
    clock = MagicMock(side_effect=AssertionError("cached queries need no new timestamp"))
    first = service.lookup_dictionary_definition(
        connection, " 익일 \n", opendict_client=client, clock=clock
    )
    second = service.lookup_dictionary_definition(connection, "익일")
    assert first == second == rows["익일"]
    assert first.entries == meanings and first.from_cache
    assert saves == []
    credentials.assert_not_called()
    client.lookup.assert_not_called()
    clock.assert_not_called()
    connection.commit.assert_not_called()
    connection.close.assert_not_called()


def test_onterm_only_found_cache_cannot_short_circuit_ourmalsam(store, connection) -> None:
    rows, saves, _ = store
    old = _result((_onterm_entry(),), providers=("onterm",))
    rows["익일"] = old
    client = MagicMock()
    client.lookup.return_value = (_entry(),)
    result = service.lookup_dictionary_definition(
        connection, "익일", opendict_client=client, clock=lambda: NOW
    )
    client.lookup.assert_called_once_with("익일")
    assert result.entries == (_entry(),)
    assert result.providers_checked == ("opendict",)
    assert result.from_cache is False
    assert saves == [result]
    assert old.entries == (_onterm_entry(),)


def test_onterm_only_cache_can_become_successful_cached_ourmalsam_not_found(
    store, connection, monkeypatch
) -> None:
    rows, saves, _ = store
    rows["익일"] = _result((_onterm_entry(),), providers=("onterm",))
    client = MagicMock()
    client.lookup.return_value = ()
    first = service.lookup_dictionary_definition(
        connection, "익일", opendict_client=client, clock=lambda: NOW
    )
    _forbid_credentials(monkeypatch)
    second = service.lookup_dictionary_definition(connection, "익일", opendict_client=client)
    assert first.status == second.status == "not_found"
    assert first.entries == second.entries == ()
    assert first.providers_checked == second.providers_checked == ("opendict",)
    assert not first.from_cache and second.from_cache
    assert len(saves) == 1
    client.lookup.assert_called_once_with("익일")


def test_mixed_legacy_cache_filters_sources_and_preserves_every_ourmalsam_meaning(
    store, connection, monkeypatch
) -> None:
    rows, saves, _ = store
    meanings = (_entry(sense_id="001"), _entry(sense_id="002"), _entry(entry_id="20"))
    old = _result(
        (_onterm_entry(), meanings[0], _entry("krdict"), *meanings[1:]),
        providers=("onterm", "opendict", "krdict"),
    )
    rows["익일"] = old
    _forbid_credentials(monkeypatch)
    result = service.lookup_dictionary_definition(connection, "익일")
    assert result.entries == meanings
    assert result.providers_checked == ("opendict",)
    assert result.status == "found" and result.from_cache
    assert rows["익일"] is old and len(old.entries) == 5
    assert saves == []


@pytest.mark.parametrize("providers", [("opendict",), ("onterm", "opendict")])
def test_successful_empty_ourmalsam_cache_is_reused(store, connection, monkeypatch, providers):
    rows, saves, _ = store
    rows["익일"] = _result(providers=providers)
    _forbid_credentials(monkeypatch)
    result = service.lookup_dictionary_definition(connection, "익일")
    assert result.status == "not_found" and result.entries == () and result.from_cache
    assert result.providers_checked == ("opendict",)
    assert saves == []


def test_checked_ourmalsam_empty_in_historical_other_source_result_is_reused(
    store, connection, monkeypatch
) -> None:
    rows, saves, _ = store
    rows["익일"] = _result((_entry("krdict"),), providers=("onterm", "opendict", "krdict"))
    _forbid_credentials(monkeypatch)
    result = service.lookup_dictionary_definition(connection, "익일")
    assert result.status == "not_found" and result.entries == () and result.from_cache
    assert result.providers_checked == ("opendict",)
    assert saves == []


@pytest.mark.parametrize("code", ["http", "authentication", "rate_limit", "timeout"])
def test_api_failure_is_never_saved_and_preserves_prior_cache(store, connection, code) -> None:
    rows, saves, _ = store
    old = _result((_entry(),))
    rows["익일"] = old
    error = GlossaryAPIError("opendict", code)
    client = MagicMock()
    client.lookup.side_effect = error
    with pytest.raises(GlossaryAPIError) as caught:
        service.lookup_dictionary_definition(
            connection, "익일", refresh=True, opendict_client=client, clock=lambda: NOW
        )
    assert caught.value is error
    assert rows["익일"] is old and saves == []
    connection.commit.assert_not_called()


def test_first_failure_does_not_create_negative_cache(store, connection) -> None:
    rows, saves, _ = store
    client = MagicMock()
    client.lookup.side_effect = GlossaryAPIError("opendict", "rate_limit")
    with pytest.raises(GlossaryAPIError):
        service.lookup_dictionary_definition(connection, "익일", opendict_client=client)
    assert rows == {} and saves == []


def test_explicit_refresh_with_empty_result_preserves_existing_ourmalsam_meanings(
    store, connection
) -> None:
    rows, _, _ = store
    old = _result((_entry(),))
    rows["익일"] = old
    client = MagicMock()
    client.lookup.return_value = ()
    with pytest.raises(GlossaryStorageError, match="보존"):
        service.lookup_dictionary_definition(
            connection, "익일", refresh=True, opendict_client=client, clock=lambda: NOW
        )
    assert rows["익일"] is old


def test_query_preserves_all_senses_fields_and_original_word(monkeypatch) -> None:
    forbidden = MagicMock(side_effect=AssertionError("explicit query must not access the DB"))
    monkeypatch.setattr(service, "get_glossary", forbidden)
    monkeypatch.setattr(service, "save_glossary", forbidden)
    note = NormInfo(type="순화", description="‘익일’을 ‘다음 날’로 순화.")
    meanings = (
        _entry(norm_info=(note,), easy_terms=("다음 날",)),
        _entry(sense_id="002", definition="다른 뜻풀이."),
        _entry(entry_id="20", original_language="翌日"),
    )
    client = MagicMock()
    client.lookup.return_value = meanings
    result = service.query_dictionary_definition(
        " \t익일 \n", opendict_client=client, clock=lambda: NOW
    )
    assert result.query == "익일" and result.queried_at == NOW
    assert result.entries == meanings and result.providers_checked == ("opendict",)
    assert result.status == "found" and not result.from_cache
    client.lookup.assert_called_once_with("익일")
    client.__enter__.assert_not_called()
    client.close.assert_not_called()


def test_successful_query_saves_then_rereads_store_result(store, connection, monkeypatch) -> None:
    rows, saves, events = store
    client = MagicMock()
    client.lookup.return_value = (_entry(),)
    stored_entry = _entry(definition="저장 시점에 확인한 최신 뜻풀이.")

    def save(conn, result):
        events.append(("save", result.query))
        saves.append(result)
        rows[result.query] = result.model_copy(
            update={"entries": (stored_entry,), "from_cache": True}
        )

    monkeypatch.setattr(service, "save_glossary", save)
    result = service.lookup_dictionary_definition(
        connection, "익일", opendict_client=client, clock=lambda: NOW
    )
    assert events == [("read", "익일"), ("save", "익일"), ("read", "익일")]
    assert result.entries == (stored_entry,) and not result.from_cache
    connection.commit.assert_not_called()
    connection.close.assert_not_called()


def test_lock_precedes_cache_read_and_uses_normalized_query(store, connection) -> None:
    rows, _, events = store
    rows["익일"] = _result((_entry(),))
    connection.execute.side_effect = lambda statement, args: events.append(("lock", args))
    service.lookup_dictionary_definition(connection, "  익일\t")
    assert events == [("lock", ("ourmalsam-definition:익일",)), ("read", "익일")]
    connection.execute.assert_called_once_with(
        "select pg_advisory_xact_lock(hashtextextended(%s, 2026100701))",
        ("ourmalsam-definition:익일",),
    )
    connection.transaction.assert_not_called()
    connection.commit.assert_not_called()


def test_autocommit_rejected_before_lock_cache_or_network(store, connection) -> None:
    _, saves, events = store
    connection.autocommit = True
    client = MagicMock()
    with pytest.raises(GlossaryStorageError, match="autocommit"):
        service.lookup_dictionary_definition(connection, "익일", opendict_client=client)
    connection.execute.assert_not_called()
    client.lookup.assert_not_called()
    assert saves == events == []


def test_owned_client_reads_only_ourmalsam_setting_and_closes(monkeypatch) -> None:
    class OurmalsamSettings:
        opendict_api_key = "  ourmalsam-fake  "

        @property
        def onterm_api_key(self):
            raise AssertionError("must not access OnTerm credentials")

        def key_for(self, provider):
            raise AssertionError("legacy key_for inspects other providers")

    client = MagicMock()
    client.__enter__.return_value = client
    client.lookup.return_value = ()
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr(service, "DictionaryClient", constructor)
    result = service.query_dictionary_definition("익일", settings=OurmalsamSettings())
    constructor.assert_called_once_with("opendict", "ourmalsam-fake")
    client.lookup.assert_called_once_with("익일")
    client.__exit__.assert_called_once()
    assert result.status == "not_found"


def test_default_key_loader_ignores_onterm_environment_and_legacy_loader(monkeypatch) -> None:
    monkeypatch.setenv("OPENDICT_API_KEY", "  ourmalsam-fake  ")
    monkeypatch.setenv("ONTERM_API_KEY", "unrelated-fake")
    legacy = MagicMock(side_effect=AssertionError("must not load OnTerm settings"))
    monkeypatch.setattr(GlossarySettings, "from_env", legacy)
    local = MagicMock(side_effect=AssertionError("environment key should avoid the local file"))
    monkeypatch.setattr(service, "_local_opendict_key", local)
    client = MagicMock()
    client.__enter__.return_value = client
    client.lookup.return_value = ()
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr(service, "DictionaryClient", constructor)
    service.query_dictionary_definition("익일")
    constructor.assert_called_once_with("opendict", "ourmalsam-fake")
    legacy.assert_not_called()
    local.assert_not_called()


def test_local_key_loader_ignores_invalid_unrelated_key(tmp_path: Path, monkeypatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        'ONTERM_API_KEY="invalid quote\nexport OPENDICT_API_KEY="ourmalsam-fake" # local\n',
        encoding="utf-8-sig",
    )
    monkeypatch.delenv("OPENDICT_API_KEY", raising=False)
    monkeypatch.setattr(service, "DEFAULT_ENV_PATH", dotenv)
    assert service._opendict_key(None) == "ourmalsam-fake"


def test_missing_ourmalsam_key_is_a_safe_configuration_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENDICT_API_KEY", raising=False)
    monkeypatch.setenv("ONTERM_API_KEY", "unrelated-fake")
    monkeypatch.setattr(service, "DEFAULT_ENV_PATH", tmp_path / "absent.env")
    with pytest.raises(GlossaryConfigurationError, match="OPENDICT_API_KEY") as caught:
        service.query_dictionary_definition("익일")
    assert "unrelated-fake" not in str(caught.value)


def test_mismatched_provider_never_reaches_storage(store, connection) -> None:
    _, saves, _ = store
    client = MagicMock()
    client.lookup.return_value = (_entry("krdict"),)
    with pytest.raises(ValueError, match="출처"):
        service.lookup_dictionary_definition(connection, "익일", opendict_client=client)
    assert saves == []


def test_naive_clock_rejected_before_keys_or_network(monkeypatch) -> None:
    forbidden = _forbid_credentials(monkeypatch)
    client = MagicMock()
    with pytest.raises(ValueError, match="시간대"):
        service.query_dictionary_definition(
            "익일", opendict_client=client, clock=lambda: datetime(2026, 10, 7)
        )
    client.lookup.assert_not_called()
    forbidden.assert_not_called()


def test_save_failure_is_propagated_without_returning_success(store, connection, monkeypatch):
    rows, _, _ = store
    old = _result((_entry(),))
    rows["익일"] = old
    client = MagicMock()
    client.lookup.return_value = (_entry(sense_id="002"),)
    monkeypatch.setattr(
        service, "save_glossary", MagicMock(side_effect=RuntimeError("offline DB failure"))
    )
    with pytest.raises(RuntimeError, match="DB failure"):
        service.lookup_dictionary_definition(
            connection, "익일", refresh=True, opendict_client=client
        )
    assert rows["익일"] is old


def test_missing_saved_ourmalsam_result_is_not_returned_as_success(store, connection, monkeypatch):
    client = MagicMock()
    client.lookup.return_value = (_entry(),)
    monkeypatch.setattr(service, "save_glossary", MagicMock())
    with pytest.raises(RuntimeError, match="저장 후"):
        service.lookup_dictionary_definition(connection, "익일", opendict_client=client)


def test_explicit_refresh_repairs_corrupt_cache_without_reading_old_payload(
    connection, monkeypatch
) -> None:
    stored = None
    events = []

    def get(conn, query):
        events.append("read")
        if stored is None:
            raise ValueError("corrupt saved dictionary entry")
        return stored

    def save(conn, result):
        nonlocal stored
        events.append("save")
        stored = result.model_copy(update={"from_cache": True})

    read = MagicMock(side_effect=get)
    monkeypatch.setattr(service, "get_glossary", read)
    monkeypatch.setattr(service, "save_glossary", save)
    meanings = (_entry(), _entry(sense_id="002"))
    client = MagicMock()
    client.lookup.return_value = meanings
    result = service.lookup_dictionary_definition(
        connection, "익일", refresh=True, opendict_client=client, clock=lambda: NOW
    )
    assert events == ["save", "read"]
    read.assert_called_once_with(connection, "익일")
    client.lookup.assert_called_once_with("익일")
    assert result.entries == meanings and not result.from_cache
    connection.commit.assert_not_called()


def test_failed_explicit_refresh_preserves_corrupt_old_cache_without_reading_it(
    connection, monkeypatch
) -> None:
    old_rows = {"익일": "corrupt saved dictionary entry"}
    read = MagicMock(side_effect=ValueError("corrupt saved dictionary entry"))
    save = MagicMock(side_effect=lambda conn, result: old_rows.update({result.query: result}))
    monkeypatch.setattr(service, "get_glossary", read)
    monkeypatch.setattr(service, "save_glossary", save)
    client = MagicMock()
    client.lookup.side_effect = GlossaryAPIError("opendict", "authentication")
    with pytest.raises(GlossaryAPIError, match="authentication"):
        service.lookup_dictionary_definition(
            connection, "익일", refresh=True, opendict_client=client, clock=lambda: NOW
        )
    read.assert_not_called()
    save.assert_not_called()
    assert old_rows == {"익일": "corrupt saved dictionary entry"}
    connection.commit.assert_not_called()
