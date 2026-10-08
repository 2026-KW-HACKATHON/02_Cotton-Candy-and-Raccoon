"""Real committed database tests for shared dictionary ownership and preservation."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from time import monotonic, sleep
from uuid import uuid4

import psycopg
import pytest
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb
from support.db import owned_migrated_database

from pipeline.glossary.dictionary import (
    DictionaryEntry,
    DictionaryQuery,
    DictionaryResult,
    DictionarySense,
)
from pipeline.storage.dictionary_cache import (
    DictionaryStorageError,
    claim_lookup,
    complete_lookup,
    fail_lookup,
)


@pytest.fixture(scope="module")
def dictionary_database():
    with owned_migrated_database(
        env="PIPELINE_TEST_DATABASE_URL", required_prefix="pipeline_schema_test_", missing="skip",
        missing_message="PIPELINE_TEST_DATABASE_URL is required",
        invalid_message="Use a disposable local pipeline_schema_test_* database",
    ) as info:
        yield info


@pytest.fixture
def dictionary_db(dictionary_database):
    with psycopg.connect(**dictionary_database, autocommit=True) as conn:
        try:
            yield conn
        finally:
            conn.execute("truncate public.standard_dictionary_cache")


def _found(query: DictionaryQuery, definition: str = "어떤 조건을 갖춘 대상.") -> DictionaryResult:
    return DictionaryResult(
        query_word=query.word, status="found", entries=(DictionaryEntry(
            target_code="100", headword=query.word,
            source_url="https://stdict.korean.go.kr/search/searchView.do?word_no=100",
            senses=(DictionarySense(
                sense_code="1001", pos_code="1", part_of_speech="명사", definition=definition,
            ),),
        ),),
    )


def _row(conn, query):
    return conn.execute(
        "select result,result_updated_at,lease_token,lease_expires_at,"
        "retry_after_at,last_error_code "
        "from public.standard_dictionary_cache where cache_key=%s", (query.cache_key,),
    ).fetchone()


def _expire(conn, query):
    conn.execute(
        "update public.standard_dictionary_cache set lease_expires_at=clock_timestamp() - "
        "interval '1 second' where cache_key=%s", (query.cache_key,),
    )


def test_concurrent_committed_claims_have_exactly_one_owner(dictionary_db, dictionary_database):
    query = DictionaryQuery("지원")
    barrier = Barrier(2)

    def worker():
        with psycopg.connect(**dictionary_database, autocommit=True) as conn:
            barrier.wait(timeout=5)
            claim = claim_lookup(conn, query)
            assert conn.info.transaction_status == TransactionStatus.IDLE
            return claim

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: worker(), range(2)))
    assert sum(item.token is not None for item in (first, second)) == 1
    assert sum(0 < item.retry_after_seconds <= 180 for item in (first, second)) == 1
    owner = first if first.token else second
    assert _row(dictionary_db, query)[2] == owner.token


@pytest.mark.parametrize("not_found", [False, True])
def test_committed_complete_results_never_expire_and_are_shared(
    dictionary_db, dictionary_database, not_found,
):
    query = DictionaryQuery("지원")
    expected = (DictionaryResult(query_word=query.word, status="not_found", entries=())
                if not_found else _found(query))
    claim = claim_lookup(dictionary_db, query)
    assert complete_lookup(dictionary_db, query, claim.token, expected)
    dictionary_db.execute(
        "update public.standard_dictionary_cache set result_updated_at='2020-01-01T00:00:00Z' "
        "where cache_key=%s", (query.cache_key,),
    )
    with psycopg.connect(**dictionary_database, autocommit=True) as reader:
        hit = claim_lookup(reader, DictionaryQuery("  지원  "))
    assert hit.cached == expected
    assert hit.token is None and hit.retry_after_seconds == 0
    assert _row(dictionary_db, query)[2:] == (None, None, None, None)


def test_refresh_keeps_prior_result_until_whole_replacement(dictionary_db, dictionary_database):
    query = DictionaryQuery("지원")
    initial = claim_lookup(dictionary_db, query)
    old = _found(query)
    assert complete_lookup(dictionary_db, query, initial.token, old)
    refresh = claim_lookup(dictionary_db, query, refresh=True)
    assert refresh.cached is None and refresh.token is not None
    with psycopg.connect(**dictionary_database, autocommit=True) as reader:
        assert claim_lookup(reader, query).cached == old
        busy = claim_lookup(reader, query, refresh=True)
        assert busy.token is None and busy.cached is None and busy.retry_after_seconds > 0
    new = _found(query, "새로 갱신된 완전한 사전 정의.")
    assert complete_lookup(dictionary_db, query, refresh.token, new)
    assert claim_lookup(dictionary_db, query).cached == new


@pytest.mark.parametrize("had_result", [False, True])
def test_failure_cooldown_preserves_cache_and_never_becomes_not_found(dictionary_db, had_result):
    query = DictionaryQuery("지원")
    initial = claim_lookup(dictionary_db, query)
    old = _found(query)
    if had_result:
        assert complete_lookup(dictionary_db, query, initial.token, old)
        initial = claim_lookup(dictionary_db, query, refresh=True)
    before = _row(dictionary_db, query)[:2]
    assert fail_lookup(
        dictionary_db, query, initial.token, "dictionary_rate_limited", retry_after_seconds=60,
    )
    after = _row(dictionary_db, query)
    assert after[:2] == before
    assert after[2:4] == (None, None)
    assert after[-1] == "dictionary_rate_limited"
    claim = claim_lookup(dictionary_db, query)
    if had_result:
        assert claim.cached == old
    else:
        assert claim.cached is None and claim.token is None and 0 < claim.retry_after_seconds <= 60
    assert claim_lookup(dictionary_db, query, refresh=True).retry_after_seconds > 0
    dictionary_db.execute(
        "update public.standard_dictionary_cache set retry_after_at=clock_timestamp() - "
        "interval '1 second' where cache_key=%s", (query.cache_key,),
    )
    recovered = claim_lookup(dictionary_db, query, refresh=True)
    assert recovered.token is not None


def test_expired_and_replaced_owner_cannot_write_or_release_new_lease(dictionary_db):
    query = DictionaryQuery("지원")
    old = claim_lookup(dictionary_db, query)
    _expire(dictionary_db, query)
    expired = _row(dictionary_db, query)
    assert not complete_lookup(dictionary_db, query, old.token, _found(query))
    assert not fail_lookup(dictionary_db, query, old.token, "dictionary_timeout")
    assert _row(dictionary_db, query) == expired
    new = claim_lookup(dictionary_db, query)
    assert new.token != old.token
    leased = _row(dictionary_db, query)
    assert not complete_lookup(dictionary_db, query, old.token, _found(query))
    assert not fail_lookup(dictionary_db, query, old.token, "dictionary_timeout")
    assert _row(dictionary_db, query) == leased
    updated = _found(query, "새 소유자가 검증한 정의.")
    assert complete_lookup(dictionary_db, query, new.token, updated)
    assert not fail_lookup(dictionary_db, query, old.token, "dictionary_timeout")
    assert claim_lookup(dictionary_db, query).cached == updated


@pytest.mark.parametrize("operation", ["complete", "fail"])
def test_ownership_expiry_is_checked_after_lock_wait(dictionary_db, dictionary_database, operation):
    query = DictionaryQuery("지원")
    owner = claim_lookup(dictionary_db, query, lease_seconds=2)
    entered = Event()
    worker_pid = []

    def worker():
        with psycopg.connect(**dictionary_database, autocommit=True) as conn:
            worker_pid.append(conn.info.backend_pid)
            entered.set()
            if operation == "complete":
                return complete_lookup(conn, query, owner.token, _found(query))
            return fail_lookup(conn, query, owner.token, "dictionary_timeout")

    with ThreadPoolExecutor(max_workers=1) as pool:
        with dictionary_db.transaction():
            dictionary_db.execute(
                "select cache_key from public.standard_dictionary_cache where cache_key=%s "
                "for update", (query.cache_key,),
            )
            future = pool.submit(worker)
            assert entered.wait(timeout=5)
            # Confirm actual contention, then let the database clock pass the deadline
            # without changing the row. An UPDATE's early WHERE check cannot be reused.
            timeout = monotonic() + 5
            while dictionary_db.execute(
                "select wait_event_type from pg_stat_activity where pid=%s", (worker_pid[0],),
            ).fetchone() != ("Lock",):
                assert monotonic() < timeout
                sleep(0.01)
            while not dictionary_db.execute(
                "select lease_expires_at<clock_timestamp() from public.standard_dictionary_cache "
                "where cache_key=%s", (query.cache_key,),
            ).fetchone()[0]:
                assert monotonic() < timeout
                sleep(0.01)
        assert future.result(timeout=5) is False
    assert _row(dictionary_db, query)[2] == owner.token


def test_query_mismatch_and_nested_corrupt_cache_are_not_accepted(dictionary_db):
    query = DictionaryQuery("지원")
    owner = claim_lookup(dictionary_db, query)
    with pytest.raises(DictionaryStorageError, match="invalid_dictionary_cached_result"):
        complete_lookup(dictionary_db, query, owner.token, _found(DictionaryQuery("신청")))
    assert _row(dictionary_db, query)[0] is None
    corrupt = _found(query).model_dump(mode="json")
    corrupt["entries"][0]["senses"][0]["sense_code"] = "not-an-id"
    dictionary_db.execute(
        "update public.standard_dictionary_cache set result=%s,result_updated_at=clock_timestamp() "
        "where cache_key=%s", (Jsonb(corrupt), query.cache_key),
    )
    with pytest.raises(DictionaryStorageError, match="invalid_dictionary_cached_result"):
        claim_lookup(dictionary_db, query)


def test_storage_error_rolls_back_insert_and_does_not_leak_database_message(dictionary_db):
    query = DictionaryQuery("지원")
    dictionary_db.execute("""
        create function public.reject_dictionary_test_update() returns trigger language plpgsql
        as $$ begin raise exception 'secret-url-do-not-expose'; end; $$;
        create trigger dictionary_test_reject before update on public.standard_dictionary_cache
        for each row execute function public.reject_dictionary_test_update();
    """)
    try:
        with pytest.raises(DictionaryStorageError, match="^dictionary_storage_error$") as failure:
            claim_lookup(dictionary_db, query)
        assert failure.value.__cause__ is None
        assert dictionary_db.info.transaction_status == TransactionStatus.IDLE
        assert _row(dictionary_db, query) is None
    finally:
        dictionary_db.execute(
            "drop trigger dictionary_test_reject on public.standard_dictionary_cache"
        )
        dictionary_db.execute("drop function public.reject_dictionary_test_update()")


def test_open_caller_transaction_is_rejected_without_implicit_commit(dictionary_db):
    query = DictionaryQuery("지원")
    with dictionary_db.transaction():
        with pytest.raises(DictionaryStorageError, match="requires_idle_autocommit"):
            claim_lookup(dictionary_db, query)
        assert dictionary_db.info.transaction_status == TransactionStatus.INTRANS
    assert _row(dictionary_db, query) is None


@pytest.mark.parametrize("operation", ["complete", "fail"])
def test_missing_lease_never_creates_completion_or_failure(dictionary_db, operation):
    query = DictionaryQuery("지원")
    if operation == "complete":
        assert not complete_lookup(dictionary_db, query, uuid4(), _found(query))
    else:
        assert not fail_lookup(dictionary_db, query, uuid4(), "dictionary_timeout")
    assert _row(dictionary_db, query) is None
