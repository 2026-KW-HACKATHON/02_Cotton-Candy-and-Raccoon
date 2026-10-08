"""Helpers shared from test_collect_easy_text_storage.py."""

import os
from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from pipeline.config import DatabaseSettings
from support.paths import REPO_ROOT

__all__ = [
    "_TEST_ROLES",
    "committed_easy_db",
]


_ROOT = REPO_ROOT


_TEST_ROLES = ("anon", "authenticated", "service_role")


@pytest.fixture
def committed_easy_db() -> Iterator[DatabaseSettings]:
    """Create and remove an owned DB; never commit fixtures into the supplied DB.

    This module runs serially with the other local integration tests. Track roles
    created here because the suite's rollback fixtures expect no persistent roles.
    """
    database_url = os.getenv("GLOSSARY_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("GLOSSARY_TEST_DATABASE_URL 미설정: 수집 후 저장 통합 테스트 생략")
    info = conninfo_to_dict(database_url)
    if (
        info.get("host") not in {"localhost", "127.0.0.1", "::1"}
        or info.get("hostaddr") not in {None, "127.0.0.1", "::1"}
        or not info.get("dbname", "").startswith("pipeline_glossary_test_")
    ):
        raise ValueError("자동 쉬운말 검증은 전용 로컬 테스트 DB에서만 가능합니다.")
    database_name = "pipeline_glossary_test_auto_" + uuid4().hex
    admin = psycopg.connect(**{**info, "dbname": "postgres"}, autocommit=True)
    created_database = False
    created_roles: list[str] = []
    try:
        admin.execute(sql.SQL("create database {}").format(sql.Identifier(database_name)))
        created_database = True
        for role in _TEST_ROLES:
            flags = admin.execute(
                "select rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,rolreplication,rolcanlogin "
                "from pg_roles where rolname = %s", (role,),
            ).fetchone()
            if flags is None:
                admin.execute(sql.SQL("create role {} nologin").format(sql.Identifier(role)))
                created_roles.append(role)
            else:
                assert flags == (False,) * 6, "Existing test role has elevated flags"
                assert admin.execute(
                    "select count(*) from pg_auth_members "
                    "where member=(select oid from pg_roles where rolname=%s)", (role,),
                ).fetchone() == (0,), "Existing test role inherits other privileges"
        test_info = {**info, "dbname": database_name}
        with psycopg.connect(**test_info) as setup:
            setup.execute("grant usage on schema public to anon, authenticated, service_role")
            for migration in sorted((_ROOT / "supabase/migrations").glob("*.sql")):
                setup.execute(migration.read_text("utf-8"))
        yield DatabaseSettings(make_conninfo(**test_info))
    finally:
        try:
            if created_database:
                admin.execute(
                    sql.SQL("drop database {} with (force)").format(sql.Identifier(database_name))
                )
            for role in reversed(created_roles):
                admin.execute(sql.SQL("drop role {}").format(sql.Identifier(role)))
        finally:
            admin.close()
