"""Private dictionary cache permissions and database-level identity invariants."""

import psycopg
import pytest
from psycopg.types.json import Jsonb


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("statement", [
    "select * from public.standard_dictionary_cache",
    "insert into public.standard_dictionary_cache(cache_key) values (repeat('a',64))",
    "update public.standard_dictionary_cache set last_error_code=null",
    "delete from public.standard_dictionary_cache",
    "truncate public.standard_dictionary_cache",
])
def test_app_cannot_read_or_change_dictionary_cache(db, role, statement):
    db.execute("set local role " + role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute(statement)


def _insert(db):
    db.execute(
        "insert into public.standard_dictionary_cache "
        "(cache_key,query_word,search_conditions,contract_version) "
        "values (repeat('a',64),'지원','{}','stdict-v1')",
    )


def test_service_role_operates_without_bypassrls(db):
    db.execute("set local role service_role")
    _insert(db)
    assert db.execute(
        "select query_word from public.standard_dictionary_cache"
    ).fetchone() == ("지원",)
    db.execute(
        "update public.standard_dictionary_cache set lease_token=gen_random_uuid(),"
        "lease_expires_at=clock_timestamp()+interval '3 minutes'",
    )
    db.execute("delete from public.standard_dictionary_cache")
    assert db.execute("select count(*) from public.standard_dictionary_cache").fetchone() == (0,)
    assert db.execute(
        "select relrowsecurity from pg_class "
        "where oid='public.standard_dictionary_cache'::regclass",
    ).fetchone() == (True,)


@pytest.mark.parametrize("field,value", [
    ("query_word", "신청"), ("contract_version", "different-contract"),
    ("status", "failed"), ("status", None), ("entries", None),
    ("entries", [{"unexpected": "nonempty not_found"}]),
])
def test_result_must_match_query_contract_and_outcome(db, field, value):
    _insert(db)
    result = {"query_word": "지원", "contract_version": "stdict-v1",
              "status": "not_found", "entries": []}
    result[field] = value
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "update public.standard_dictionary_cache set result=%s,"
            "result_updated_at=clock_timestamp()", (Jsonb(result),),
        )


@pytest.mark.parametrize("assignment", [
    "lease_token=gen_random_uuid()", "lease_expires_at=clock_timestamp()",
    "result_updated_at=clock_timestamp()", "last_error_code='secret-api-key'",
    "lease_token=gen_random_uuid(),lease_expires_at=clock_timestamp(),"
    "retry_after_at=clock_timestamp()",
])
def test_partial_ownership_metadata_and_unapproved_error_messages_are_rejected(db, assignment):
    _insert(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("update public.standard_dictionary_cache set " + assignment)


def test_public_and_app_roles_have_no_grants_even_with_broad_defaults(db):
    for role in ("anon", "authenticated"):
        privileges = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
        for privilege in privileges:
            assert db.execute(
                "select has_table_privilege(%s,'public.standard_dictionary_cache',%s)",
                (role, privilege),
            ).fetchone() == (False,)
