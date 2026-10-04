"""Field-by-field scheduled selection and safe partial collection."""

import json
import os
from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree as ET

import httpx
import psycopg
import pytest

from pipeline.attachments.seoul_html import SeoulAttachmentError
from pipeline.cli import main
from pipeline.collect_seoul import prepare_notice
from pipeline.collect_seoul_scheduled import (
    BoardResult,
    ScheduledSeoulResult,
    SeoulFailure,
    _api_page,
    _board,
    collect_scheduled,
)
from pipeline.config import DatabaseSettings, SeoulNewsSettings
from pipeline.models import FileRecord, NoticeRecord, RawSeoulNotice
from pipeline.sources.seoul_api import (
    BOARD_SLUGS,
    SeoulApiPage,
    SeoulSourceError,
    collect_page,
    parse_notice,
)
from pipeline.transform.seoul import SeoulTransformError

FIXTURE = Path(__file__).parent / "fixtures/seoul_one.xml"
SETTINGS = SeoulNewsSettings("test-key", 5, 20)
DATABASE = DatabaseSettings("postgresql://not-used/test")


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pipeline.collect_seoul_scheduled.sleep", lambda _: None)


def notice(board: str, index: int) -> RawSeoulNotice:
    return replace(parse_notice(FIXTURE.read_bytes()), source_board=board, post_sn=f"{index:06d}")


def api_page(
    settings: SeoulNewsSettings, *, source_board: str, start_index: int, end_index: int
) -> SeoulApiPage:
    return SeoulApiPage(
        start_index,
        end_index,
        100,
        tuple(notice(source_board, i) for i in range(start_index, min(100, end_index) + 1)),
    )


def prepared(n: RawSeoulNotice) -> tuple[NoticeRecord, tuple[FileRecord, ...]]:
    record = NoticeRecord(
        "seoul",
        n.source_board,
        None,
        False,
        n.post_sn,
        n.title,
        n.department,
        date(2026, 10, 2),
        f"https://news.seoul.go.kr/{BOARD_SLUGS[n.source_board]}/archives/{n.post_sn}",
        n.body_html,
        "KOGL-4",
    )
    file = FileRecord(
        "seoul",
        n.source_board,
        n.post_sn,
        "attachment",
        None,
        None,
        "notice.pdf",
        "https://news.seoul.go.kr/" + BOARD_SLUGS[n.source_board] + "/files/notice.pdf",
    )
    return record, (file,)


def connection(known: dict[str, set[str]], history: set[str]) -> MagicMock:
    conn = MagicMock()
    conn.__enter__.return_value = conn

    def execute(sql: str, params: tuple) -> MagicMock:
        result = MagicMock()
        board = params[0]
        if "exists(" in sql:
            result.fetchone.return_value = (board in history,)
        else:
            assert "category='seoul'" in sql and "source_board=%s" in sql and "is_visible" in sql
            result.fetchall.return_value = [
                (sn,) for sn in params[1] if sn in known.get(board, set())
            ]
        return result

    conn.execute.side_effect = execute
    return conn


def run(
    known: dict[str, set[str]],
    history: set[str],
    *,
    mode: str = "new",
    board: str | None = "25",
    fetch: Callable = api_page,
    prepare: Callable = prepared,
    save: Callable | None = None,
) -> tuple[ScheduledSeoulResult, MagicMock, MagicMock, MagicMock]:
    conn = connection(known, history)
    with (
        patch("pipeline.collect_seoul_scheduled.psycopg.connect", return_value=conn),
        patch("pipeline.collect_seoul_scheduled.collect_page", side_effect=fetch) as fetched,
        patch(
            "pipeline.collect_seoul_scheduled.prepare_notice", side_effect=prepare
        ) as preparation,
        patch(
            "pipeline.collect_seoul_scheduled.save_notice_with_files", side_effect=save
        ) as stored,
    ):
        result = collect_scheduled(SETTINGS, DATABASE, mode=mode, source_board=board)
    return result, fetched, preparation, stored


@pytest.mark.parametrize("mode", ["new", "refresh"])
def test_each_empty_board_selects_25(mode: str) -> None:
    result, fetch, prepare, save = run({}, set(), mode=mode, board=None)
    assert len(result.boards) == 8 and result.complete
    assert all(
        b.initial_baseline and b.selected_count == b.saved_count == 25 for b in result.boards
    )
    assert prepare.call_count == save.call_count == 200
    assert all(
        call.kwargs["start_index"] == 1 and call.kwargs["end_index"] == 25
        for call in fetch.call_args_list
    )
    assert {c.args[1].source_board for c in save.call_args_list} == set(BOARD_SLUGS)


