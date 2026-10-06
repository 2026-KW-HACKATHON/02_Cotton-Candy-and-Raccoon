"""Real PostgreSQL cache reuse across notices; dictionary providers are offline fakes."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from pipeline.glossary.client import GlossaryAPIError
from pipeline.glossary.models import GlossaryEntry
from pipeline.glossary.notice_service import process_and_store_notice_glossary
from pipeline.glossary.source import NoticeGlossaryInput
from pipeline.storage.glossary import get_glossary
from pipeline.storage.notice_glossary import get_notice_glossary

_ROOT = Path(__file__).resolve().parents[3]
_TIME = datetime(2026, 10, 6, 12, tzinfo=UTC)


def _safe_test_database(database_url: str) -> dict[str, str]:
    info = conninfo_to_dict(database_url)
    if (
        info.get("host") not in {"localhost", "127.0.0.1", "::1"}
        or info.get("hostaddr") not in {None, "127.0.0.1", "::1"}
        or not info.get("dbname", "").startswith("pipeline_glossary_test_")
    ):
        raise ValueError("캐시 통합 테스트에는 전용 로컬 테스트 DB가 필요합니다.")
    return info


@pytest.fixture
def cache_db():
    database_url = os.getenv("GLOSSARY_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("GLOSSARY_TEST_DATABASE_URL 미설정: 전용 PostgreSQL 테스트 미실행")
    conn = psycopg.connect(**_safe_test_database(database_url))
    try:
        for table in ("notices", "glossary_lookups", "notice_glossary_results"):
            assert (
                conn.execute("select to_regclass(%s)", (f"public.{table}",)).fetchone()[0] is None
            )
        for role in ("anon", "authenticated"):
            exists = conn.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone()
            if exists is None:
                conn.execute(f"create role {role} nologin")
        conn.execute("grant usage on schema public to anon, authenticated")
        for name in (
            "20260922053900_init.sql",
            "20261005000000_glossary.sql",
            "20261006000000_notice_glossary.sql",
        ):
            conn.execute((_ROOT / "supabase/migrations" / name).read_text(encoding="utf-8"))
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _notice(conn, post_sn: str) -> int:
    return conn.execute(
        "insert into public.notices "
        "(category, source_board, post_sn, title, body_html, registered_on, url) "
        "values ('nowon', '1001', %s, '수집한 제목', '<p>수집한 원문</p>', "
        "current_date, 'https://www.nowon.kr/test') returning id",
        (post_sn,),
    ).fetchone()[0]


def _entry(query: str, easy_terms=()) -> GlossaryEntry:
    entry_id = {"익일": "123", "공람": "456", "기안": "789", "금회": "999", "송부": "200429"}[query]
    return GlossaryEntry(
        provider="opendict",
        entry_id=entry_id,
        sense_id="001",
        headword=query,
        definition=f"{query}에 관한 공식 사전 뜻풀이.",
        easy_terms=easy_terms,
        source_url=f"https://opendict.korean.go.kr/dictionary/view?sense_no={entry_id}",
    )


class OfflineClient:
    """Provider responses and request counts without opening an HTTP connection."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []
        self.forbid_calls = False

    def lookup(self, query):
        if self.forbid_calls:
            raise AssertionError("a saved dictionary lookup triggered another provider request")
        self.calls.append(query)
        answer = self.answers.get(query, ())
        if isinstance(answer, Exception):
            raise answer
        return answer


def _process(conn, source, clients, when):
    return process_and_store_notice_glossary(
        conn,
        source,
        onterm_client=clients[0],
        opendict_client=clients[1],
        clock=lambda: when,
    )


