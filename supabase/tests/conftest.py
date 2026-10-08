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
    "20261007000000_notice_easy_text.sql",
    "20261007000004_notice_easy_text_scope.sql",
    "20261007000005_notice_easy_text_body_only.sql",
    "20261007120000_notice_summaries_review_content.sql",
    "20261007123000_notice_summary_card_summaries.sql",
    "20261007130000_notice_summary_executions.sql",
    "20261007133000_notice_summary_source_revisions.sql",
    "20261007140000_notice_summary_file_references.sql",
    "20261008120000_notice_summary_omissions.sql",
    "20261008130000_notice_summary_information_loss.sql",
]


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
        files = sorted((ROOT / "supabase/migrations").glob("*.sql"))
        assert [path.name for path in files] == MIGRATION_NAMES
        migrations = {path.name: path for path in files}

        def apply_migration(name: str) -> None:
            conn.execute(migrations[name].read_text(encoding="utf-8"))

        for name in (
            "20260922053900_init.sql",
            "20260922053901_rls.sql",
            "20260923044500_holidays.sql",
        ):
            apply_migration(name)
        # Verify the incremental migration backfills actual pre-existing data.
        legacy_id = conn.execute(
            "insert into notices(category,source_board,post_sn,title,registered_on,url,created_at) "
            "values ('nowon','1001','schema-backfill','old',current_date,"
            "'https://www.nowon.kr/test/old','2026-01-02T03:04:05Z') returning id"
        ).fetchone()[0]
        apply_migration("20261006120000_notice_summaries.sql")
        assert conn.execute(
            "select content_updated_at = created_at from notices where id=%s",
            (legacy_id,),
        ).fetchone() == (True,)
        # Old review rows have no public result and must survive the new check.
        conn.execute(
            "insert into notice_summaries "
            "(notice_id,status,attachment_status,source_hash,model,prompt_version,generated_at) "
            "values (%s,'needs_review','partial',repeat('c',64),'test','test',now())",
            (legacy_id,),
        )
        for name in (
            "20261007000000_notice_easy_text.sql",
            "20261007000004_notice_easy_text_scope.sql",
            "20261007000005_notice_easy_text_body_only.sql",
        ):
            apply_migration(name)
        apply_migration("20261007120000_notice_summaries_review_content.sql")
        # Existing result JSON predates card text; both absent and explicit null
        # cards must backfill to SQL NULL without changing the original result.
        legacy_cards = conn.execute(
            "insert into notices(category,source_board,post_sn,title,registered_on,url) "
            "values ('nowon','1001','schema-legacy-missing-card','old',current_date,"
            "'https://www.nowon.kr/test/missing-card'),"
            "('nowon','1001','schema-legacy-null-card','old',current_date,"
            "'https://www.nowon.kr/test/null-card') returning id,post_sn"
        ).fetchall()
        for notice_id, post_sn in legacy_cards:
            conn.execute(
                "insert into notice_summaries "
                "(notice_id,status,result,category,category_code,attachment_status,"
                "source_hash,model,prompt_version,generated_at) "
                "values (%s,'summarized',jsonb_build_object('category','living',"
                "'category_code',27) || %s::jsonb,'living',27,'none',repeat('c',64),"
                "'test','test',now())",
                (
                    notice_id,
                    '{"card_summaries":null}' if "null-card" in post_sn else "{}",
                ),
            )
        apply_migration("20261007123000_notice_summary_card_summaries.sql")
        apply_migration("20261007130000_notice_summary_executions.sql")
        legacy_execution = conn.execute(
            "insert into notice_summary_executions(notice_id) values (%s) "
            "returning execution_token",
            (legacy_id,),
        ).fetchone()[0]
        apply_migration("20261007133000_notice_summary_source_revisions.sql")
        apply_migration("20261007140000_notice_summary_file_references.sql")
        apply_migration("20261008120000_notice_summary_omissions.sql")
        apply_migration("20261008130000_notice_summary_information_loss.sql")
        upgraded_execution = conn.execute(
            "select e.execution_token,e.source_revision,n.content_revision "
            "from notice_summary_executions e join notices n on n.id=e.notice_id "
            "where e.notice_id=%s",
            (legacy_id,),
        ).fetchone()
        assert upgraded_execution[0] > legacy_execution
        assert upgraded_execution[1:] == (1, 1)
        assert conn.execute(
            "select result,category,category_code,deadline_on,card_summaries "
            "from notice_summaries where notice_id=%s",
            (legacy_id,),
        ).fetchone() == (None, None, None, None, None)
        for notice_id, post_sn in legacy_cards:
            expected = {"category": "living", "category_code": 27}
            if "null-card" in post_sn:
                expected["card_summaries"] = None
            assert conn.execute(
                "select result,card_summaries from notice_summaries where notice_id=%s",
                (notice_id,),
            ).fetchone() == (expected, None)
            conn.execute("delete from notices where id=%s", (notice_id,))
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
