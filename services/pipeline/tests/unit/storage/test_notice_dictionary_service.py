"""Dictionary failures and source races must preserve committed easy text."""

import json
from datetime import timedelta

import httpx
import psycopg
import pytest
from support.db import database_uri, owned_migrated_database
from support.easy_text_cache_cas import _alternative_result
from support.easy_text_storage import _NOW, _candidate_result, _notice

from pipeline.config import DatabaseSettings
from pipeline.glossary.dictionary import DictionaryError, DictionaryQuery
from pipeline.glossary.easy_language import EasyLanguageResult, simplify_notice
from pipeline.glossary.notice_dictionary_service import enrich_notice_dictionary
from pipeline.glossary.notice_service import load_notice_glossary_input
from pipeline.storage.dictionary_cache import claim_lookup
from pipeline.storage.notice_dictionary import get_notice_dictionary
from pipeline.storage.notice_easy_text import (
    EasyTextStorageError,
    get_notice_easy_text,
    get_notice_easy_text_cache_token,
    save_notice_easy_text,
)


@pytest.fixture(scope="module")
def dictionary_database():
    with owned_migrated_database(
        env="PIPELINE_TEST_DATABASE_URL", required_prefix="pipeline_schema_test_", missing="skip",
        missing_message="PIPELINE_TEST_DATABASE_URL is required",
        invalid_message="Use a disposable local pipeline_schema_test_* database",
    ) as info:
        yield DatabaseSettings(database_url=database_uri(info))


@pytest.fixture
def dictionary_db(dictionary_database):
    with psycopg.connect(dictionary_database.database_url) as conn:
        try:
            yield conn
        finally:
            conn.rollback()
            conn.autocommit = True
            conn.execute("truncate public.notices, public.standard_dictionary_cache cascade")


def _saved_notice(conn, *, repeated=False):
    with conn.transaction():
        source = _notice(conn)
        if repeated:
            conn.execute(
                "update public.notices set body_html='<p>구비서류를 지참하세요. "
                "구비서류 제출 안내.</p>' where id=%s", (source.notice_id,),
            )
            source = load_notice_glossary_input(conn, source.notice_id)
            result = simplify_notice(
                source, api_key="test-key", clock=lambda: _NOW,
                request=lambda **_: json.dumps({
                    "changes": [{
                        "original": "지참하세요", "replacement": "가져오세요",
                        "context": "구비서류를 지참하세요.",
                    }],
                    "dictionary_candidates": [{
                        "original": "구비서류를", "query_word": "구비서류",
                        "context": "구비서류를 지참하세요.",
                    }, {
                        "original": "구비서류", "query_word": "구비서류",
                        "context": "구비서류 제출 안내.",
                    }],
                }, ensure_ascii=False),
            )
        else:
            result = _candidate_result(source)
        save_notice_easy_text(conn, result)
        token = get_notice_easy_text_cache_token(conn, source.notice_id)
    return source, result, token


