"""Apply the real migrations in a rollback-only disposable PostgreSQL DB."""

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_NAMES = [
    "20260922053900_init.sql",
    "20260922053901_rls.sql",
    "20260923044500_holidays.sql",
    "20261006120000_notice_summaries.sql",
]


@pytest.fixture(scope="module")
def database() -> Iterator[psycopg.Connection]:
    dsn = os.environ.get("SCHEMA_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("SCHEMA_TEST_DATABASE_URL is required")
    conn = psycopg.connect(dsn)
    if conn.info.host != "127.0.0.1" or not conn.info.dbname.startswith("pipeline_schema_test_"):
        conn.close()
        pytest.fail("Use a disposable loopback pipeline_schema_test_* database")
    try:
        assert conn.execute("select to_regclass('public.notices')").fetchone()[0] is None
        # Reproduce the broad default grants to ensure the migration revokes them.
        # service_role deliberately has no BYPASSRLS: its explicit policy must work.
        conn.execute("create role anon; create role authenticated; create role service_role")
        conn.execute("grant usage on schema public to anon, authenticated, service_role")
        conn.execute(
            "alter default privileges in schema public "
            "grant all on tables to anon, authenticated, service_role"
        )
        conn.execute(
            "alter default privileges in schema public "
            "grant all on sequences to anon, authenticated, service_role"
        )
        files = sorted((ROOT / "supabase/migrations").glob("*.sql"))
        assert [path.name for path in files] == MIGRATION_NAMES
        for path in files[:3]:
            conn.execute(path.read_text(encoding="utf-8"))
        # Verify the incremental migration backfills actual pre-existing data.
        legacy_id = conn.execute(
            "insert into notices(category,source_board,post_sn,title,registered_on,url,created_at) "
            "values ('nowon','1001','schema-backfill','old',current_date,"
            "'https://www.nowon.kr/test/old','2026-01-02T03:04:05Z') returning id"
        ).fetchone()[0]
        conn.execute(files[3].read_text(encoding="utf-8"))
        assert conn.execute(
            "select content_updated_at = created_at from notices where id=%s", (legacy_id,)
        ).fetchone() == (True,)
        conn.execute("delete from notices where id=%s", (legacy_id,))
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
