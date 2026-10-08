import json
import os
from dataclasses import replace
from datetime import date
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg
import pytest

from pipeline.cli import main
from pipeline.collect_wolgye1 import (
    ScheduledWolgyeResult,
    WolgyeListing,
    collect_and_save_wolgye1,
    collect_and_save_wolgye1_scheduled,
    collect_one_wolgye1,
    collect_wolgye_listing,
)
from pipeline.config import DatabaseSettings, WolgyeSettings
from pipeline.models import FileRecord, NoticeRecord, RawNotice
from pipeline.sources.wolgye1_board import BoardEntry, BoardPage, WolgyeSourceError


def _entry(post_sn: str, department: str, pinned: bool) -> BoardEntry:
    return BoardEntry(post_sn, "제목", department, "2026-09-28", pinned)


def _notice(entry: BoardEntry) -> RawNotice:
    return RawNotice(
        source_board="1042",
        category="dong", dong_group="wolgye1", is_pinned=entry.is_pinned,
        post_sn=entry.post_sn, title="제목", department="월계1동 행정민원팀",
        registered_on="2026-09-28", url=entry.url, body_html="<p>본문</p>",
        license_type="KOGL-1",
    )


def _record() -> NoticeRecord:
    return NoticeRecord(
        source_board="1042",
        category="dong", dong_group="wolgye1", is_pinned=False,
        post_sn="001234", title="제목", department="월계1동 행정민원팀",
        registered_on=date(2026, 9, 28),
        url=_entry("001234", "월계1동", False).url,
        body_html='<img src="/file">', license_type="KOGL-1",
    )


def test_default_selection_ignores_other_dong_pinned_notice() -> None:
    entries = (_entry("100", "하계2동", True), _entry("200", "월계1동", True),
               _entry("001234", "월계1동", False))
    with (
        patch("pipeline.collect_wolgye1.fetch_list_page", return_value="list"),
        patch("pipeline.collect_wolgye1.parse_list_page", return_value=entries),
        patch("pipeline.collect_wolgye1.fetch_detail_page", return_value="detail") as detail,
        patch("pipeline.collect_wolgye1.parse_detail_page",
              side_effect=lambda entry, _: _notice(entry)),
        patch("pipeline.collect_wolgye1.extract_dong_files", return_value=[]),
    ):
        record, files = collect_one_wolgye1(WolgyeSettings(1, 2))
    assert record.post_sn == "001234"
    assert files == []
    assert detail.call_args.args[0].post_sn == "001234"


def test_post_sn_must_be_on_first_page_before_detail_request() -> None:
    with (
        patch("pipeline.collect_wolgye1.fetch_list_page", return_value="list"),
        patch("pipeline.collect_wolgye1.parse_list_page",
              return_value=(_entry("001234", "월계1동", False),)),
        patch("pipeline.collect_wolgye1.fetch_detail_page") as detail,
    ):
        with pytest.raises(WolgyeSourceError, match="첫 페이지"):
            collect_one_wolgye1(WolgyeSettings(1, 2), post_sn="999999")
    detail.assert_not_called()


def test_manual_retry_can_verify_a_post_on_later_page() -> None:
    entry = _entry("001234", "월계1동", False)
    with (
        patch("pipeline.collect_wolgye1.fetch_list_page", return_value="list") as fetch,
        patch("pipeline.collect_wolgye1.parse_list_page", return_value=(entry,)),
        patch("pipeline.collect_wolgye1.fetch_detail_page", return_value="detail"),
        patch("pipeline.collect_wolgye1.parse_detail_page", return_value=_notice(entry)),
        patch("pipeline.collect_wolgye1.extract_dong_files", return_value=[]),
    ):
        record, _files = collect_one_wolgye1(
            WolgyeSettings(1, 2), post_sn="001234", page=7,
        )
    assert record.post_sn == "001234"
    assert fetch.call_args.kwargs == {"page": 7}