@pytest.mark.parametrize("mode", ["new", "refresh"])
def test_scheduled_preparation_uses_api_html_without_original_or_file_requests(mode: str) -> None:
    with patch("httpx.Client", side_effect=AssertionError("No page/file requests")):
        result, _, preparation, save = run({}, set(), mode=mode, prepare=prepare_notice)
    assert result.complete and preparation.call_count == save.call_count == 25
    for call in save.call_args_list:
        record, files = call.args[1:]
        assert record.body_html == notice("25", 1).body_html
        assert record.license_type is None
        assert len(files) == 2


def test_history_is_per_field_not_entire_seoul_category() -> None:
    result, _, _, save = run({"24": {f"{i:06d}" for i in range(1, 11)}}, {"24"}, board=None)
    economy = next(b for b in result.boards if b.source_board == "24")
    assert economy.initial_baseline is False and economy.selected_count == 0
    assert all(
        b.initial_baseline and b.selected_count == 25
        for b in result.boards
        if b.source_board != "24"
    )
    assert save.call_count == 175


@pytest.mark.parametrize("mode,expected", [("new", 0), ("refresh", 10)])
def test_known_latest_ten_new_skips_and_refresh_reads(mode: str, expected: int) -> None:
    result, fetch, prepare, _ = run({"25": {f"{i:06d}" for i in range(1, 11)}}, {"25"}, mode=mode)
    b = result.boards[0]
    assert b.selected_count == b.saved_count == expected and b.complete
    assert prepare.call_count == expected and fetch.call_count == 1
    assert fetch.call_args.kwargs["end_index"] == 10


def test_new_checks_all_ten_even_after_meeting_one_known_notice() -> None:
    result, _, prepare, _ = run({"25": {"000002", "000009"}}, {"25"})
    assert result.boards[0].saved_count == 8 and result.complete
    assert [c.args[0].post_sn for c in prepare.call_args_list] == [
        f"{i:06d}" for i in (1, 3, 4, 5, 6, 7, 8, 10)
    ]


@pytest.mark.parametrize("mode", ["new", "refresh"])
def test_ten_all_new_expand_to_known_boundary_with_overlap(mode: str) -> None:
    result, fetch, prepare, _ = run({"25": {"000013"}}, {"25"}, mode=mode)
    assert result.boards[0].saved_count == 12 and result.complete
    assert [c.args[0].post_sn for c in prepare.call_args_list] == [f"{i:06d}" for i in range(1, 13)]
    assert [(c.kwargs["start_index"], c.kwargs["end_index"]) for c in fetch.call_args_list] == [
        (1, 10),
        (9, 18),
        (1, 1),
    ]


def test_page_timeout_keeps_good_notices_but_not_complete() -> None:
    def fetch(settings: SeoulNewsSettings, **kwargs) -> SeoulApiPage:
        if kwargs["start_index"] > 1:
            raise SeoulSourceError("timeout", retryable=True)
        return api_page(settings, **kwargs)

    result, fetch_mock, _, _ = run({}, {"25"}, fetch=fetch)
    b = result.boards[0]
    assert b.saved_count == 10 and not b.complete and not b.listing_complete
    assert b.failures[0].reason_code == "api_page_failed" and fetch_mock.call_count == 4


@pytest.mark.parametrize(
    "change,reason",
    [
        ("count", "total_count_changed"),
        ("head", "listing_changed"),
        ("repeat", "no_listing_progress"),
    ],
)
def test_unstable_or_repeated_pages_cannot_be_complete(change: str, reason: str) -> None:
    def fetch(settings: SeoulNewsSettings, **kwargs) -> SeoulApiPage:
        p = api_page(settings, **kwargs)
        if kwargs["start_index"] > 1:
            if change == "count":
                return replace(p, total_count=101)
            if change == "repeat":
                return replace(p, notices=tuple(notice("25", i) for i in range(1, 11)))
        if kwargs["end_index"] == 1 and change == "head":
            return replace(p, notices=(notice("25", 99),))
        return p

    result, _, _, _ = run({"25": {"000013"}}, {"25"}, fetch=fetch)
    assert not result.complete
    assert reason in {f.reason_code for f in result.boards[0].failures}


def test_conflicting_overlap_is_not_saved() -> None:
    def fetch(settings: SeoulNewsSettings, **kwargs) -> SeoulApiPage:
        p = api_page(settings, **kwargs)
        if kwargs["start_index"] > 1:
            return replace(
                p,
                notices=tuple(
                    replace(n, title="changed") if n.post_sn == "000009" else n for n in p.notices
                ),
            )
        return p

    result, _, prepare, _ = run({"25": {"000013"}}, {"25"}, fetch=fetch)
    assert not result.complete and result.boards[0].saved_count == 11
    assert "000009" not in {c.args[0].post_sn for c in prepare.call_args_list}
    assert result.boards[0].failures[0].reason_code == "listing_conflict"


