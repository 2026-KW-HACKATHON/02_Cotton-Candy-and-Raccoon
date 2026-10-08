"""Real local commits bridge collection, separate easy-text saves and cache reuse."""

import json
from unittest.mock import MagicMock

import psycopg
import pytest
from support.collect_easy_text_storage import committed_easy_db as committed_easy_db

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

_BODY = "익일 방문하세요."
_TITLE = "익일 안내 제목"


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
