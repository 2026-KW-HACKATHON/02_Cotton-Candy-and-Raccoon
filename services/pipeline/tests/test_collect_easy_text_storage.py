"""Real local commits bridge collection, separate easy-text saves and cache reuse."""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from pipeline.after_collect import AfterCollectEasyText
from pipeline.collect_nowon import PreparedNotice, _save_notices
from pipeline.collect_wolgye1 import _save_entries
from pipeline.config import DatabaseSettings, NowonSettings, WolgyeSettings
from pipeline.glossary.easy_language import EasyLanguageAPIError
from pipeline.glossary.notice_service import load_notice_glossary_input
from pipeline.models import RawNotice
from pipeline.sources.wolgye1_board import BoardEntry
from pipeline.storage.notice_easy_text import get_notice_easy_text
from pipeline.transform.nowon import transform_nowon_notice

_ROOT = Path(__file__).resolve().parents[3]
_BODY = "익일 방문하세요."
_TITLE = "익일 안내 제목"


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
        for role in ("anon", "authenticated"):
            if admin.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone():
                continue
            admin.execute(sql.SQL("create role {} nologin").format(sql.Identifier(role)))
            created_roles.append(role)
        test_info = {**info, "dbname": database_name}
        with psycopg.connect(**test_info) as setup:
            for name in (
                "20260922053900_init.sql",
                "20261007000000_notice_easy_text.sql",
                "20261007000004_notice_easy_text_scope.sql",
                "20261007000005_notice_easy_text_body_only.sql",
            ):
                setup.execute((_ROOT / "supabase/migrations" / name).read_text("utf-8"))
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


def _notice(source: str, post_sn: str) -> RawNotice:
    return RawNotice(
        category="nowon" if source == "nowon" else "dong",
        source_board="1001" if source == "nowon" else "1042",
        dong_group=None if source == "nowon" else "wolgye1",
        is_pinned=False,
        post_sn=post_sn,
        title=_TITLE,
        department="월계1동 행정민원팀",
        registered_on="2026-10-07",
        body_html=f"<p>{_BODY}</p>",
        license_type="KOGL-4",
        url=(
            f"https://www.nowon.kr/www/user/bbs/BD_selectBbs.do?q_bbsCode=1001&q_bbscttSn={post_sn}"
            if source == "nowon"
            else BoardEntry(post_sn, _TITLE, "월계1동", "2026-10-07", False).url
        ),
    )