def test_one_page_failure_does_not_discard_other_fields() -> None:
    def fetch(settings: SeoulNewsSettings, **kwargs) -> SeoulApiPage:
        if kwargs["source_board"] == "24":
            raise SeoulSourceError("bad response")
        return api_page(settings, **kwargs)

    result, _, _, _ = run({}, set(), board=None, fetch=fetch)
    assert not result.complete and sum(b.saved_count for b in result.boards) == 175
    assert next(b for b in result.boards if b.source_board == "24").saved_count == 0


@pytest.mark.parametrize("stage", ["attachments", "transform", "storage"])
def test_one_notice_failure_continues_without_hiding(stage: str) -> None:
    def prepare(n: RawSeoulNotice) -> tuple:
        if n.post_sn == "000002" and stage in ("attachments", "transform"):
            error = SeoulAttachmentError if stage == "attachments" else SeoulTransformError
            raise error("invalid API data")
        return prepared(n)

    def save(conn: psycopg.Connection, record: NoticeRecord, files: tuple[FileRecord, ...]) -> None:
        if record.post_sn == "000002" and stage == "storage":
            raise psycopg.errors.CheckViolation("invalid file")

    result, _, _, _ = run({}, set(), prepare=prepare, save=save)
    b = result.boards[0]
    assert b.selected_count == 25 and b.saved_count == 24 and not b.complete
    assert b.failures[0].stage == stage


def test_api_rate_limit_stops_remaining_fields() -> None:
    def fetch(settings: SeoulNewsSettings, **kwargs) -> SeoulApiPage:
        raise SeoulSourceError("limited", retryable=True, rate_limited=True)

    result, fetch_mock, prepare_mock, save = run({}, set(), board=None, fetch=fetch)
    assert not result.complete and sum(b.saved_count for b in result.boards) == 0
    assert fetch_mock.call_count == 1
    prepare_mock.assert_not_called()
    save.assert_not_called()
    assert all(b.failures[0].reason_code == "rate_limited_not_attempted" for b in result.boards[1:])


def test_empty_board_completes_without_prepare_or_storage() -> None:
    result, _, prepare, save = run({}, set(), fetch=lambda s, **k: SeoulApiPage(1, 25, 0, ()))
    assert result.complete and result.boards[0].saved_count == 0
    prepare.assert_not_called()
    save.assert_not_called()


def test_small_board_initial_scope_does_not_require_25_available_rows() -> None:
    def fetch(settings: SeoulNewsSettings, **kwargs) -> SeoulApiPage:
        return replace(
            api_page(settings, **kwargs),
            total_count=3,
            notices=tuple(notice("25", i) for i in range(1, 4)),
        )

    result, _, _, _ = run({}, set(), fetch=fetch)
    assert result.complete and result.boards[0].saved_count == 3


def test_sample_mode_is_rejected_before_any_db_or_api_call() -> None:
    with patch("pipeline.collect_seoul_scheduled.psycopg.connect") as connect:
        with pytest.raises(ValueError, match="발급"):
            collect_scheduled(SeoulNewsSettings("sample", 5, 20), DATABASE, mode="new")
        connect.assert_not_called()


def test_api_range_and_empty_response_contract() -> None:
    template = ET.fromstring(FIXTURE.read_bytes())
    first_row = template.find("row")
    template.remove(first_row)
    template.find("list_total_count").text = "30"
    for i in range(1, 26):
        row = deepcopy(first_row)
        row.find("POST_ID").text = f"{i:06d}"
        template.append(row)
    payload = ET.tostring(template)

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/1/25/25/")
        return httpx.Response(200, content=payload)

    page = collect_page(
        SETTINGS,
        source_board="25",
        start_index=1,
        end_index=25,
        transport=httpx.MockTransport(respond),
    )
    assert (
        len(page.notices) == 25 and page.total_count == 30 and page.notices[0].post_sn == "000001"
    )
    with pytest.raises(SeoulSourceError, match="행 개수"):
        collect_page(
            SETTINGS,
            source_board="25",
            start_index=1,
            end_index=10,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=payload)),
        )
    empty = collect_page(
        SETTINGS,
        source_board="25",
        start_index=1,
        end_index=25,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                content=b"<RESULT><CODE>INFO-200</CODE></RESULT>",
            )
        ),
    )
    assert empty.total_count == 0 and not empty.notices


@pytest.mark.parametrize("start,end", [(0, 25), (25, 1), (1, 1001), (True, 5)])
def test_invalid_api_range_is_not_requested(start: int, end: int) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        pytest.fail("must not request")

    with pytest.raises(SeoulSourceError):
        collect_page(
            SETTINGS,
            source_board="25",
            start_index=start,
            end_index=end,
            transport=httpx.MockTransport(fail),
        )