def test_db_different_notices_reuse_all_normal_dictionary_outcomes_without_requests(
    cache_db,
) -> None:
    conn = cache_db
    first_id, second_id = _notice(conn, "cache-first"), _notice(conn, "cache-second")
    original = " \t익일 공람 기안 송부 정산없음: 2026-10-06 100만원.\r\n"
    sources = [
        NoticeGlossaryInput(notice_id=notice_id, text=original)
        for notice_id in (first_id, second_id)
    ]
    clients = (
        OfflineClient(),
        OfflineClient(
            {
                "익일": (_entry("익일", ("다음 날",)),),
                "공람": (_entry("공람"),),
                "기안": (
                    _entry("기안", ("작성",)),
                    _entry("기안", ("초안 작성",)).model_copy(update={"sense_id": "002"}),
                ),
                "송부": (_entry("송부", ("물건보냄", "보냄")),),
            }
        ),
    )
    first = _process(conn, sources[0], clients, _TIME)
    assert first.status == "completed" and first.new_query_count == 5
    assert {term.word: term.status for term in first.terms} == {
        "익일": "replaced",
        "공람": "explained",
        "기안": "ambiguous",
        "송부": "replaced",
        "정산없음": "not_found",
    }
    requests = [tuple(client.calls) for client in clients]
    assert requests == [
        ("익일", "공람", "기안", "송부", "정산없음"),
        ("익일", "공람", "기안", "송부", "정산없음"),
    ]
    for client in clients:
        client.forbid_calls = True
    second = _process(conn, sources[1], clients, _TIME + timedelta(seconds=1))
    assert second.notice_id != first.notice_id
    assert second.status == "completed" and second.new_query_count == 0
    assert first.original_text == second.original_text == original
    assert (
        first.easy_text
        == second.easy_text
        == original.replace("익일", "다음 날").replace("송부", "물건보냄(보냄)")
    )
    assert second.terms == first.terms
    assert all(outcome.lookup.from_cache for outcome in second.queries)
    assert [tuple(client.calls) for client in clients] == requests
    for query in ("익일", "공람", "기안", "송부", "정산없음"):
        cached = get_glossary(conn, query)
        assert cached is not None and cached.from_cache
        assert cached.status == ("not_found" if query == "정산없음" else "found")
        assert cached.providers_checked == ("onterm", "opendict")
    assert get_notice_glossary(conn, second_id) == second
    assert conn.execute("select count(*) from public.glossary_lookups").fetchone()[0] == 5
    assert conn.execute("select count(*) from public.notice_glossary_results").fetchone()[0] == 2
    assert conn.execute("select title, body_html from public.notices order by id").fetchall() == [
        ("수집한 제목", "<p>수집한 원문</p>"),
        ("수집한 제목", "<p>수집한 원문</p>"),
    ]


def test_db_failures_are_not_cached_and_other_notice_success_can_resume_original(cache_db) -> None:
    conn = cache_db
    first_id, second_id = _notice(conn, "retry-first"), _notice(conn, "retry-second")
    original = "금회 익일"
    first_source = NoticeGlossaryInput(notice_id=first_id, text=original)
    second_source = NoticeGlossaryInput(notice_id=second_id, text=original)
    clients = (
        OfflineClient(),
        OfflineClient(
            {
                "금회": GlossaryAPIError("opendict", "timeout"),
                "익일": (_entry("익일", ("다음 날",)),),
            }
        ),
    )
    first = _process(conn, first_source, clients, _TIME)
    assert first.status == "partial" and first.pending_queries == ("금회",)
    assert first.original_text == original and first.easy_text == "금회 다음 날"
    assert first.terms[0].status == "failed"
    assert get_glossary(conn, "금회") is None
    assert get_glossary(conn, "익일").from_cache
    before = [len(client.calls) for client in clients]
    clients[1].answers["금회"] = (_entry("금회", ("이번 회",)),)
    second = _process(conn, second_source, clients, _TIME + timedelta(seconds=1))
    assert second.status == "completed" and second.new_query_count == 1
    assert second.original_text == original and second.easy_text == "이번 회 다음 날"
    assert [client.calls[count:] for client, count in zip(clients, before, strict=True)] == [
        ["금회"],
        ["금회"],
    ]
    for client in clients:
        client.forbid_calls = True
    resumed = _process(conn, first_source, clients, _TIME + timedelta(seconds=2))
    assert resumed.status == "completed" and resumed.new_query_count == 0
    assert resumed.original_text == original and resumed.easy_text == second.easy_text
    assert get_notice_glossary(conn, first_id) == resumed
    assert get_glossary(conn, "금회").status == "found"
    assert conn.execute("select count(*) from public.glossary_lookups").fetchone()[0] == 2
