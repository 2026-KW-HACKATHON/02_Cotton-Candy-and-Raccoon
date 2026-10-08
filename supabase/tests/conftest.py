"""Apply the real migrations in a rollback-only disposable PostgreSQL DB."""

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def database() -> Iterator[psycopg.Connection]:
    dsn = os.environ.get("SCHEMA_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("SCHEMA_TEST_DATABASE_URL is required")
    conn = psycopg.connect(dsn)
    if conn.info.host != "127.0.0.1" or not conn.info.dbname.startswith(
        "pipeline_schema_test_"
    ):
        conn.close()
        pytest.fail("Use a disposable loopback pipeline_schema_test_* database")
    try:
        assert (
            conn.execute("select to_regclass('public.notices')").fetchone()[0] is None
        )
        # Reproduce the broad default grants to ensure the migration revokes them.
        # service_role deliberately has no BYPASSRLS: its explicit policy must work.
        # A cluster may already contain the isolated pipeline-test roles. Reuse
        # only plain NOLOGIN roles without elevated flags or inherited roles;
        # never silently accept a role that could bypass these RLS assertions.
        for role in ("anon", "authenticated", "service_role"):
            flags = conn.execute(
                "select rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,rolreplication,rolcanlogin "
                "from pg_roles where rolname=%s",
                (role,),
            ).fetchone()
            if flags is None:
                conn.execute("create role " + role)
            else:
                assert flags == (False,) * 6, "Existing test role has elevated flags"
                assert conn.execute(
                    "select count(*) from pg_auth_members "
                    "where member=(select oid from pg_roles where rolname=%s)",
                    (role,),
                ).fetchone() == (0,), "Existing test role inherits other privileges"
        conn.execute(
            "grant usage on schema public to anon, authenticated, service_role"
        )
        conn.execute(
            "alter default privileges in schema public "
            "grant all on tables to anon, authenticated, service_role"
        )
        conn.execute(
            "alter default privileges in schema public "
            "grant all on sequences to anon, authenticated, service_role"
        )
        # Supabase also grants EXECUTE on new functions to these roles explicitly, so
        # revoking from PUBLIC alone leaves them executable by the app.
        conn.execute(
            "alter default privileges in schema public "
            "grant all on functions to anon, authenticated, service_role"
        )
        # The migrations create the schema from scratch; apply them in version order.
        files = sorted((ROOT / "supabase/migrations").glob("*.sql"))
        assert files, "supabase/migrations has no migration files"
        for path in files:
            conn.execute(path.read_text(encoding="utf-8"))
        conn.execute((ROOT / "supabase/seed.sql").read_text(encoding="utf-8"))
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def db(database: psycopg.Connection) -> Iterator[psycopg.Connection]:
    database.execute("savepoint schema_test")
    try:
        yield database
    finally:
        database.execute("rollback to savepoint schema_test")
        database.execute("release savepoint schema_test")