def test_retryable_api_failure_then_success() -> None:
    success = api_page(SETTINGS, source_board="25", start_index=1, end_index=10)
    with patch(
        "pipeline.collect_seoul_scheduled.collect_page",
        side_effect=[
            SeoulSourceError("temporary", retryable=True),
            success,
        ],
    ) as fetch:
        assert _api_page(SETTINGS, "25", 1, 10) == success
        assert fetch.call_count == 2


def test_cli_modes_json_and_invalid_combinations(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SEOUL_NEWS_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://not-used/test")
    result = ScheduledSeoulResult(
        "new",
        (
            BoardResult(
                "25",
                100,
                25,
                24,
                1,
                True,
                True,
                (SeoulFailure("000002", "attachments", "invalid_file_reference"),),
            ),
        ),
    )
    with patch("pipeline.cli.collect_seoul_scheduled", return_value=result) as collect:
        assert main(["collect", "--source", "seoul", "--mode", "new", "--source-board", "25"]) == 1
    assert collect.call_args.kwargs == {"mode": "new", "source_board": "25"}
    output = json.loads(capsys.readouterr().out)
    assert output["saved_count"] == 24 and output["complete"] is False
    assert output["boards"][0]["initial_baseline"] is True
    for args in (
        ["collect", "--source", "seoul"],
        ["collect", "--source", "seoul", "--mode", "new", "--limit", "2"],
        ["collect", "--source", "nowon", "--source-board", "25"],
    ):
        with patch("pipeline.cli.collect_seoul_scheduled") as collect:
            assert main(args) == 2
            collect.assert_not_called()


@pytest.fixture
def db_conn() -> Iterator[psycopg.Connection]:
    dsn = os.getenv("PIPELINE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("PIPELINE_TEST_DATABASE_URL is required for scheduled Seoul integration")
    conn = psycopg.connect(dsn, autocommit=True)
    if conn.info.host != "127.0.0.1" or not conn.info.dbname.startswith("pipeline_schema_test_"):
        conn.close()
        pytest.fail("Use a disposable loopback pipeline_schema_test_* database")
    try:
        with conn.transaction(force_rollback=True):
            yield conn
    finally:
        conn.close()


def test_real_sql_initial_25_new_skips_refresh_updates_and_parent_keys(
    db_conn: psycopg.Connection,
) -> None:
    with (
        patch("pipeline.collect_seoul_scheduled.collect_page", side_effect=api_page),
        patch(
            "pipeline.collect_seoul_scheduled.prepare_notice",
            side_effect=prepared,
        ),
    ):
        first = _board(db_conn, SETTINGS, "25", "new")
        other = _board(db_conn, SETTINGS, "24", "new")
        next_new = _board(db_conn, SETTINGS, "25", "new")
    assert first.saved_count == other.saved_count == 25
    assert next_new.saved_count == 0 and next_new.complete

    def revised(n: RawSeoulNotice) -> tuple:
        record, files = prepared(n)
        return replace(record, title="17시 수정 제목"), files

    with (
        patch("pipeline.collect_seoul_scheduled.collect_page", side_effect=api_page),
        patch(
            "pipeline.collect_seoul_scheduled.prepare_notice",
            side_effect=revised,
        ),
    ):
        refresh = _board(db_conn, SETTINGS, "25", "refresh")
    assert refresh.saved_count == 10 and refresh.complete
    assert (
        db_conn.execute("select count(*) from notices where category='seoul'").fetchone()[0] == 50
    )
    assert db_conn.execute("select count(*) from notice_files").fetchone()[0] == 50
    assert (
        db_conn.execute(
            "select count(*) from notices where source_board='25' and is_modified"
        ).fetchone()[0]
        == 10
    )
    assert (
        db_conn.execute(
            "select count(*) from notices where source_board='24' and is_modified"
        ).fetchone()[0]
        == 0
    )
    assert db_conn.execute("select count(*) from notices where not is_visible").fetchone()[0] == 0


def test_real_sql_hidden_notice_is_recollected_and_restored(db_conn: psycopg.Connection) -> None:
    with (
        patch("pipeline.collect_seoul_scheduled.collect_page", side_effect=api_page),
        patch(
            "pipeline.collect_seoul_scheduled.prepare_notice",
            side_effect=prepared,
        ),
    ):
        _board(db_conn, SETTINGS, "25", "new")
        db_conn.execute(
            "update notices set is_visible=false where source_board='25' and post_sn='000002'"
        )
        result = _board(db_conn, SETTINGS, "25", "new")
    assert result.saved_count == 1 and result.complete
    assert (
        db_conn.execute(
            "select is_visible from notices where source_board='25' and post_sn='000002'"
        ).fetchone()[0]
        is True
    )
