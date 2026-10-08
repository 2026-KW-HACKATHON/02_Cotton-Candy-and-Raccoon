"""Scheduled Nowon runs select a bounded recent scope without changing the schema."""

import json
import os
from dataclasses import replace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg
import pytest

from pipeline.cli import main
from pipeline.collect_nowon import (
    ScheduledNowonResult,
    _fetch_api_page_with_retry,
    collect_and_save_nowon_scheduled,
)
from pipeline.config import DatabaseSettings, NowonSettings
from pipeline.models import RawNotice
from pipeline.sources.nowon_api import NowonPage, NowonSourceError
from pipeline.sources.nowon_page import NowonPageError, NowonPageMissing
from pipeline.storage.notice_bundle import save_notice_with_files
from pipeline.transform.nowon import transform_nowon_notice

EMPTY_ATTACHMENTS = "<table><tr><th>첨부파일</th><td>첨부파일이 없습니다</td></tr></table>"


def _notice(post_sn: str, *, title: str = "제목") -> RawNotice:
    return RawNotice(
        source_board="1001",
        category="nowon", dong_group=None, is_pinned=False,
        post_sn=post_sn, title=title, department="행정팀",
        registered_on="2026-09-28",
        url=("https://www.nowon.kr/www/user/bbs/BD_selectBbs.do"
             f"?q_bbsCode=1001&q_bbscttSn={post_sn}"),
        body_html="<p>본문</p>", license_type="KOGL-4",
    )


def _connection(known: set[str], *, has_history: bool) -> MagicMock:
    conn = MagicMock()
    conn.closed = False

    def execute(query: str, params: tuple[list[str]] | None = None) -> MagicMock:
        result = MagicMock()
        if "SELECT EXISTS" in query:
            result.fetchone.return_value = (has_history,)
        else:
            result.fetchall.return_value = [(sn,) for sn in params[0] if sn in known]
        return result

    conn.execute.side_effect = execute
    return conn


def _settings() -> tuple[NowonSettings, DatabaseSettings]:
    return NowonSettings("test-key", 2.0, 7.0), DatabaseSettings(
        "postgresql://test@localhost/db",
    )


def test_empty_database_attempts_first_fifty_even_in_refresh_mode() -> None:
    notices = tuple(_notice(str(index)) for index in range(1, 51))
    first = NowonPage(1, 50, 60, notices)
    settings, database = _settings()
    conn = _connection(set(), has_history=False)
    with (
        patch("pipeline.collect_nowon.psycopg.connect", return_value=conn),
        patch("pipeline.collect_nowon._fetch_api_page_with_retry", return_value=first) as fetch,
        patch("pipeline.collect_nowon._save_notices", return_value=(50, ())) as save,
    ):
        result = collect_and_save_nowon_scheduled(settings, database, mode="refresh")
    assert (result.initial_baseline, result.selected_count, result.complete) == (
        True, 50, True,
    )
    fetch.assert_called_once_with(settings, 1, 50)
    assert len(save.call_args.args[1]) == 50


def test_new_mode_skips_known_top_ten_without_expansion() -> None:
    notices = tuple(_notice(str(index)) for index in range(1, 51))
    first = NowonPage(1, 50, 100, notices)
    settings, database = _settings()
    conn = _connection({"2", "9"}, has_history=True)
    with (
        patch("pipeline.collect_nowon.psycopg.connect", return_value=conn),
        patch("pipeline.collect_nowon._fetch_api_page_with_retry", return_value=first) as fetch,
        patch("pipeline.collect_nowon._save_notices", return_value=(8, ())) as save,
    ):
        result = collect_and_save_nowon_scheduled(settings, database, mode="new")
    assert result.initial_baseline is False
    assert result.selected_count == 8
    assert result.complete is True
    assert [notice.post_sn for notice in save.call_args.args[1]] == [
        "1", "3", "4", "5", "6", "7", "8", "10",
    ]
    fetch.assert_called_once()


def test_ten_new_posts_expand_until_first_known_id() -> None:
    notices = tuple(_notice(str(index)) for index in range(1, 51))
    first = NowonPage(1, 50, 100, notices)
    settings, database = _settings()
    conn = _connection({"13"}, has_history=True)
    with (
        patch("pipeline.collect_nowon.psycopg.connect", return_value=conn),
        patch("pipeline.collect_nowon._fetch_api_page_with_retry", return_value=first) as fetch,
        patch("pipeline.collect_nowon._save_notices", return_value=(12, ())) as save,
    ):
        result = collect_and_save_nowon_scheduled(settings, database, mode="new")
    assert result.selected_count == 12
    assert [notice.post_sn for notice in save.call_args.args[1]] == [
        str(index) for index in range(1, 13)
    ]
    fetch.assert_called_once()