@pytest.mark.parametrize(
    ("failure", "code", "retryable", "expected_requests"),
    [
        ("missing-key", "dictionary_missing_api_key", False, 0),
        ("authentication", "dictionary_authentication_failed", False, 1),
        ("rate-limit", "dictionary_rate_limited", True, 1),
        ("timeout", "dictionary_timeout", True, 2),
        ("malformed", "dictionary_invalid_response", False, 1),
    ],
)
def test_http_failures_keep_easy_text_and_one_lookup_for_repeated_candidates(
    dictionary_database, dictionary_db, monkeypatch, failure, code, retryable, expected_requests,
):
    source, saved, token = _saved_notice(dictionary_db, repeated=True)
    monkeypatch.setattr("pipeline.glossary.dictionary_client.time.sleep", lambda _: None)
    requests = []

    def handler(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("secret-request-url-and-key", request=request)
        return httpx.Response(
            {"authentication": 401, "rate-limit": 429, "malformed": 200}[failure],
            text="secret-response-and-key",
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = enrich_notice_dictionary(
            dictionary_database, source.notice_id, client=client,
            api_key=None if failure == "missing-key" else "secret-api-key",
        )
        assert not client.is_closed

    assert len(requests) == expected_requests
    assert response["easy_text"] == saved.easy_text
    assert response["dictionary_status"] == "partial"
    assert len(response["dictionary_candidates"]) == 2
    for candidate in response["dictionary_candidates"]:
        assert candidate["lookup_status"] == "failed"
        assert candidate["error_code"] == code
        assert candidate["retryable"] is retryable
        assert candidate["dictionary"] is None
        assert candidate["retry_after_seconds"] > 0
    assert "secret" not in json.dumps(response)
    assert get_notice_easy_text(dictionary_db, source.notice_id) == saved
    assert get_notice_easy_text_cache_token(dictionary_db, source.notice_id) == token
    assert dictionary_db.execute(
        "select result,last_error_code from public.standard_dictionary_cache"
    ).fetchall() == [(None, code)]


@pytest.mark.parametrize("error", [
    RuntimeError("secret-request-and-key"),
    DictionaryError("secret-unrecognized-error", retryable=True),
])
def test_unexpected_lookup_failures_publish_only_safe_fallback(
    dictionary_database, dictionary_db, error,
):
    source, saved, token = _saved_notice(dictionary_db, repeated=True)
    queries = []

    def lookup(database, query_word, **kwargs):
        queries.append(query_word)
        raise error

    response = enrich_notice_dictionary(dictionary_database, source.notice_id, lookup=lookup)

    assert queries == ["구비서류"]
    assert response["dictionary_status"] == "partial"
    assert {item["error_code"] for item in response["dictionary_candidates"]} == {
        "dictionary_lookup_failed",
    }
    assert "secret" not in json.dumps(response)
    assert get_notice_easy_text(dictionary_db, source.notice_id) == saved
    assert get_notice_easy_text_cache_token(dictionary_db, source.notice_id) == token


def test_busy_shared_lookup_does_not_start_another_request(dictionary_database, dictionary_db):
    source, saved, token = _saved_notice(dictionary_db)
    with psycopg.connect(dictionary_database.database_url, autocommit=True) as owner:
        assert claim_lookup(owner, DictionaryQuery("구비서류")).token is not None

    def unexpected_request(request):
        pytest.fail("A live shared lookup must not issue another HTTP request")

    with httpx.Client(transport=httpx.MockTransport(unexpected_request)) as client:
        response = enrich_notice_dictionary(
            dictionary_database, source.notice_id, api_key="test-key", client=client,
        )

    assert response["dictionary_status"] == "pending"
    candidate, = response["dictionary_candidates"]
    assert candidate["lookup_status"] == "pending" and candidate["dictionary"] is None
    assert candidate["retryable"] is True
    assert 0 < candidate["retry_after_seconds"] <= 180
    assert get_notice_easy_text(dictionary_db, source.notice_id) == saved
    assert get_notice_easy_text_cache_token(dictionary_db, source.notice_id) == token


@pytest.mark.parametrize("change", ["source", "same-revision-refresh", "candidates-only"])
def test_snapshot_change_during_http_rejects_stale_links_without_holding_transaction(
    dictionary_database, dictionary_db, monkeypatch, change,
):
    source, saved, token = _saved_notice(dictionary_db)
    replacement = _alternative_result(source, _NOW + timedelta(seconds=1))
    service_connections = []
    connect = psycopg.connect

    def track_connection(*args, **kwargs):
        conn = connect(*args, **kwargs)
        service_connections.append(conn)
        return conn

    monkeypatch.setattr(psycopg, "connect", track_connection)

    def change_snapshot(request):
        # Observe the actual service connections: pg_stat_activity may lag behind
        # connection shutdown. The NOWAIT queries also verify server-side locks.
        assert service_connections
        assert all(conn.closed for conn in service_connections)
        with dictionary_db.transaction():
            dictionary_db.execute("select id from public.notices for update nowait")
            dictionary_db.execute(
                "select notice_id from public.notice_easy_texts for update nowait"
            )
            dictionary_db.execute("select cache_key from public.standard_dictionary_cache "
                                  "for update nowait")
            if change == "source":
                dictionary_db.execute(
                    "update public.notices set body_html='<p>변경된 본문.</p>' where id=%s",
                    (source.notice_id,),
                )
            elif change == "same-revision-refresh":
                save_notice_easy_text(dictionary_db, replacement)
            else:
                dictionary_db.execute(
                    "update public.notice_easy_texts set dictionary_candidates='[]'::jsonb "
                    "where notice_id=%s", (source.notice_id,),
                )
        return httpx.Response(200, json={"channel": {"total": 0, "start": 1, "num": 100}})

    def handler(request):
        try:
            return change_snapshot(request)
        except Exception as error:
            # Transport exceptions are intentionally sanitized by the production client.
            # Pytest failures bypass that boundary so callback defects remain visible.
            pytest.fail(f"Concurrent snapshot mutation failed: {error!r}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EasyTextStorageError, match="원문이나 쉬운말 결과가 바뀌었습니다"):
            enrich_notice_dictionary(
                dictionary_database, source.notice_id, api_key="test-key", client=client,
            )

    assert dictionary_db.execute(
        "select count(*) from public.notice_dictionary_links"
    ).fetchone() == (0,)
    response = get_notice_dictionary(dictionary_db, source.notice_id)
    if change == "source":
        assert response is None
        assert get_notice_easy_text_cache_token(dictionary_db, source.notice_id) == token
    else:
        assert get_notice_easy_text_cache_token(dictionary_db, source.notice_id) != token
        assert response["dictionary_candidates"] is None
        assert response["dictionary_status"] == "pending"
        assert response["easy_text"] == (replacement.easy_text if change == "same-revision-refresh"
                                         else saved.easy_text)


def test_legacy_unknown_candidates_fail_before_http_and_preserve_easy_text(
    dictionary_database, dictionary_db,
):
    with dictionary_db.transaction():
        source = _notice(dictionary_db)
        saved = EasyLanguageResult.model_validate({
            **_candidate_result(source).model_dump(), "dictionary_candidates": None,
        })
        save_notice_easy_text(dictionary_db, saved)

    def unexpected_lookup(*args, **kwargs):
        pytest.fail("Unknown candidates must be regenerated before dictionary lookup")

    with pytest.raises(EasyTextStorageError, match="단어 후보를 먼저 저장"):
        enrich_notice_dictionary(dictionary_database, source.notice_id, lookup=unexpected_lookup)

    assert get_notice_easy_text(dictionary_db, source.notice_id) == saved
    response = get_notice_dictionary(dictionary_db, source.notice_id)
    assert response["dictionary_status"] == "unprocessed"
    assert response["dictionary_candidates"] is None


@pytest.mark.parametrize("failure,retryable", [("dictionary_timeout", True),
                                               ("dictionary_authentication_failed", False)])
def test_dictionary_scan_recovers_old_links_and_respects_retry_policy(
    dictionary_database, dictionary_db, failure, retryable,
):
    from pipeline.storage.notice_dictionary import dictionary_work

    source, saved, _ = _saved_notice(dictionary_db)
    category = dictionary_db.execute("select category from notices where id=%s",
                                     (source.notice_id,)).fetchone()[0]
    dictionary_db.commit()
    assert dictionary_work(dictionary_db, source=category, limit=1) == ([source.notice_id], 1)
    assert dictionary_work(dictionary_db, source="seoul", limit=1) == ([], 0)
    dictionary_db.commit()

    def fail(*args, **kwargs):
        raise DictionaryError(failure, retryable=retryable)

    enrich_notice_dictionary(dictionary_database, source.notice_id, lookup=fail)
    expected = [source.notice_id] if retryable else []
    assert dictionary_work(dictionary_db, source=category, limit=1) == (expected, 1)
    assert get_notice_easy_text(dictionary_db, source.notice_id) == saved
    dictionary_db.commit()
    # A live lookup lease defers even a retryable word without hiding the gap.
    with psycopg.connect(dictionary_database.database_url, autocommit=True) as conn:
        assert claim_lookup(conn, DictionaryQuery("구비서류")).token is not None
    assert dictionary_work(dictionary_db, source=category, limit=1) == ([], 1)
    dictionary_db.execute("update notices set is_visible=false where id=%s", (source.notice_id,))
    assert dictionary_work(dictionary_db, source=category, limit=1) == ([], 0)
