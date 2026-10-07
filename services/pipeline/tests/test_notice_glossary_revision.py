"""Keep historical glossary results private when the collected notice changes."""

import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from pipeline.glossary.notice_service import load_notice_glossary_input

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[3]


def _safe_test_database(database_url: str) -> dict[str, str]:
    info = conninfo_to_dict(database_url)
    if (
        info.get("host") not in {"localhost", "127.0.0.1", "::1"}
        or info.get("hostaddr") not in {None, "127.0.0.1", "::1"}
        or not info.get("dbname", "").startswith("pipeline_glossary_test_")
    ):
        raise ValueError("공지 용어 통합 테스트는 전용 로컬 테스트 DB에서만 실행할 수 있습니다.")
    return info


@pytest.fixture
def notice_glossary_db():
    database_url = os.getenv("GLOSSARY_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("GLOSSARY_TEST_DATABASE_URL 미설정: 전용 PostgreSQL 테스트 미실행")
    conn = psycopg.connect(**_safe_test_database(database_url))
    try:
        assert conn.execute("select to_regclass('public.notices')").fetchone()[0] is None
        for role in ("anon", "authenticated"):
            existing = conn.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone()
            if existing is None:
                conn.execute(f"create role {role} nologin")
        conn.execute("grant usage on schema public to anon, authenticated")
        for name in (
            "20260922053900_init.sql",
            "20260922053901_rls.sql",
            "20261006000000_notice_glossary.sql",
        ):
            conn.execute((ROOT / "supabase/migrations" / name).read_text("utf-8"))
        conn.execute(
            "insert into public.notices"
            "(category, source_board, post_sn, title, registered_on, url) "
            "values ('nowon', '1001', 'test', 'test', current_date, "
            "'https://www.nowon.kr/test')"
        )
        notice_id = conn.execute("select id from public.notices").fetchone()[0]
        yield conn, notice_id
    finally:
        conn.rollback()
        conn.close()


def apply_public_revision_policy(conn):
    for name in (
        "20261007000000_notice_easy_text.sql",
        "20261007000001_notice_glossary_current_source.sql",
    ):
        conn.execute((ROOT / "supabase/migrations" / name).read_text("utf-8"))


def historical_result(source, *, seconds=0):
    """A stored historical JSON sample, independent of the removed Python workflow."""
    return {
        "notice_id": source.notice_id,
        "notice_revision": source.notice_revision,
        "source_hash": sha256(source.text.encode("utf-8")).hexdigest(),
        "rules_version": "historical-rls-test",
        "generated_at": (NOW + timedelta(seconds=seconds)).isoformat(),
        "status": "completed",
        "original_text": source.text,
        "easy_text": source.text,
        "terms": [],
        "queries": [],
        "changes": [],
        "pending_queries": [],
        "new_query_count": 0,
    }


def write_historical_result(conn, sample):
    conn.execute(
        "insert into public.notice_glossary_results "
        "(notice_id, source_hash, rules_version, generated_at, status, result) "
        "values (%s, %s, %s, %s, %s, %s) "
        "on conflict (notice_id) do update set "
        "source_hash=excluded.source_hash, rules_version=excluded.rules_version, "
        "generated_at=excluded.generated_at, status=excluded.status, result=excluded.result",
        (
            sample["notice_id"],
            sample["source_hash"],
            sample["rules_version"],
            sample["generated_at"],
            sample["status"],
            Jsonb(sample),
        ),
    )


def set_notice(conn, notice_id, title, body=None):
    conn.execute(
        "update public.notices set title = %s, body_html = %s where id = %s",
        (title, body, notice_id),
    )
    return load_notice_glossary_input(conn, notice_id)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize(
    "title,body",
    [
        ("변경된 제목", "<p>익일 안내</p>"),
        ("안내", "<p>변경된 본문</p>"),
        ("안내", "<div>익일 안내</div>"),
    ],
)
def test_db_public_reads_hide_stale_result_until_current_revision_saved(
    notice_glossary_db, role, title, body
):
    conn, notice_id = notice_glossary_db
    apply_public_revision_policy(conn)
    original = set_notice(conn, notice_id, "안내", "<p>익일 안내</p>")
    saved = historical_result(original)
    write_historical_result(conn, saved)
    conn.execute("set local role " + role)
    assert conn.execute(
        "select notice_id from public.notice_glossary_results where notice_id=%s", (notice_id,)
    ).fetchall() == [(notice_id,)]
    conn.execute("reset role")

    current = set_notice(conn, notice_id, title, body)
    assert conn.execute(
        "select result from public.notice_glossary_results where notice_id=%s", (notice_id,)
    ).fetchone() == (saved,)  # Keep the previous internal snapshot.
    conn.execute("set local role " + role)
    assert (
        conn.execute(
            "select notice_id from public.notice_glossary_results where notice_id=%s", (notice_id,)
        ).fetchall()
        == []
    )
    conn.execute("reset role")

    write_historical_result(conn, historical_result(current, seconds=1))
    conn.execute("set local role " + role)
    assert conn.execute(
        "select result->>'notice_revision' from public.notice_glossary_results where notice_id=%s",
        (notice_id,),
    ).fetchall() == [(current.notice_revision,)]


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize(
    "revision",
    ["missing", None, "", "not-a-revision", "b" * 64, "A" * 64, 123, ["b" * 64]],
)
def test_db_public_reads_hide_missing_invalid_or_different_source_revision(
    notice_glossary_db, role, revision
):
    conn, notice_id = notice_glossary_db
    apply_public_revision_policy(conn)
    current = set_notice(conn, notice_id, "안내", "<p>익일 안내</p>")
    write_historical_result(conn, historical_result(current))
    if revision == "missing":
        conn.execute(
            "update public.notice_glossary_results set result=result-'notice_revision' "
            "where notice_id=%s",
            (notice_id,),
        )
    else:
        conn.execute(
            "update public.notice_glossary_results "
            "set result=jsonb_set(result,'{notice_revision}',%s) where notice_id=%s",
            (Jsonb(revision), notice_id),
        )
    conn.execute("set local role " + role)
    assert (
        conn.execute(
            "select notice_id from public.notice_glossary_results where notice_id=%s", (notice_id,)
        ).fetchall()
        == []
    )


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_db_current_revision_does_not_make_hidden_notice_result_public(notice_glossary_db, role):
    conn, notice_id = notice_glossary_db
    apply_public_revision_policy(conn)
    current = set_notice(conn, notice_id, "안내", "<p>익일 안내</p>")
    write_historical_result(conn, historical_result(current))
    conn.execute("update public.notices set is_visible=false where id=%s", (notice_id,))
    conn.execute("set local role " + role)
    assert (
        conn.execute(
            "select notice_id from public.notice_glossary_results where notice_id=%s", (notice_id,)
        ).fetchall()
        == []
    )
