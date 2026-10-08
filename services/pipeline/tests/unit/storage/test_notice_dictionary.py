"""Database races and transaction boundaries for notice dictionary enrichment."""

from datetime import timedelta

import psycopg
import pytest
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb
from support.db import owned_migrated_database
from support.easy_text_storage import _NOW, _candidate_result, _notice

from pipeline.glossary.notice_dictionary import map_dictionary_candidates
from pipeline.storage.notice_dictionary import (
    NoticeDictionaryStorageError,
    get_notice_dictionary,
    save_notice_dictionary,
)
from pipeline.storage.notice_easy_text import (
    get_notice_easy_text_cache_token,
    save_notice_easy_text,
)


@pytest.fixture(scope="module")
def notice_dictionary_database():
    with owned_migrated_database(
        env="PIPELINE_TEST_DATABASE_URL", required_prefix="pipeline_schema_test_", missing="skip",
        missing_message="PIPELINE_TEST_DATABASE_URL is required",
        invalid_message="Use a disposable local pipeline_schema_test_* database",
    ) as info:
        yield info


@pytest.fixture
def linked_notice(notice_dictionary_database):
    with psycopg.connect(**notice_dictionary_database) as setup:
        source = _notice(setup)
        result = _candidate_result(source)
        save_notice_easy_text(setup, result)
    with psycopg.connect(**notice_dictionary_database, autocommit=True) as conn:
        token = get_notice_easy_text_cache_token(conn, source.notice_id)
        candidates = [item | {"lookup_status": "pending", "error_code": None, "retryable": True}
                      for item in map_dictionary_candidates(result)]
        try:
            yield conn, source, result, token, candidates
        finally:
            conn.execute("truncate public.notices,public.standard_dictionary_cache cascade")


@pytest.mark.parametrize("mutation", ["source", "generation", "candidates", "scope"])
def test_snapshot_cas_rejects_source_and_same_source_refresh(
    linked_notice, notice_dictionary_database, mutation,
):
    conn, source, result, token, candidates = linked_notice
    assert save_notice_dictionary(
        conn, source.notice_id, expected_easy_text_token=token, candidates=candidates,
    )
    before = conn.execute("select to_jsonb(l) from public.notice_dictionary_links l").fetchone()[0]
    if mutation == "source":
        conn.execute(
            "update public.notices set body_html='바뀐 원문' where id=%s", (source.notice_id,),
        )
    elif mutation == "generation":
        with psycopg.connect(**notice_dictionary_database) as writer:
            save_notice_easy_text(writer, _candidate_result(source, _NOW + timedelta(seconds=1)))
    elif mutation == "candidates":
        conn.execute("update public.notice_easy_texts set dictionary_candidates='[]'")
    else:
        conn.execute(
            "update public.notice_easy_texts set body_text_present=null,"
            "attachment_content_included=null",
        )
    assert not save_notice_dictionary(
        conn, source.notice_id, expected_easy_text_token=token, candidates=candidates,
    )
    after = conn.execute("select to_jsonb(l) from public.notice_dictionary_links l").fetchone()[0]
    assert after == before
    assert conn.info.transaction_status == TransactionStatus.IDLE
    public = get_notice_dictionary(conn, source.notice_id)
    if mutation == "source":
        assert public is None
    else:
        assert public["dictionary_candidates"] is None
        assert public["dictionary_status"] == "pending"


def test_sql_and_python_tokens_match_across_timezones(linked_notice):
    conn, source, _, token, _ = linked_notice
    conn.execute("set time zone 'Asia/Seoul'")
    assert conn.execute(
        "select public.notice_dictionary_easy_text_token(e) "
        "from public.notice_easy_texts e where notice_id=%s", (source.notice_id,),
    ).fetchone() == (token,)
    assert get_notice_easy_text_cache_token(conn, source.notice_id) == token


