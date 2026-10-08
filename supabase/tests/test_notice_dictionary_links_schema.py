"""Restricted dictionary RPC visibility, cache precedence and grant boundaries."""

import json
from copy import deepcopy

import psycopg
import pytest
from psycopg.types.json import Jsonb

TITLE = "😀 안내"
BODY = "구비서류를 지참하세요."
ORIGINAL = TITLE + "\n" + BODY
CANDIDATE = {
    "original": "구비서류를", "query_word": "구비서류", "context": BODY,
    "start": len(TITLE) + 1, "end": len(TITLE) + 1 + len("구비서류를"),
}
LINK = CANDIDATE | {
    "cache_key": "a" * 64, "easy_start": CANDIDATE["start"], "easy_end": CANDIDATE["end"],
    "easy_expression": "구비서류를", "mapping_status": "unchanged", "lookup_status": "pending",
    "error_code": None, "retryable": True,
}


@pytest.fixture
def linked_row(db):
    notice_id = db.execute(
        "insert into notices(category,source_board,post_sn,title,body_html,registered_on,url) "
        "values ('nowon','1001','dictionary-links',%s,%s,current_date,"
        "'https://www.nowon.kr/test/dictionary-links') returning id", (TITLE, BODY),
    ).fetchone()[0]
    db.execute(
        "insert into notice_easy_texts "
        "(notice_id,notice_revision,source_hash,original_text,easy_text,changes,model,"
        "prompt_version,attempt_count,generated_at,dictionary_candidates) "
        "values (%s,notice_easy_text_revision(%s,%s),encode(sha256(convert_to(%s,'UTF8')),"
        "'hex'),%s,%s,'[]','test','easy-language-v8',1,'2026-10-08T00:00:00Z',%s)",
        (notice_id, TITLE, BODY, ORIGINAL, ORIGINAL, ORIGINAL, Jsonb([CANDIDATE])),
    )
    db.execute(
        "insert into notice_dictionary_links(notice_id,easy_text_token,candidates) "
        "select notice_id,notice_dictionary_easy_text_token(e),%s from notice_easy_texts e "
        "where notice_id=%s", (Jsonb([LINK]), notice_id),
    )
    return notice_id


def _public(db, notice_id, role="anon"):
    with db.transaction():
        db.execute("set local role " + role)
        result = db.execute("select public.get_notice_dictionary(%s)", (notice_id,)).fetchone()[0]
        db.execute("reset role")
        return result


