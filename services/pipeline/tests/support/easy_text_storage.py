"""Helpers shared from test_easy_text_storage.py."""

import json
import os
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from pipeline.glossary.easy_language import (
    simplify_notice,
)
from pipeline.glossary.notice_service import load_notice_glossary_input
from support.collect_easy_text_storage import _TEST_ROLES
from support.paths import REPO_ROOT

__all__ = [
    "_NOW",
    "_easy_db_connection",
    "_notice",
    "_request",
    "_result",
    "easy_db",
    "service_db",
]


_ROOT = REPO_ROOT


_NOW = datetime(2026, 10, 7, 9, tzinfo=UTC)


def _easy_db_connection():
    url = os.getenv("GLOSSARY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("GLOSSARY_TEST_DATABASE_URL 미설정: 로컬 DB 통합 테스트 생략")
    info = conninfo_to_dict(url)
    if (
        info.get("host") not in {"localhost", "127.0.0.1", "::1"}
        or info.get("hostaddr") not in {None, "127.0.0.1", "::1"}
        or not info.get("dbname", "").startswith("pipeline_glossary_test_")
    ):
        raise ValueError("쉬운말 검증은 전용 로컬 테스트 DB에서만 가능합니다.")
    conn = psycopg.connect(**info)
    try:
        assert conn.execute("select to_regclass('public.notices')").fetchone()[0] is None
        for role in _TEST_ROLES:
            flags = conn.execute(
                "select rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,rolreplication,rolcanlogin "
                "from pg_roles where rolname = %s", (role,),
            ).fetchone()
            if flags is None:
                conn.execute(f"create role {role} nologin")
            else:
                assert flags == (False,) * 6, "Existing test role has elevated flags"
                assert conn.execute(
                    "select count(*) from pg_auth_members "
                    "where member=(select oid from pg_roles where rolname=%s)", (role,),
                ).fetchone() == (0,), "Existing test role inherits other privileges"
        conn.execute("grant usage on schema public to anon, authenticated, service_role")
        for migration in sorted((_ROOT / "supabase/migrations").glob("*.sql")):
            conn.execute(migration.read_text("utf-8"))
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def easy_db():
    yield from _easy_db_connection()


@pytest.fixture
def service_db(committed_easy_db):
    """Allow the service to commit only into its owned disposable local DB."""
    with psycopg.connect(committed_easy_db.database_url) as conn:
        yield conn


def _notice(conn):
    notice_id = conn.execute(
        "insert into public.notices "
        "(category, source_board, post_sn, title, body_html, registered_on, url) "
        "values ('nowon', '1001', 'easy-test', '참가자 모집', "
        "'<p>구비서류를 지참하세요. 참가비는 30,000원입니다.</p>', "
        "current_date, 'https://www.nowon.kr/test') returning id"
    ).fetchone()[0]
    return load_notice_glossary_input(conn, notice_id)


def _request(**kwargs):
    return json.dumps(
        {
            "changes": [
                {
                    "original": "구비서류를",
                    "replacement": "준비할 서류를",
                    "context": "구비서류를 지참하세요.",
                }
            ],
        },
        ensure_ascii=False,
    )


def _result(source, now=_NOW):
    return simplify_notice(source, api_key="fake", request=_request, clock=lambda: now)