def _collect(
    source: str,
    notices: tuple[RawNotice, ...],
    database: DatabaseSettings,
    processor: AfterCollectEasyText,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[int, tuple]:
    """Keep the real save loop/storage/processor; stub source network preparation."""
    conn = psycopg.connect(database.database_url, autocommit=True)
    collector_pid = conn.info.backend_pid

    def load_from_separate_connection(processor_conn, notice_id):
        assert processor_conn.info.backend_pid != collector_pid
        assert processor_conn.autocommit is False
        loaded = load_notice_glossary_input(processor_conn, notice_id)
        assert loaded.title == _TITLE
        assert loaded.body_text_present is True
        return loaded

    monkeypatch.setattr(
        "pipeline.after_collect.load_notice_glossary_input",
        load_from_separate_connection,
    )
    if source == "nowon":
        monkeypatch.setattr(
            "pipeline.collect_nowon._prepare_notice",
            lambda notice, _settings: PreparedNotice(transform_nowon_notice(notice), [], None),
        )
        return _save_notices(
            conn,
            notices,
            NowonSettings("fake", 1, 2),
            database.database_url,
            after_save=processor,
        )
    monkeypatch.setattr("pipeline.collect_wolgye1._fetch_detail_with_retry", lambda *_args: "page")
    by_post_sn = {notice.post_sn: notice for notice in notices}
    monkeypatch.setattr(
        "pipeline.collect_wolgye1.parse_detail_page",
        lambda entry, _page: by_post_sn[entry.post_sn],
    )
    monkeypatch.setattr("pipeline.collect_wolgye1.extract_dong_files", lambda *_args: [])
    entries = tuple(
        BoardEntry(notice.post_sn, notice.title, notice.department, notice.registered_on, False)
        for notice in notices
    )
    return _save_entries(
        conn,
        entries,
        WolgyeSettings(1, 2),
        database.database_url,
        after_save=processor,
    )


@pytest.mark.parametrize("source", ["nowon", "wolgye1"])
def test_collector_commit_is_visible_to_body_only_processor_and_reuses_saved_cache(
    committed_easy_db: DatabaseSettings,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    database = committed_easy_db
    notices = (_notice(source, "001"),)
    processor = AfterCollectEasyText(database)

    def request(**kwargs: object) -> str:
        assert kwargs["notice_text"] == _BODY
        # A third connection proves the raw save committed before API processing.
        with psycopg.connect(database.database_url) as observer:
            assert observer.execute("select title, body_html from notices").fetchall() == [
                (_TITLE, f"<p>{_BODY}</p>"),
            ]
        return json.dumps(
            {
                "changes": [
                    {
                        "original": "익일",
                        "replacement": "다음 날",
                        "context": _BODY,
                    }
                ]
            },
            ensure_ascii=False,
        )

    generate = MagicMock(side_effect=request)
    monkeypatch.setattr(
        "pipeline.glossary.easy_language_client.generate_easy_language_json",
        generate,
    )
    monkeypatch.setattr(
        "pipeline.glossary.easy_language_service.load_gemini_api_key",
        lambda: "fake",
    )
    assert _collect(source, notices, database, processor, monkeypatch) == (1, ())
    with psycopg.connect(database.database_url) as observer:
        notice_id = observer.execute("select id from notices").fetchone()[0]
        saved = get_notice_easy_text(observer, notice_id)
        assert saved is not None
        assert saved.original_text == _TITLE + "\n" + _BODY
        assert saved.easy_text == _TITLE + "\n다음 날 방문하세요."
        assert saved.changes[0].start == len(_TITLE) + 1
        assert saved.changes[0].context == _BODY
        assert (saved.body_text_present, saved.attachment_content_included) == (True, False)
    monkeypatch.setattr(
        "pipeline.glossary.easy_language_service.load_gemini_api_key",
        MagicMock(side_effect=AssertionError("current cache must not read a key")),
    )
    assert _collect(source, notices, database, processor, monkeypatch) == (1, ())
    assert generate.call_count == 1
    assert processor.report()["successful_count"] == 2
    assert processor.report()["failed_count"] == 0
    with psycopg.connect(database.database_url) as observer:
        assert get_notice_easy_text(observer, notice_id) == saved
        assert observer.execute("select title, body_html, is_modified from notices").fetchall() == [
            (_TITLE, f"<p>{_BODY}</p>", False),
        ]


@pytest.mark.parametrize("source", ["nowon", "wolgye1"])
def test_api_failure_preserves_committed_raw_notice_and_later_notice_still_saves(
    committed_easy_db: DatabaseSettings,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    database = committed_easy_db
    notices = (_notice(source, "001"), _notice(source, "002"))
    processor = AfterCollectEasyText(database)
    generate = MagicMock(side_effect=[EasyLanguageAPIError("simulated"), '{"changes": []}'])
    monkeypatch.setattr(
        "pipeline.glossary.easy_language_client.generate_easy_language_json",
        generate,
    )
    monkeypatch.setattr(
        "pipeline.glossary.easy_language_service.load_gemini_api_key",
        lambda: "fake",
    )
    assert _collect(source, notices, database, processor, monkeypatch) == (2, ())
    with psycopg.connect(database.database_url) as observer:
        rows = observer.execute(
            "select id, post_sn, title, body_html from notices order by post_sn",
        ).fetchall()
        assert [(post_sn, title, body) for _id, post_sn, title, body in rows] == [
            ("001", _TITLE, f"<p>{_BODY}</p>"),
            ("002", _TITLE, f"<p>{_BODY}</p>"),
        ]
        first_id, second_id = rows[0][0], rows[1][0]
        assert get_notice_easy_text(observer, first_id) is None
        assert get_notice_easy_text(observer, second_id) is not None
    assert generate.call_count == 2
    assert all(call.kwargs["notice_text"] == _BODY for call in generate.call_args_list)
    assert processor.report()["successful_count"] == processor.report()["failed_count"] == 1
    assert processor.report()["failures"] == [{"notice_id": first_id, "reason_code": "api_failed"}]