def _cache(db, *, status=None, lease=False, error=None):
    result = None if status is None else {
        "query_word": "구비서류", "contract_version": "stdict-v1", "status": status,
        "entries": ([{"target_code": "100", "headword": "구비서류",
                     "source_url": "https://stdict.korean.go.kr/search/searchView.do?word_no=100",
                     "senses": [
            {"definition": "필요한 서류.", "sense_code": "1", "pos_code": "1",
             "part_of_speech": "명사"},
        ]}] if status == "found" else []),
    }
    db.execute(
        "insert into public.standard_dictionary_cache "
        "(cache_key,query_word,search_conditions,contract_version,result,result_updated_at,"
        "lease_token,lease_expires_at,last_error_code,retry_after_at) "
        "values (repeat('a',64),'구비서류',%s,'stdict-v1',%s,"
        "case when %s then clock_timestamp() end,"
        "case when %s then gen_random_uuid() end,"
        "case when %s then clock_timestamp()+interval '3 minutes' end,%s,"
        "case when %s then clock_timestamp()+interval '1 minute' end)",
        (Jsonb({"provider": "stdict", "target": 1, "method": "exact", "pos": 0}),
         Jsonb(result) if result is not None else None, status is not None, lease, lease,
         error, error is not None and not lease),
    )
    return result


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("statement", [
    "select * from public.notice_dictionary_links",
    "insert into public.notice_dictionary_links(notice_id) values (1)",
    "update public.notice_dictionary_links set candidates='[]'",
    "delete from public.notice_dictionary_links", "truncate public.notice_dictionary_links",
])
def test_link_table_is_private(db, role, statement):
    db.execute("set local role " + role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute(statement)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_public_rpc_is_read_only_and_hides_backend_identity(db, linked_row, role):
    _cache(db, status="found", lease=True)
    public = _public(db, linked_row, role)
    assert public["dictionary_status"] == "complete"
    assert public["original_text"] == public["easy_text"] == ORIGINAL
    item = public["dictionary_candidates"][0]
    assert item["lookup_status"] == "found"
    assert item["dictionary"]["entries"][0]["senses"][0]["definition"] == "필요한 서류."
    for forbidden in ("cache_key", "easy_text_token", "lease_token", "lease_expires_at"):
        assert forbidden not in json.dumps(public)
    assert item["retry_after_seconds"] == 0
    assert item["retryable"] is False


@pytest.mark.parametrize("status", ["found", "not_found"])
def test_complete_shared_cache_survives_refresh_failure(db, linked_row, status):
    _cache(db, status=status, error="dictionary_timeout")
    public = _public(db, linked_row)
    assert public["dictionary_status"] == "complete"
    assert public["dictionary_candidates"][0]["lookup_status"] == status
    assert public["dictionary_candidates"][0]["error_code"] is None


@pytest.mark.parametrize("path,value", [
    (("entries", 0, "senses", 0, "sense_code"), "not-an-id"),
    (("entries", 0, "senses", 0, "sense_code"), 1),
    (("entries", 0, "senses", 0, "pos_code"), True),
    (("entries", 0, "senses", 0, "part_of_speech"), ""),
    (("entries", 0, "senses", 0, "part_of_speech"), "명" * 101),
    (("entries", 0, "senses", 0, "definition"), None),
    (("entries", 0, "senses", 0, "definition"), "뜻" * 20001),
    (("entries", 0, "senses", 0, "extra"), "unexpected"),
    (("entries", 0, "senses", 0), "malformed sense"),
    (("entries", 0, "target_code"), "not-an-id"),
    (("entries", 0, "headword"), "말" * 201),
    (("entries", 0, "homonym_number"), 1),
    (("entries", 0, "homonym_number"), "not-an-id"),
    (("entries", 0, "senses"), []),
    (("entries", 0, "senses"), {}),
    (("entries", 0, "source_url"), None),
    (("entries", 0, "source_url"), "https://evil.example/?word_no=100"),
    (("entries", 0, "source_url"),
     "https://stdict.korean.go.kr/search/searchView.do?word_no=999"),
    (("entries", 0, "source_url"),
     "https://stdict.korean.go.kr/search/searchView.do?word_no=100#fragment"),
    (("entries", 0, "extra"), "unexpected"),
    (("entries", 0), "malformed entry"),
    (("extra",), "unexpected"),
], ids=[
    "sense-id-format", "sense-id-type", "pos-id-type", "empty-part-of-speech",
    "long-part-of-speech", "null-definition", "long-definition", "extra-sense-field",
    "sense-type", "target-id-format", "long-headword", "homonym-type", "homonym-format",
    "empty-senses", "senses-type", "null-source-url", "unofficial-source-url",
    "source-target-mismatch", "source-url-fragment", "extra-entry-field", "entry-type",
    "extra-result-field",
])
def test_rpc_rejects_nested_corrupt_cache_even_during_refresh(db, linked_row, path, value):
    result = _cache(db, status="found", lease=True)
    node = result
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    db.execute("update standard_dictionary_cache set result=%s", (Jsonb(result),))
    public = _public(db, linked_row)
    assert public["dictionary_status"] == "partial"
    item = public["dictionary_candidates"][0]
    assert item["lookup_status"] == "failed"
    assert item["error_code"] == "invalid_dictionary_cached_result"
    assert item["dictionary"] is None
    assert item["retryable"] is False
    assert public["easy_text"] == ORIGINAL


@pytest.mark.parametrize("kind", ["entries", "senses"])
@pytest.mark.parametrize("duplicate", [False, True], ids=["over-limit", "duplicate-id"])
def test_rpc_rejects_duplicate_ids_and_oversized_dictionary_arrays(db, linked_row, kind, duplicate):
    result = _cache(db, status="found")
    if kind == "entries":
        prototype = result["entries"][0]
        values = [deepcopy(prototype) for _ in range(2 if duplicate else 1001)]
        if not duplicate:
            for index, entry in enumerate(values):
                entry["target_code"] = str(index)
                entry["source_url"] = (
                    "https://stdict.korean.go.kr/search/searchView.do?word_no=" + str(index)
                )
        result["entries"] = values
    else:
        prototype = result["entries"][0]["senses"][0]
        values = [deepcopy(prototype) for _ in range(2 if duplicate else 1001)]
        if not duplicate:
            for index, sense in enumerate(values):
                sense["sense_code"] = str(index)
        result["entries"][0]["senses"] = values
    db.execute("update standard_dictionary_cache set result=%s", (Jsonb(result),))
    public = _public(db, linked_row)
    assert public["dictionary_status"] == "partial"
    assert public["dictionary_candidates"][0]["dictionary"] is None
    assert public["dictionary_candidates"][0]["error_code"] == "invalid_dictionary_cached_result"


@pytest.mark.parametrize("field,value", [
    ("query_word", "다른말"), ("contract_version", "other-version"),
    ("status", "not_found"), ("entries", None),
])
def test_result_validator_checks_top_level_identity_and_outcome(db, field, value):
    result = _cache(db, status="found")
    result[field] = value
    assert db.execute(
        "select public.notice_dictionary_result_valid(%s,'구비서류','stdict-v1')", (Jsonb(result),),
    ).fetchone() == (False,)


@pytest.mark.parametrize("homonym", [None, "01"])
def test_valid_optional_homonym_numbers_remain_public(db, linked_row, homonym):
    result = _cache(db, status="found")
    result["entries"][0]["homonym_number"] = homonym
    db.execute("update standard_dictionary_cache set result=%s", (Jsonb(result),))
    public = _public(db, linked_row)
    assert public["dictionary_status"] == "complete"
    entry = public["dictionary_candidates"][0]["dictionary"]["entries"][0]
    assert entry["homonym_number"] == homonym


@pytest.mark.parametrize("assignment", [
    "query_word='다른말',result=jsonb_set(result,'{query_word}','\"다른말\"')",
    "contract_version='other-contract',"
    "result=jsonb_set(result,'{contract_version}','\"other-contract\"')",
    "search_conditions='{}'",
])
def test_cache_key_alone_cannot_expose_another_query_or_contract(db, linked_row, assignment):
    _cache(db, status="found")
    db.execute("update public.standard_dictionary_cache set " + assignment)
    public = _public(db, linked_row)
    assert public["dictionary_status"] == "pending"
    assert public["dictionary_candidates"][0]["dictionary"] is None


@pytest.mark.parametrize("lease", [False, True])
def test_pending_and_failed_are_distinct_from_not_found(db, linked_row, lease):
    _cache(db, lease=lease, error="dictionary_rate_limited")
    public = _public(db, linked_row)
    assert public["dictionary_status"] == ("pending" if lease else "partial")
    candidate = public["dictionary_candidates"][0]
    assert candidate["lookup_status"] == ("pending" if lease else "failed")
    assert candidate["dictionary"] is None
    assert 0 < candidate["retry_after_seconds"] <= (180 if lease else 60)


@pytest.mark.parametrize("mutation", ["hidden", "source", "deleted"])
def test_nonpublic_or_stale_source_returns_no_snapshot(db, linked_row, mutation):
    if mutation == "hidden":
        db.execute("update notices set is_visible=false where id=%s", (linked_row,))
    elif mutation == "source":
        db.execute("update notices set body_html='새 원문' where id=%s", (linked_row,))
    else:
        db.execute("delete from notices where id=%s", (linked_row,))
    assert _public(db, linked_row) is None


@pytest.mark.parametrize("mutation", ["missing", "stale-token", "mismatched-candidates"])
def test_absent_or_obsolete_links_keep_easy_text_as_pending(db, linked_row, mutation):
    if mutation == "missing":
        db.execute("delete from notice_dictionary_links")
    elif mutation == "stale-token":
        db.execute("update notice_dictionary_links set easy_text_token=repeat('b',64)")
    else:
        db.execute("update notice_dictionary_links set candidates='[]'")
    public = _public(db, linked_row)
    assert public["easy_text"] == ORIGINAL
    assert public["dictionary_candidates"] is None
    assert public["dictionary_status"] == "pending"


def test_unprocessed_and_empty_candidates_are_distinct(db, linked_row):
    db.execute("update notice_easy_texts set dictionary_candidates=null")
    public = _public(db, linked_row)
    assert public["dictionary_candidates"] is None
    assert public["dictionary_status"] == "unprocessed"
    db.execute("update notice_easy_texts set dictionary_candidates='[]'")
    db.execute(
        "update notice_dictionary_links set candidates='[]',easy_text_token="
        "(select notice_dictionary_easy_text_token(e) from notice_easy_texts e)",
    )
    public = _public(db, linked_row)
    assert public["dictionary_candidates"] == []
    assert public["dictionary_status"] == "complete"


def test_function_grants_and_search_path_are_explicit(db):
    for role in ("anon", "authenticated"):
        for function in ("notice_dictionary_links_valid(jsonb)",
                         "notice_dictionary_result_valid(jsonb,text,text)",
                         "notice_dictionary_easy_text_token(public.notice_easy_texts)"):
            assert db.execute(
                "select has_function_privilege(%s,%s,'execute')", (role, "public." + function),
            ).fetchone() == (False,)
        assert db.execute(
            "select has_function_privilege(%s,'public.get_notice_dictionary(bigint)','execute')",
            (role,),
        ).fetchone() == (True,)
    assert db.execute(
        "select prosecdef,proconfig from pg_proc "
        "where oid='public.get_notice_dictionary(bigint)'::regprocedure",
    ).fetchone() == (True, ["search_path=pg_catalog"])


@pytest.mark.parametrize("field,value", [
    ("cache_key", "secret-key"), ("start", True), ("end", 10**100),
    ("lookup_status", "not_found"), ("error_code", "secret-url"),
    ("retryable", "true"), ("extra", "definition"), ("easy_expression", None),
])
def test_database_rejects_malformed_links(db, linked_row, field, value):
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute(
            "update notice_dictionary_links set candidates=%s", (Jsonb([LINK | {field: value}]),),
        )


def test_service_role_can_write_without_bypassrls(db, linked_row):
    db.execute("set local role service_role")
    db.execute("update notice_dictionary_links set candidates=%s", (Jsonb([LINK]),))
    assert db.execute("select count(*) from notice_dictionary_links").fetchone() == (1,)
    db.execute("delete from notice_dictionary_links")
