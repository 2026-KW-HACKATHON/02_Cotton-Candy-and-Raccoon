"""Verify provider preference, lazy credentials and repeat-query reuse without live APIs."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from pipeline.glossary import config
from pipeline.glossary import dictionary_service as service
from pipeline.glossary.client import GlossaryAPIError
from pipeline.glossary.config import GlossaryConfigurationError, GlossarySettings
from pipeline.glossary.models import GlossaryEntry, GlossaryLookup
from pipeline.storage.glossary import GlossaryStorageError

NOW = datetime(2026, 10, 7, 2, tzinfo=UTC)


def _entry(provider="stdict", sense_id="001"):
    return GlossaryEntry(
        provider=provider,
        entry_id="10",
        sense_id=sense_id,
        headword="익일",
        definition="어느 날의 다음 날.",
        source_url=f"https://{provider}.korean.go.kr/entry/10",
    )


def _result(entries=(), providers=("stdict", "opendict"), **changes):
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


def _client(entries=(), error=None):
    client = MagicMock()
    client.lookup.return_value = entries
    client.lookup.side_effect = error
    return client


@pytest.fixture
def connection():
    conn = MagicMock()
    conn.autocommit = False
    return conn


@pytest.fixture
def store(monkeypatch):
    rows, saves, events = {}, [], []

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
            and any(e.provider in service.DICTIONARY_PROVIDERS for e in prior.entries)
        ):
            raise GlossaryStorageError("검색 결과가 없어 기존 용어 설명을 보존했습니다.")
        rows[result.query] = result.model_copy(update={"from_cache": True})

    monkeypatch.setattr(service, "get_glossary", get)
    monkeypatch.setattr(service, "save_glossary", save)
    return rows, saves, events


def _forbid_credentials(monkeypatch):
    forbidden = MagicMock(side_effect=AssertionError("must not inspect credentials or network"))
    monkeypatch.setattr(service, "_provider_key", forbidden)
    monkeypatch.setattr(service, "DictionaryDefinitionClient", forbidden)
    monkeypatch.setattr(GlossarySettings, "from_env", forbidden)
    return forbidden


@pytest.mark.parametrize("provider", ["stdict", "opendict", None])
def test_complete_cache_repeats_need_no_credentials_network_or_clock(
    store, connection, monkeypatch, provider
):
    rows, saves, _ = store
    entries = (_entry(provider), _entry(provider, "002")) if provider else ()
    checked = ("stdict",) if provider == "stdict" else service.DICTIONARY_PROVIDERS
    rows["익일"] = _result(entries, checked, queried_at=datetime(2000, 1, 1, tzinfo=UTC))
    forbidden = _forbid_credentials(monkeypatch)
    clock = MagicMock(side_effect=AssertionError("cache needs no timestamp"))
    first = service.lookup_dictionary_definition(connection, " 익일 ", clock=clock)
    second = service.lookup_dictionary_definition(connection, "익일")
    assert first == second == rows["익일"] and first.from_cache and saves == []
    forbidden.assert_not_called()
    clock.assert_not_called()
    connection.commit.assert_not_called()
    connection.close.assert_not_called()


def test_standard_found_does_not_inspect_fallback_credentials_or_client(monkeypatch):
    meanings = (_entry(), _entry(sense_id="002"))
    stdict = _client(meanings)
    opendict = _client(error=AssertionError("fallback must be unused"))
    _forbid_credentials(monkeypatch)
    result = service.query_dictionary_definition(
        " 익일 ", stdict_client=stdict, opendict_client=opendict, clock=lambda: NOW
    )
    assert result.entries == meanings and result.providers_checked == ("stdict",)
    assert result.query == "익일" and result.queried_at == NOW and not result.from_cache
    stdict.lookup.assert_called_once_with("익일")
    opendict.lookup.assert_not_called()
    stdict.close.assert_not_called()
    opendict.close.assert_not_called()


@pytest.mark.parametrize("found", [True, False])
def test_only_successful_empty_standard_search_uses_ourmalsam(found):
    stdict = _client()
    meanings = (_entry("opendict"), _entry("opendict", "002")) if found else ()
    opendict = _client(meanings)
    result = service.query_dictionary_definition(
        "익일", stdict_client=stdict, opendict_client=opendict
    )
    assert result.entries == meanings and result.providers_checked == service.DICTIONARY_PROVIDERS
    assert result.status == ("found" if found else "not_found")
    stdict.lookup.assert_called_once_with("익일")
    opendict.lookup.assert_called_once_with("익일")


@pytest.mark.parametrize("provider", ["stdict", "opendict"])
@pytest.mark.parametrize("code", ["http", "authentication", "rate_limit", "timeout"])
def test_api_failure_is_not_empty_search_or_cache_success(store, connection, provider, code):
    rows, saves, _ = store
    prior = _result((_entry(),), ("stdict",))
    rows["익일"] = prior
    error = GlossaryAPIError(provider, code)
    stdict = _client(error=error if provider == "stdict" else None)
    opendict = _client(error=error if provider == "opendict" else None)
    with pytest.raises(GlossaryAPIError) as caught:
        service.lookup_dictionary_definition(
            connection, "익일", refresh=True, stdict_client=stdict, opendict_client=opendict
        )
    assert caught.value is error and rows["익일"] is prior and saves == []
    if provider == "stdict":
        opendict.lookup.assert_not_called()
    connection.commit.assert_not_called()


def test_first_failure_never_creates_negative_global_cache(store, connection):
    rows, saves, _ = store
    with pytest.raises(GlossaryAPIError):
        service.lookup_dictionary_definition(
            connection, "익일", stdict_client=_client(error=GlossaryAPIError("stdict", "timeout"))
        )
    assert rows == {} and saves == []


@pytest.mark.parametrize("prior_found", [True, False])
def test_historical_ourmalsam_cache_requires_standard_but_reuses_fallback(
    store, connection, monkeypatch, prior_found
):
    rows, saves, _ = store
    meanings = (_entry("opendict"),) if prior_found else ()
    rows["익일"] = _result(meanings, ("opendict",))
    stdict = _client()
    opendict = _client(error=AssertionError("saved fallback should be reused"))
    first = service.lookup_dictionary_definition(
        connection, "익일", stdict_client=stdict, opendict_client=opendict, clock=lambda: NOW
    )
    _forbid_credentials(monkeypatch)
    second = service.lookup_dictionary_definition(connection, "익일")
    assert first.entries == second.entries == meanings
    assert first.providers_checked == second.providers_checked == service.DICTIONARY_PROVIDERS
    assert not first.from_cache and second.from_cache and len(saves) == 1
    stdict.lookup.assert_called_once_with("익일")
    opendict.lookup.assert_not_called()


def test_standard_found_replaces_historical_ourmalsam_preference(store, connection):
    rows, _, _ = store
    rows["익일"] = _result((_entry("opendict"),), ("opendict",))
    standard = _client((_entry(),))
    fallback = _client(error=AssertionError("standard preference"))
    result = service.lookup_dictionary_definition(
        connection, "익일", stdict_client=standard, opendict_client=fallback, clock=lambda: NOW
    )
    assert result.entries == (_entry(),) and result.providers_checked == ("stdict",)
    fallback.lookup.assert_not_called()


def test_explicit_refresh_does_not_reuse_old_fallback(store, connection):
    rows, _, _ = store
    rows["익일"] = _result((_entry("opendict"),))
    fallback = _client((_entry("opendict", "002"),))
    result = service.lookup_dictionary_definition(
        connection,
        "익일",
        refresh=True,
        stdict_client=_client(),
        opendict_client=fallback,
        clock=lambda: NOW,
    )
    assert result.entries[0].sense_id == "002"
    fallback.lookup.assert_called_once_with("익일")


@pytest.mark.parametrize("provider", ["stdict", "opendict"])
def test_empty_refresh_preserves_existing_meanings(store, connection, provider):
    rows, _, _ = store
    prior = _result(
        (_entry(provider),), ("stdict",) if provider == "stdict" else ("stdict", "opendict")
    )
    rows["익일"] = prior
    with pytest.raises(GlossaryStorageError, match="보존"):
        service.lookup_dictionary_definition(
            connection,
            "익일",
            refresh=True,
            stdict_client=_client(),
            opendict_client=_client(),
            clock=lambda: NOW,
        )
    assert rows["익일"] is prior


def test_lock_precedes_cache_read_and_caller_owns_transaction(store, connection):
    rows, _, events = store
    rows["익일"] = _result((_entry(),), ("stdict",))
    connection.execute.side_effect = lambda sql, args: events.append(("lock", args))
    service.lookup_dictionary_definition(connection, " 익일 ")
    assert events == [("lock", ("dictionary-definition:익일",)), ("read", "익일")]
    connection.transaction.assert_not_called()
    connection.commit.assert_not_called()


def test_autocommit_rejected_before_lock_cache_or_requests(store, connection):
    _, _, events = store
    connection.autocommit = True
    with pytest.raises(GlossaryStorageError, match="autocommit"):
        service.lookup_dictionary_definition(connection, "익일")
    connection.execute.assert_not_called()
    assert events == []


@pytest.mark.parametrize("provider", ["stdict", "opendict"])
def test_wrong_provider_never_reaches_storage(store, connection, provider):
    _, saves, _ = store
    stdict = _client((_entry("opendict"),)) if provider == "stdict" else _client()
    opendict = _client((_entry(),))
    with pytest.raises(ValueError, match="출처"):
        service.lookup_dictionary_definition(
            connection, "익일", stdict_client=stdict, opendict_client=opendict
        )
    assert saves == []


def test_naive_clock_rejected_before_keys_or_requests(monkeypatch):
    forbidden = _forbid_credentials(monkeypatch)
    client = _client()
    with pytest.raises(ValueError, match="시간대"):
        service.query_dictionary_definition(
            "익일", stdict_client=client, clock=lambda: datetime(2026, 10, 7)
        )
    client.lookup.assert_not_called()
    forbidden.assert_not_called()


def test_owned_clients_closed_and_only_needed_provider_key_loaded(monkeypatch):
    client = _client((_entry(),))
    client.__enter__.return_value = client
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr(service, "DictionaryDefinitionClient", constructor)
    service.query_dictionary_definition(
        "익일", settings=GlossarySettings(stdict_api_key="standard")
    )
    constructor.assert_called_once_with("stdict", "standard")
    client.__exit__.assert_called_once()


def test_environment_standard_key_avoids_reading_unused_or_unreadable_dotenv(monkeypatch):
    monkeypatch.setenv("STDICT_API_KEY", " standard ")
    local = MagicMock(side_effect=AssertionError("unused fallback must not require local file"))
    monkeypatch.setattr(config, "_local_values", local)
    assert service._provider_key("stdict", None) == "standard"
    local.assert_not_called()


@pytest.mark.parametrize(
    "provider,key_name", [("stdict", "STDICT_API_KEY"), ("opendict", "OPENDICT_API_KEY")]
)
def test_missing_key_has_safe_provider_for_notice_failure(
    tmp_path, monkeypatch, provider, key_name
):
    monkeypatch.delenv(key_name, raising=False)
    monkeypatch.setattr(config, "DEFAULT_ENV_PATH", tmp_path / "absent.env")
    with pytest.raises(GlossaryConfigurationError, match=key_name) as caught:
        service._provider_key(provider, None)
    assert caught.value.provider == provider


def test_failed_save_or_missing_reread_is_not_reported_as_success(store, connection, monkeypatch):
    monkeypatch.setattr(service, "save_glossary", MagicMock(side_effect=RuntimeError("DB failure")))
    with pytest.raises(RuntimeError, match="DB failure"):
        service.lookup_dictionary_definition(connection, "익일", stdict_client=_client((_entry(),)))
    monkeypatch.setattr(service, "save_glossary", MagicMock())
    with pytest.raises(RuntimeError, match="저장 후"):
        service.lookup_dictionary_definition(connection, "익일", stdict_client=_client((_entry(),)))


def test_explicit_refresh_repairs_corrupt_cache_without_reading_old_payload(
    connection, monkeypatch
):
    stored = None
    events = []

    def get(conn, query):
        events.append("read")
        if stored is None:
            raise ValueError("corrupt old entry")
        return stored

    def save(conn, result):
        nonlocal stored
        events.append("save")
        stored = result.model_copy(update={"from_cache": True})

    monkeypatch.setattr(service, "get_glossary", get)
    monkeypatch.setattr(service, "save_glossary", save)
    result = service.lookup_dictionary_definition(
        connection, "익일", refresh=True, stdict_client=_client((_entry(),))
    )
    assert events == ["save", "read"] and result.entries == (_entry(),)


def test_failed_explicit_refresh_does_not_read_or_overwrite_corrupt_cache(connection, monkeypatch):
    read = MagicMock(side_effect=ValueError("corrupt old entry"))
    save = MagicMock()
    monkeypatch.setattr(service, "get_glossary", read)
    monkeypatch.setattr(service, "save_glossary", save)
    with pytest.raises(GlossaryAPIError):
        service.lookup_dictionary_definition(
            connection,
            "익일",
            refresh=True,
            stdict_client=_client(error=GlossaryAPIError("stdict", "authentication")),
        )
    read.assert_not_called()
    save.assert_not_called()


@pytest.mark.parametrize("cached", [True, False])
def test_mismatched_dictionary_headword_is_not_displayed_or_saved(store, connection, cached):
    rows, saves, _ = store
    wrong = _entry().model_copy(update={"headword": "산정"})
    if cached:
        rows["익일"] = _result((wrong,), ("stdict",))
    with pytest.raises(ValueError, match="표제어"):
        service.lookup_dictionary_definition(connection, "익일", stdict_client=_client((wrong,)))
    assert saves == []


def test_official_headword_markers_remain_compatible(store, connection):
    rows, _, _ = store
    marked = _entry().model_copy(update={"headword": "익-일"})
    rows["익일"] = _result((marked,), ("stdict",))
    assert service.lookup_dictionary_definition(connection, "익일").entries == (marked,)