def test_expansion_across_api_pages_rechecks_first_id() -> None:
    first = NowonPage(1, 50, 95, tuple(_notice(str(index)) for index in range(1, 51)))
    second = NowonPage(46, 95, 95, tuple(_notice(str(index)) for index in range(46, 96)))
    confirm = NowonPage(1, 1, 95, (_notice("1"),))
    settings, database = _settings()
    conn = _connection({"52"}, has_history=True)
    with (
        patch("pipeline.collect_nowon.psycopg.connect", return_value=conn),
        patch("pipeline.collect_nowon._fetch_api_page_with_retry",
              side_effect=[first, second, confirm]) as fetch,
        patch("pipeline.collect_nowon._save_notices", return_value=(51, ())) as save,
        patch("pipeline.collect_nowon.sleep"),
    ):
        result = collect_and_save_nowon_scheduled(settings, database, mode="new")
    assert result.pages_read == 2
    assert result.selected_count == 51
    assert result.complete is True
    assert [call.args[1:] for call in fetch.call_args_list] == [
        (1, 50), (46, 95), (1, 1),
    ]
    assert save.call_args.args[1][-1].post_sn == "51"


def test_sample_key_is_rejected_before_db_or_network() -> None:
    with (
        patch("pipeline.collect_nowon.psycopg.connect") as connect,
        patch("pipeline.collect_nowon._fetch_api_page_with_retry") as fetch,
    ):
        with pytest.raises(ValueError, match="발급받은 API 키"):
            collect_and_save_nowon_scheduled(
                NowonSettings("sample", 2.0, 7.0), _settings()[1], mode="new",
            )
    connect.assert_not_called()
    fetch.assert_not_called()


def test_api_rate_limit_stops_before_detail_requests() -> None:
    settings, database = _settings()
    conn = _connection(set(), has_history=True)
    with (
        patch("pipeline.collect_nowon.psycopg.connect", return_value=conn),
        patch("pipeline.collect_nowon._fetch_api_page_with_retry",
              side_effect=NowonSourceError("429", retryable=True, rate_limited=True)),
        patch("pipeline.collect_nowon._save_notices") as save,
    ):
        with pytest.raises(NowonSourceError, match="429"):
            collect_and_save_nowon_scheduled(settings, database, mode="new")
    save.assert_not_called()
    conn.close.assert_called_once()


def test_api_rate_limit_is_not_retried_immediately() -> None:
    settings, _database = _settings()
    with (
        patch("pipeline.collect_nowon.collect_page",
              side_effect=NowonSourceError("429", retryable=True,
                                          rate_limited=True)) as fetch,
        patch("pipeline.collect_nowon.sleep") as wait,
    ):
        with pytest.raises(NowonSourceError, match="429"):
            _fetch_api_page_with_retry(settings, 1, 50)
    fetch.assert_called_once()
    wait.assert_not_called()


def test_missing_original_page_is_reported_without_saving_that_notice() -> None:
    notices = tuple(_notice(str(index)) for index in range(1, 12))
    first = NowonPage(1, 50, 11, notices)
    settings, database = _settings()
    conn = _connection({"11"}, has_history=True)

    def detail(notice: RawNotice, _settings: NowonSettings) -> tuple[str, str]:
        if notice.post_sn == "3":
            raise NowonPageMissing("missing")
        return notice.url, EMPTY_ATTACHMENTS

    with (
        patch("pipeline.collect_nowon.psycopg.connect", return_value=conn),
        patch("pipeline.collect_nowon._fetch_api_page_with_retry", return_value=first),
        patch("pipeline.collect_nowon.fetch_notice_page", side_effect=detail),
        patch("pipeline.collect_nowon.save_notice_with_files") as save,
        patch("pipeline.collect_nowon.sleep"),
    ):
        result = collect_and_save_nowon_scheduled(settings, database, mode="new")
    assert result.selected_count == 10
    assert result.saved_count == 9
    assert result.complete is False
    assert [(failure.post_sn, failure.reason_code) for failure in result.failures] == [
        ("3", "source_page_missing"),
    ]
    assert save.call_count == 9


