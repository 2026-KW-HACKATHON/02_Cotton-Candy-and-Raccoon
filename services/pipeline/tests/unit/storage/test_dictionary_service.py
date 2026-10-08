"""HTTP-to-PostgreSQL tests for shared caching and recoverable lookup ownership."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import psycopg
import pytest
from support.db import database_uri, owned_migrated_database

from pipeline.config import DatabaseSettings
from pipeline.glossary.dictionary import (
    DictionaryBusy,
    DictionaryError,
    DictionaryQuery,
    DictionaryResult,
)
from pipeline.glossary.dictionary_service import lookup_dictionary
from pipeline.storage.dictionary_cache import claim_lookup, complete_lookup


@pytest.fixture(scope="module")
def service_database():
    with owned_migrated_database(
        env="PIPELINE_TEST_DATABASE_URL", required_prefix="pipeline_schema_test_", missing="skip",
        missing_message="PIPELINE_TEST_DATABASE_URL is required",
        invalid_message="Use a disposable local pipeline_schema_test_* database",
    ) as info:
        yield DatabaseSettings(database_url=database_uri(info))


@pytest.fixture
def service_db(service_database):
    with psycopg.connect(service_database.database_url, autocommit=True) as conn:
        try:
            yield conn
        finally:
            conn.execute("truncate public.standard_dictionary_cache")


def _response(request, *, found=True):
    if request.url.path.endswith("search.do"):
        return httpx.Response(200, json={"channel": {
            "total": int(found), "start": 1, "num": 100,
            "item": [{"target_code": "100", "word": "지원"}] if found else [],
        }})
    return httpx.Response(200, json={"channel": {"total": 1, "item": {
        "target_code": "100", "word_info": {"word": "지원", "pos_info": [{
            "pos_code": "1", "pos": "명사", "comm_pattern_info": [{"sense_info": [
                {"sense_code": "101", "definition": "다른 사람을 돕는 일."},
                {"sense_code": "102", "definition": "스스로 참여를 청함."},
            ]}],
        }]},
    }}})


def _lookup(database, client, **kwargs):
    return lookup_dictionary(database, "지원", api_key="test-key", client=client, **kwargs)


@pytest.mark.parametrize("found", [True, False])
def test_complete_and_negative_results_are_reused_without_key_or_ttl(
    service_database, service_db, found,
):
    requests = []

    def handler(request):
        requests.append(request)
        return _response(request, found=found)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = _lookup(service_database, client)
        expected_count = 2 if found else 1
        assert len(requests) == expected_count
        assert not first.cache_hit
        assert first.result.status == ("found" if found else "not_found")
        if found:
            assert len(first.result.entries[0].senses) == 2
        service_db.execute(
            "update public.standard_dictionary_cache set result_updated_at='2020-01-01Z'"
        )
        cached = lookup_dictionary(service_database, "  지원  ", client=client)
        assert cached.cache_hit and cached.result == first.result
        assert len(requests) == expected_count
        assert not client.is_closed


def test_failed_refresh_preserves_existing_result_for_ordinary_readers(
    service_database, service_db,
):
    with httpx.Client(transport=httpx.MockTransport(_response)) as client:
        prior = _lookup(service_database, client)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429))) as client:
        with pytest.raises(DictionaryError, match="^dictionary_rate_limited$"):
            _lookup(service_database, client, refresh=True)
        cached = lookup_dictionary(service_database, "지원", client=client)
        assert cached.cache_hit and cached.result == prior.result
    assert service_db.execute(
        "select lease_token,last_error_code from public.standard_dictionary_cache"
    ).fetchone() == (None, "dictionary_rate_limited")


@pytest.mark.parametrize("failure", ["authentication", "timeout", "missing_key"])
def test_external_failure_never_becomes_negative_cache(service_database, service_db, monkeypatch,
                                                       failure):
    monkeypatch.setattr("pipeline.glossary.dictionary_client.time.sleep", lambda _: None)

    def handler(request):
        if failure == "authentication":
            return httpx.Response(401)
        if failure == "timeout":
            raise httpx.ReadTimeout("secret-request-url", request=request)
        pytest.fail("Missing key must not issue an HTTP request")

    expected = {
        "authentication": "dictionary_authentication_failed",
        "timeout": "dictionary_timeout", "missing_key": "dictionary_missing_api_key",
    }[failure]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DictionaryError, match=f"^{expected}$"):
            lookup_dictionary(service_database, "지원", client=client,
                              api_key=None if failure == "missing_key" else "test-key")
    row = service_db.execute(
        "select result,result_updated_at,lease_token,last_error_code,retry_after_at "
        "from public.standard_dictionary_cache"
    ).fetchone()
    assert row[:4] == (None, None, None, expected)
    assert row[4] is not None


def test_concurrent_miss_has_one_http_owner_and_no_open_database_transaction(
    service_database, service_db,
):
    entered, release = Event(), Event()
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("search.do"):
            entered.set()
            assert release.wait(timeout=10)
        return _response(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            owner = pool.submit(_lookup, service_database, client)
            try:
                assert entered.wait(timeout=5)
                assert service_db.execute(
                    "select count(*) from pg_stat_activity where datname=current_database() "
                    "and pid<>pg_backend_pid()"
                ).fetchone() == (0,)
                with service_db.transaction():
                    service_db.execute("select * from public.standard_dictionary_cache "
                                       "for update nowait")
                with pytest.raises(DictionaryBusy) as busy:
                    _lookup(service_database, client)
                assert 0 < busy.value.retry_after_seconds <= 180
                assert len(requests) == 1
            finally:
                release.set()
            result = owner.result(timeout=10)
    assert not result.cache_hit and len(requests) == 2
    assert lookup_dictionary(service_database, "지원").cache_hit


def test_expired_http_owner_cannot_overwrite_replacement(service_database, service_db):
    with httpx.Client(transport=httpx.MockTransport(_response)) as client:
        _lookup(service_database, client)
    entered, release = Event(), Event()

    def handler(request):
        if request.url.path.endswith("search.do"):
            entered.set()
            assert release.wait(timeout=10)
        return _response(request)

    query = DictionaryQuery("지원")
    newer_result = DictionaryResult(query_word="지원", status="not_found", entries=())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            stale = pool.submit(_lookup, service_database, client, refresh=True)
            try:
                assert entered.wait(timeout=5)
                service_db.execute(
                    "update public.standard_dictionary_cache "
                    "set lease_expires_at=clock_timestamp()-interval '1 second'"
                )
                replacement = claim_lookup(service_db, query, refresh=True)
                assert complete_lookup(service_db, query, replacement.token, newer_result)
            finally:
                release.set()
            with pytest.raises(DictionaryError, match="^dictionary_lease_lost$"):
                stale.result(timeout=10)
    assert lookup_dictionary(service_database, "지원").result == newer_result


@pytest.mark.parametrize("kwargs", [{"query_word": "\x00"}, {"query_word": "지원", "refresh": 1}])
def test_invalid_input_is_rejected_before_database_or_http(monkeypatch, kwargs):
    def unexpected_connection(*args, **kw):
        pytest.fail("Invalid input must not connect to PostgreSQL")

    monkeypatch.setattr("pipeline.glossary.dictionary_service.psycopg.connect",
                        unexpected_connection)
    with pytest.raises(DictionaryError):
        lookup_dictionary(DatabaseSettings(database_url="postgresql://unused/test"), **kwargs)