def test_manual_later_page_requires_post_sn(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["collect-one", "--source", "wolgye1", "--page", "7"]) == 2
    assert "--post-sn" in capsys.readouterr().err


def test_cli_saves_complete_notice_with_both_file_roles(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://pipeline@localhost/test")
    monkeypatch.delenv("NOWON_NOTICE_API_KEY", raising=False)
    record = _record()
    files = [
        FileRecord("dong", "1042", "001234", "inline_image", "1", "image-a", None,
                   "https://www.nowon.kr/file?x=1"),
        FileRecord("dong", "1042", "001234", "attachment", "2", "file-b", "안내.pdf",
                   "https://www.nowon.kr/file?x=2"),
    ]
    conn = MagicMock()
    conn.__enter__.return_value = conn
    with (
        patch("pipeline.cli.collect_one_wolgye1", return_value=(record, files)) as collect,
        patch("pipeline.cli.psycopg.connect", return_value=conn) as connect,
        patch("pipeline.cli.save_notice_with_files", return_value=42) as save,
    ):
        assert main(["collect-one", "--source", "wolgye1", "--post-sn", "001234"]) == 0
    collect.assert_called_once()
    assert collect.call_args.kwargs == {"post_sn": "001234", "page": 1}
    connect.assert_called_once()
    assert save.call_args.args == (conn, record, files)
    summary = json.loads(capsys.readouterr().out)
    assert (summary["notice_id"], summary["attachment_count"],
            summary["inline_image_count"]) == (42, 1, 1)


def test_cli_does_not_connect_to_db_when_detail_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://pipeline@localhost/test")
    with (
        patch("pipeline.cli.collect_one_wolgye1",
              side_effect=WolgyeSourceError("본문 영역이 없습니다.")),
        patch("pipeline.cli.psycopg.connect") as connect,
    ):
        assert main(["collect-one", "--source", "wolgye1"]) == 1
    connect.assert_not_called()
    assert "수집 실패" in capsys.readouterr().err


def test_check_config_for_wolgye_does_not_require_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("NOWON_NOTICE_API_KEY", raising=False)
    assert main(["check-config", "--source", "wolgye1"]) == 0


def test_nowon_rejects_wolgye_only_post_sn_option(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["collect-one", "--source", "nowon", "--post-sn", "123"]) == 2
    assert "--post-sn" in capsys.readouterr().err


def _page(number: int, total: int, *entries: BoardEntry) -> BoardPage:
    return BoardPage(
        number, total, (total + 1) // 2, 2, entries,
        tuple(entry.post_sn for entry in entries if not entry.is_pinned),
    )


def test_multi_page_listing_merges_pinned_and_rechecks_first_page() -> None:
    pinned = _entry("001", "월계1동", True)
    first = _page(1, 4, pinned, _entry("002", "월계1동", False),
                  _entry("003", "월계1동", False))
    second = _page(2, 4, _entry("001", "월계1동", False),
                   _entry("004", "월계1동", False))
    with patch("pipeline.collect_wolgye1._fetch_list_with_retry",
               side_effect=[first, second, first]) as fetch:
        result = collect_wolgye_listing(WolgyeSettings(1, 2))
    assert [call.args[1] for call in fetch.call_args_list] == [1, 2, 1]
    assert (result.total_count, len(result.entries), result.duplicate_count) == (4, 4, 1)
    assert result.entries[0].is_pinned is True
    assert result.complete is True


def test_middle_page_failure_keeps_later_page_but_is_incomplete() -> None:
    first = _page(1, 6, _entry("001", "월계1동", False),
                  _entry("002", "월계1동", False))
    third = _page(3, 6, _entry("005", "월계1동", False),
                  _entry("006", "월계1동", False))
    with patch("pipeline.collect_wolgye1._fetch_list_with_retry",
               side_effect=[first, WolgyeSourceError("timeout"), third, first]):
        result = collect_wolgye_listing(WolgyeSettings(1, 2))
    assert result.failed_pages == (2,)
    assert [entry.post_sn for entry in result.entries] == ["001", "002", "005", "006"]
    assert result.complete is False


def test_duplicate_page_does_not_claim_complete() -> None:
    first = _page(1, 4, _entry("001", "월계1동", False),
                  _entry("002", "월계1동", False))
    second = _page(2, 4, _entry("001", "월계1동", False),
                   _entry("002", "월계1동", False))
    with patch("pipeline.collect_wolgye1._fetch_list_with_retry",
               side_effect=[first, second, first]):
        result = collect_wolgye_listing(WolgyeSettings(1, 2))
    assert result.duplicate_count == 2
    assert len(result.entries) == 2
    assert result.complete is False


def test_moving_first_page_does_not_claim_complete() -> None:
    first = _page(1, 2, _entry("001", "월계1동", False),
                  _entry("002", "월계1동", False))
    changed = _page(1, 2, _entry("009", "월계1동", False),
                    _entry("001", "월계1동", False))
    with patch("pipeline.collect_wolgye1._fetch_list_with_retry",
               side_effect=[first, changed]):
        result = collect_wolgye_listing(WolgyeSettings(1, 2))
    assert result.complete is False


def test_limit_stops_before_next_page_and_marks_partial() -> None:
    first = _page(1, 4, _entry("001", "월계1동", False),
                  _entry("002", "월계1동", False))
    with patch("pipeline.collect_wolgye1._fetch_list_with_retry",
               return_value=first) as fetch:
        result = collect_wolgye_listing(WolgyeSettings(1, 2), limit=1)
    fetch.assert_called_once()
    assert result.limited is True
    assert result.complete is False


def test_detail_failure_does_not_stop_later_save() -> None:
    entries = tuple(_entry(str(index), "월계1동", False) for index in (1, 2, 3))
    listing = WolgyeListing(3, entries, (), 0, (), False, True)
    conn = MagicMock()
    conn.closed = False

    def detail(entry: BoardEntry, _settings: WolgyeSettings) -> str:
        if entry.post_sn == "2":
            raise WolgyeSourceError("missing")
        return "detail"

    with (
        patch("pipeline.collect_wolgye1.collect_wolgye_listing", return_value=listing),
        patch("pipeline.collect_wolgye1._fetch_detail_with_retry", side_effect=detail),
        patch("pipeline.collect_wolgye1.parse_detail_page",
              side_effect=lambda entry, _: _notice(entry)),
        patch("pipeline.collect_wolgye1.extract_dong_files", return_value=[]),
        patch("pipeline.collect_wolgye1.psycopg.connect", return_value=conn),
        patch("pipeline.collect_wolgye1.save_notice_with_files") as save,
    ):
        result = collect_and_save_wolgye1(
            WolgyeSettings(1, 2), DatabaseSettings("postgresql://test@localhost/db"),
        )
    assert result.saved_count == 2
    assert result.complete is False
    assert [(item.post_sn, item.stage) for item in result.failures] == [("2", "detail")]
    assert [call.args[1].post_sn for call in save.call_args_list] == ["1", "3"]
    conn.close.assert_called_once()


def test_storage_failure_does_not_stop_later_notice() -> None:
    entries = tuple(_entry(str(index), "월계1동", False) for index in (1, 2, 3))
    listing = WolgyeListing(3, entries, (), 0, (), False, True)
    conn = MagicMock()
    conn.closed = False
    with (
        patch("pipeline.collect_wolgye1.collect_wolgye_listing", return_value=listing),
        patch("pipeline.collect_wolgye1._fetch_detail_with_retry", return_value="detail"),
        patch("pipeline.collect_wolgye1.parse_detail_page",
              side_effect=lambda entry, _: _notice(entry)),
        patch("pipeline.collect_wolgye1.extract_dong_files", return_value=[]),
        patch("pipeline.collect_wolgye1.psycopg.connect", return_value=conn),
        patch("pipeline.collect_wolgye1.save_notice_with_files",
              side_effect=[1, psycopg.IntegrityError("injected"), 3]) as save,
    ):
        result = collect_and_save_wolgye1(
            WolgyeSettings(1, 2), DatabaseSettings("postgresql://test@localhost/db"),
        )
    assert result.saved_count == 2
    assert result.complete is False
    assert [(item.post_sn, item.stage, item.reason_code) for item in result.failures] == [
        ("2", "storage", "db_save_failed"),
    ]
    assert [call.args[1].post_sn for call in save.call_args_list] == ["1", "2", "3"]


def test_collect_cli_reports_partial_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test@localhost/db")
    listing = WolgyeListing(4, (_entry("001", "월계1동", False),), (), 0, (), True, False)
    with (
        patch("pipeline.collect_wolgye1.collect_wolgye_listing", return_value=listing),
        patch("pipeline.collect_wolgye1._fetch_detail_with_retry", return_value="detail"),
        patch("pipeline.collect_wolgye1.parse_detail_page",
              side_effect=lambda entry, _: _notice(entry)),
        patch("pipeline.collect_wolgye1.extract_dong_files", return_value=[]),
        patch("pipeline.collect_wolgye1.psycopg.connect") as connect,
        patch("pipeline.collect_wolgye1.save_notice_with_files"),
    ):
        connect.return_value.closed = False
        assert main(["collect", "--source", "wolgye1", "--limit", "1"]) == 1
    summary = json.loads(capsys.readouterr().out)
    assert (summary["total_count"], summary["saved_count"], summary["limited"],
            summary["complete"]) == (4, 1, True, False)


def _scheduled_page(number: int, total: int, *entries: BoardEntry) -> BoardPage:
    return BoardPage(
        number, total, (total + 4) // 5, 5, entries,
        tuple(entry.post_sn for entry in entries if not entry.is_pinned),
    )


def _scheduled_connection(known: set[str], *, has_history: bool) -> MagicMock:
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


def test_new_mode_initial_run_only_selects_five_regular_posts() -> None:
    pinned = _entry("pin", "하계2동", True)
    regular = tuple(_entry(str(index), "월계1동", False) for index in range(1, 6))
    first = _scheduled_page(1, 10, pinned, *regular)
    conn = _scheduled_connection(set(), has_history=False)
    with (
        patch("pipeline.collect_wolgye1.psycopg.connect", return_value=conn),
        patch("pipeline.collect_wolgye1._fetch_list_with_retry", return_value=first) as fetch,
        patch("pipeline.collect_wolgye1._save_entries", return_value=(5, ())) as save,
    ):
        result = collect_and_save_wolgye1_scheduled(
            WolgyeSettings(1, 2), DatabaseSettings("postgresql://test@localhost/db"),
            mode="new",
        )
    assert result.initial_baseline is True
    assert result.complete is True
    assert result.selected_count == 5
    assert [entry.post_sn for entry in save.call_args.args[1]] == ["1", "2", "3", "4", "5"]
    fetch.assert_called_once()


def test_new_mode_expands_until_first_known_regular_post() -> None:
    first = _scheduled_page(
        1, 10, *(_entry(str(index), "월계1동", False) for index in range(1, 6)),
    )
    second = _scheduled_page(
        2, 10, *(_entry(str(index), "월계1동", False) for index in range(6, 11)),
    )
    conn = _scheduled_connection({"8"}, has_history=True)
    with (
        patch("pipeline.collect_wolgye1.psycopg.connect", return_value=conn),
        patch("pipeline.collect_wolgye1._fetch_list_with_retry",
              side_effect=[first, second, first]) as fetch,
        patch("pipeline.collect_wolgye1._save_entries", return_value=(7, ())) as save,
        patch("pipeline.collect_wolgye1.sleep"),
    ):
        result = collect_and_save_wolgye1_scheduled(
            WolgyeSettings(1, 2), DatabaseSettings("postgresql://test@localhost/db"),
            mode="new",
        )
    assert result.initial_baseline is False
    assert result.pages_read == 2
    assert result.complete is True
    assert [entry.post_sn for entry in save.call_args.args[1]] == [
        "1", "2", "3", "4", "5", "6", "7",
    ]
    assert [call.args[1] for call in fetch.call_args_list] == [1, 2, 1]


def test_new_mode_skips_known_top_five_without_detail_request() -> None:
    regular = tuple(_entry(str(index), "월계1동", False) for index in range(1, 6))
    first = _scheduled_page(1, 10, *regular)
    conn = _scheduled_connection({"2", "5"}, has_history=True)
    with (
        patch("pipeline.collect_wolgye1.psycopg.connect", return_value=conn),
        patch("pipeline.collect_wolgye1._fetch_list_with_retry", return_value=first) as fetch,
        patch("pipeline.collect_wolgye1._save_entries", return_value=(3, ())) as save,
    ):
        result = collect_and_save_wolgye1_scheduled(
            WolgyeSettings(1, 2), DatabaseSettings("postgresql://test@localhost/db"),
            mode="new",
        )
    assert result.selected_count == 3
    assert [entry.post_sn for entry in save.call_args.args[1]] == ["1", "3", "4"]
    fetch.assert_called_once()


def test_refresh_mode_selects_all_pinned_plus_five_regular() -> None:
    pinned = (_entry("900", "하계2동", True), _entry("901", "월계1동", True))
    regular = tuple(_entry(str(index), "월계1동", False) for index in range(1, 6))
    first = _scheduled_page(1, 10, *pinned, *regular)
    conn = _scheduled_connection(set(), has_history=True)
    with (
        patch("pipeline.collect_wolgye1.psycopg.connect", return_value=conn),
        patch("pipeline.collect_wolgye1._fetch_list_with_retry", return_value=first),
        patch("pipeline.collect_wolgye1._save_entries", return_value=(7, ())) as save,
    ):
        result = collect_and_save_wolgye1_scheduled(
            WolgyeSettings(1, 2), DatabaseSettings("postgresql://test@localhost/db"),
            mode="refresh",
        )
    assert result.complete is True
    assert [entry.post_sn for entry in save.call_args.args[1]] == [
        "900", "901", "1", "2", "3", "4", "5",
    ]


def test_rate_limit_on_next_list_page_stops_without_detail_requests() -> None:
    first = _scheduled_page(
        1, 10, *(_entry(str(index), "월계1동", False) for index in range(1, 6)),
    )
    conn = _scheduled_connection(set(), has_history=True)
    with (
        patch("pipeline.collect_wolgye1.psycopg.connect", return_value=conn),
        patch("pipeline.collect_wolgye1._fetch_list_with_retry",
              side_effect=[first, WolgyeSourceError("HTTP 429", rate_limited=True)]),
        patch("pipeline.collect_wolgye1._save_entries") as save,
        patch("pipeline.collect_wolgye1.sleep"),
    ):
        with pytest.raises(WolgyeSourceError, match="429"):
            collect_and_save_wolgye1_scheduled(
                WolgyeSettings(1, 2), DatabaseSettings("postgresql://test@localhost/db"),
                mode="new",
            )
    save.assert_not_called()
    conn.close.assert_called_once()


def test_scheduled_duplicate_page_cannot_claim_complete_listing() -> None:
    first = _scheduled_page(
        1, 10, *(_entry(str(index), "월계1동", False) for index in range(1, 6)),
    )
    duplicate = _scheduled_page(2, 10, *first.entries)
    conn = _scheduled_connection(set(), has_history=True)
    with (
        patch("pipeline.collect_wolgye1.psycopg.connect", return_value=conn),
        patch("pipeline.collect_wolgye1._fetch_list_with_retry",
              side_effect=[first, duplicate, first]),
        patch("pipeline.collect_wolgye1._save_entries", return_value=(5, ())),
        patch("pipeline.collect_wolgye1.sleep"),
    ):
        result = collect_and_save_wolgye1_scheduled(
            WolgyeSettings(1, 2), DatabaseSettings("postgresql://test@localhost/db"),
            mode="new",
        )
    assert result.listing_complete is False
    assert result.complete is False


def test_rate_limit_on_detail_stops_remaining_detail_requests() -> None:
    regular = tuple(_entry(str(index), "월계1동", False) for index in range(1, 6))
    first = _scheduled_page(1, 5, *regular)
    conn = _scheduled_connection(set(), has_history=True)
    with (
        patch("pipeline.collect_wolgye1.psycopg.connect", return_value=conn),
        patch("pipeline.collect_wolgye1._fetch_list_with_retry", return_value=first),
        patch("pipeline.collect_wolgye1._fetch_detail_with_retry",
              side_effect=WolgyeSourceError("HTTP 429", rate_limited=True)) as detail,
        patch("pipeline.collect_wolgye1.save_notice_with_files") as save,
    ):
        result = collect_and_save_wolgye1_scheduled(
            WolgyeSettings(1, 2), DatabaseSettings("postgresql://test@localhost/db"),
            mode="refresh",
        )
    detail.assert_called_once()
    save.assert_not_called()
    assert result.complete is False
    assert [failure.reason_code for failure in result.failures] == [
        "rate_limited", *("rate_limited_not_attempted" for _ in range(4)),
    ]


def test_scheduled_cli_reports_scope_not_whole_board(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test@localhost/db")
    result = ScheduledWolgyeResult("new", 2298, 0, 0, 1, False, True, (), ())
    with patch("pipeline.cli.collect_and_save_wolgye1_scheduled", return_value=result) as run:
        assert main(["collect", "--source", "wolgye1", "--mode", "new"]) == 0
    run.assert_called_once()
    summary = json.loads(capsys.readouterr().out)
    assert (summary["total_count"], summary["selected_count"], summary["complete"]) == (
        2298, 0, True,
    )
    assert main(["collect", "--source", "wolgye1", "--mode", "refresh",
                 "--limit", "5"]) == 2


def test_real_db_multi_save_repeat_and_failed_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.getenv("PIPELINE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PIPELINE_TEST_DATABASE_URL is required for Wolgye DB integration")
    post_sns = [f"{uuid4().int % 10**17:017d}" for _ in range(3)]
    entries = tuple(_entry(post_sn, "월계1동", False) for post_sn in post_sns)
    listing = WolgyeListing(3, entries, (), 0, (), False, True)
    fail_middle = True

    def detail(entry: BoardEntry, _settings: WolgyeSettings) -> str:
        if fail_middle and entry.post_sn == post_sns[1]:
            raise WolgyeSourceError("missing")
        return "detail"

    def files(notice: RawNotice, _html: str) -> list[FileRecord]:
        return [FileRecord(
            "dong", "1042", notice.post_sn, "attachment", "42", notice.post_sn, "file.pdf",
            f"https://www.nowon.kr/file?q_fileSn=42&q_fileId={notice.post_sn}",
        )]

    monkeypatch.setattr(
        "pipeline.collect_wolgye1.collect_wolgye_listing", lambda *_a, **_kw: listing,
    )
    monkeypatch.setattr("pipeline.collect_wolgye1._fetch_detail_with_retry", detail)
    monkeypatch.setattr("pipeline.collect_wolgye1.parse_detail_page",
                        lambda entry, _html: _notice(entry))
    monkeypatch.setattr("pipeline.collect_wolgye1.extract_dong_files", files)
    settings = WolgyeSettings(1, 2)
    database = DatabaseSettings(database_url)
    try:
        first = collect_and_save_wolgye1(settings, database)
        assert (first.saved_count, first.complete) == (2, False)
        fail_middle = False
        second = collect_and_save_wolgye1(settings, database)
        third = collect_and_save_wolgye1(settings, database)
        assert second.complete is third.complete is True
        with psycopg.connect(database_url) as conn:
            rows = conn.execute(
                "select post_sn, is_modified from notices "
                "where category='dong' and post_sn = any(%s) order by post_sn", (post_sns,),
            ).fetchall()
            assert rows == [(post_sn, False) for post_sn in sorted(post_sns)]
            files_count = conn.execute(
                "select count(*) from notice_files f join notices n on n.id=f.notice_id "
                "where n.category='dong' and n.post_sn = any(%s)", (post_sns,),
            ).fetchone()
            assert files_count == (3,)
    finally:
        with psycopg.connect(database_url) as conn:
            conn.execute(
                "delete from notices where category='dong' and post_sn = any(%s)",
                (post_sns,),
            )


def test_real_db_scheduled_new_then_refresh_detects_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.getenv("PIPELINE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PIPELINE_TEST_DATABASE_URL is required for Wolgye DB integration")
    post_sns = [f"{uuid4().int % 10**17:017d}" for _ in range(5)]
    entries = tuple(_entry(post_sn, "월계1동", False) for post_sn in post_sns)
    first = _scheduled_page(1, 5, *entries)
    changed = False

    def detail(entry: BoardEntry, _html: str) -> RawNotice:
        notice = _notice(entry)
        if changed and entry.post_sn == post_sns[0]:
            return replace(notice, title="변경된 제목", body_html="<p>수정된 본문</p>")
        return notice

    monkeypatch.setattr("pipeline.collect_wolgye1._fetch_list_with_retry",
                        lambda *_args: first)
    detail_fetch = MagicMock(return_value="detail")
    monkeypatch.setattr("pipeline.collect_wolgye1._fetch_detail_with_retry", detail_fetch)
    monkeypatch.setattr("pipeline.collect_wolgye1.parse_detail_page", detail)
    monkeypatch.setattr("pipeline.collect_wolgye1.extract_dong_files",
                        lambda *_args: [])
    monkeypatch.setattr("pipeline.collect_wolgye1.sleep", lambda *_args: None)
    settings = WolgyeSettings(1, 2)
    database = DatabaseSettings(database_url)
    try:
        first_run = collect_and_save_wolgye1_scheduled(settings, database, mode="new")
        assert (first_run.selected_count, first_run.saved_count, first_run.complete) == (
            5, 5, True,
        )
        assert detail_fetch.call_count == 5
        repeat = collect_and_save_wolgye1_scheduled(settings, database, mode="new")
        assert (repeat.selected_count, repeat.saved_count, repeat.complete) == (0, 0, True)
        assert detail_fetch.call_count == 5
        changed = True
        refresh = collect_and_save_wolgye1_scheduled(settings, database, mode="refresh")
        assert (refresh.selected_count, refresh.saved_count, refresh.complete) == (5, 5, True)
        assert detail_fetch.call_count == 10
        with psycopg.connect(database_url) as conn:
            rows = conn.execute(
                "SELECT post_sn, title, is_modified FROM notices "
                "WHERE category='dong' AND post_sn = ANY(%s)", (post_sns,),
            ).fetchall()
            assert sorted(rows) == sorted(
                (post_sn, "변경된 제목" if post_sn == post_sns[0] else "제목",
                 post_sn == post_sns[0]) for post_sn in post_sns
            )
    finally:
        with psycopg.connect(database_url) as conn:
            conn.execute(
                "DELETE FROM notices WHERE category='dong' AND post_sn = ANY(%s)",
                (post_sns,),
            )