def test_rate_limit_on_original_page_stops_remaining_details() -> None:
    notices = tuple(_notice(str(index)) for index in range(1, 4))
    first = NowonPage(1, 50, 3, notices)
    settings, database = _settings()
    conn = _connection(set(), has_history=True)
    with (
        patch("pipeline.collect_nowon.psycopg.connect", return_value=conn),
        patch("pipeline.collect_nowon._fetch_api_page_with_retry", return_value=first),
        patch("pipeline.collect_nowon.fetch_notice_page",
              side_effect=NowonPageError("429", retryable=True,
                                         rate_limited=True)) as detail,
        patch("pipeline.collect_nowon.save_notice_with_files") as save,
    ):
        result = collect_and_save_nowon_scheduled(settings, database, mode="refresh")
    detail.assert_called_once()
    save.assert_not_called()
    assert result.complete is False
    assert [failure.reason_code for failure in result.failures] == [
        "rate_limited", "rate_limited_not_attempted", "rate_limited_not_attempted",
    ]


def test_cli_scheduled_result_has_scoped_completion(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test@localhost/db")
    monkeypatch.setenv("NOWON_NOTICE_API_KEY", "test-key")
    result = ScheduledNowonResult("new", 547, 0, 0, 1, False, True, (), ())
    with patch("pipeline.cli.collect_and_save_nowon_scheduled", return_value=result) as run:
        assert main(["collect", "--source", "nowon", "--mode", "new"]) == 0
    assert run.call_args.kwargs == {"mode": "new"}
    summary = json.loads(capsys.readouterr().out)
    assert (summary["total_count"], summary["selected_count"], summary["complete"]) == (
        547, 0, True,
    )


def test_real_db_new_then_refresh_detects_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.getenv("PIPELINE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PIPELINE_TEST_DATABASE_URL is required for Nowon DB integration")
    post_sns = [f"{uuid4().int % 10**17:017d}" for _ in range(11)]
    notices = tuple(_notice(post_sn) for post_sn in post_sns)
    first = NowonPage(1, 50, 11, notices)
    settings = NowonSettings("test-key", 2.0, 7.0)
    database = DatabaseSettings(database_url)
    changed = False

    def api_page(_settings: NowonSettings, *, start_index: int, end_index: int) -> NowonPage:
        assert (start_index, end_index) == (1, 50)
        if changed:
            return replace(first, notices=(replace(notices[0], title="바뀐 제목"), *notices[1:]))
        return first

    monkeypatch.setattr("pipeline.collect_nowon.collect_page", api_page)
    monkeypatch.setattr("pipeline.collect_nowon.fetch_notice_page",
                        lambda notice, _settings: (notice.url, EMPTY_ATTACHMENTS))
    monkeypatch.setattr("pipeline.collect_nowon.sleep", lambda *_args: None)
    try:
        with psycopg.connect(database_url) as conn:
            save_notice_with_files(conn, transform_nowon_notice(notices[10]), [])
        first_run = collect_and_save_nowon_scheduled(settings, database, mode="new")
        assert (first_run.selected_count, first_run.saved_count, first_run.complete) == (
            10, 10, True,
        )
        repeat = collect_and_save_nowon_scheduled(settings, database, mode="new")
        assert (repeat.selected_count, repeat.saved_count, repeat.complete) == (0, 0, True)
        changed = True
        refresh = collect_and_save_nowon_scheduled(settings, database, mode="refresh")
        assert (refresh.selected_count, refresh.saved_count, refresh.complete) == (
            10, 10, True,
        )
        with psycopg.connect(database_url) as conn:
            rows = conn.execute(
                "SELECT post_sn, title, is_modified FROM notices "
                "WHERE category='nowon' AND post_sn = ANY(%s)", (post_sns,),
            ).fetchall()
            assert len(rows) == 11
            assert [(title, modified) for sn, title, modified in rows if sn == post_sns[0]] == [
                ("바뀐 제목", True),
            ]
            assert sum(modified for _sn, _title, modified in rows) == 1
    finally:
        with psycopg.connect(database_url) as conn:
            conn.execute(
                "DELETE FROM notices WHERE category='nowon' AND post_sn = ANY(%s)",
                (post_sns,),
            )