@pytest.mark.parametrize("field,value", [
    ("cache_key", "b" * 64), ("easy_start", 0), ("easy_expression", "다른 말"),
    ("mapping_status", "original_only"), ("query_word", "신청"),
    ("definition", "복사해서는 안 되는 뜻풀이"), ("error_code", "secret-api-key"),
])
def test_save_rejects_forged_mapping_or_unapproved_errors(linked_notice, field, value):
    conn, source, _, token, candidates = linked_notice
    candidates[0][field] = value
    with pytest.raises(NoticeDictionaryStorageError, match="invalid_notice_dictionary_candidates"):
        save_notice_dictionary(
            conn, source.notice_id, expected_easy_text_token=token, candidates=candidates,
        )
    assert conn.execute("select count(*) from public.notice_dictionary_links").fetchone() == (0,)
    assert conn.info.transaction_status == TransactionStatus.IDLE


def test_link_storage_failure_preserves_easy_text_and_has_safe_message(linked_notice):
    conn, source, result, token, candidates = linked_notice
    conn.execute("""
        create function public.reject_notice_dictionary_test() returns trigger language plpgsql
        as $$ begin raise exception 'secret-url-do-not-expose'; end; $$;
        create trigger reject_notice_dictionary_test before insert on public.notice_dictionary_links
        for each row execute function public.reject_notice_dictionary_test();
    """)
    try:
        with pytest.raises(NoticeDictionaryStorageError, match="^notice_dictionary_storage_error$"):
            save_notice_dictionary(
                conn, source.notice_id, expected_easy_text_token=token, candidates=candidates,
            )
        assert conn.info.transaction_status == TransactionStatus.IDLE
        assert conn.execute(
            "select count(*) from public.notice_dictionary_links",
        ).fetchone() == (0,)
        assert get_notice_dictionary(conn, source.notice_id)["easy_text"] == result.easy_text
        assert get_notice_easy_text_cache_token(conn, source.notice_id) == token
    finally:
        conn.execute("drop trigger reject_notice_dictionary_test on public.notice_dictionary_links")
        conn.execute("drop function public.reject_notice_dictionary_test()")


def test_open_transaction_is_rejected_without_committing_caller_work(linked_notice):
    conn, source, _, token, candidates = linked_notice
    with conn.transaction():
        with pytest.raises(NoticeDictionaryStorageError, match="requires_idle_autocommit"):
            save_notice_dictionary(
                conn, source.notice_id, expected_easy_text_token=token, candidates=candidates,
            )
        assert conn.info.transaction_status == TransactionStatus.INTRANS


def test_link_save_commits_and_preserves_all_occurrence_positions(linked_notice):
    conn, source, result, token, candidates = linked_notice
    candidates[0].update(
        lookup_status="failed", error_code="dictionary_missing_api_key", retryable=False,
    )
    assert save_notice_dictionary(
        conn, source.notice_id, expected_easy_text_token=token, candidates=candidates,
    )
    public = get_notice_dictionary(conn, source.notice_id)
    assert public["original_text"] == result.original_text
    assert public["easy_text"] == result.easy_text
    assert public["dictionary_status"] == "partial"
    item = public["dictionary_candidates"][0]
    assert item["easy_expression"] == "준비할 서류를"
    assert item["original"] == "구비서류를"
    assert item["dictionary"] is None
    assert item["error_code"] == "dictionary_missing_api_key"
    # A later complete shared cache result immediately takes precedence, even
    # though the notice-specific failure record has not been written again.
    conn.execute(
        "insert into public.standard_dictionary_cache "
        "(cache_key,query_word,search_conditions,contract_version,result,result_updated_at) "
        "values (%s,'구비서류',%s,'stdict-v1',%s,clock_timestamp())",
        (candidates[0]["cache_key"],
         Jsonb({"provider": "stdict", "target": 1, "method": "exact", "pos": 0}),
         Jsonb({"query_word": "구비서류", "contract_version": "stdict-v1",
                "status": "not_found", "entries": []})),
    )
    updated = get_notice_dictionary(conn, source.notice_id)
    assert updated["dictionary_status"] == "complete"
    assert updated["dictionary_candidates"][0]["lookup_status"] == "not_found"
    assert updated["dictionary_candidates"][0]["error_code"] is None
