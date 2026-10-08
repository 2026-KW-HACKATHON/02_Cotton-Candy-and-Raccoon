"""Multi-notice orchestration, with a real DB only when explicitly configured."""

import os
from dataclasses import replace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg
import pytest

from pipeline.attachments.nowon_html import AttachmentError
from pipeline.collect_nowon import collect_and_save_nowon
from pipeline.config import DatabaseSettings, NowonSettings
from pipeline.models import RawNotice
from pipeline.sources.nowon_api import NowonCollection, PageStatus
from pipeline.sources.nowon_page import NowonPageError, NowonPageMissing

EMPTY_ATTACHMENTS = (
    "<table><tr><th>첨부파일</th>"
    "<td>첨부파일이 없습니다</td></tr></table>"
)


def _notice(post_sn: str, *, title: str = "Notice") -> RawNotice:
    return RawNotice(
        source_board="1001",
        category="nowon", dong_group=None, is_pinned=False, post_sn=post_sn,
        title=title, department=None, registered_on="2026-09-26",
        url=("https://www.nowon.kr/www/user/bbs/BD_selectBbs.do"
             f"?q_bbsCode=1001&q_bbscttSn={post_sn}"),
        body_html="<p>Body</p>", license_type="KOGL-4",
    )


def _listing(
    notices: tuple[RawNotice, ...], *, complete: bool = True,
    conflicts: tuple[str, ...] = (),
) -> NowonCollection:
    return NowonCollection(
        notices, (PageStatus(1, len(notices), 1, True),) if notices else (),
        len(notices), complete, (), conflicts,
    )


def _settings() -> tuple[NowonSettings, DatabaseSettings]:
    return NowonSettings("private-key", 2.0, 7.0), DatabaseSettings(
        "postgresql://pipeline@localhost/test",
    )


def _mock_connection() -> MagicMock:
    conn = MagicMock()
    conn.closed = False
    return conn


def test_page_failure_skips_notice_without_claiming_empty_file_list() -> None:
    notices = tuple(_notice(post_sn) for post_sn in ("001", "002", "003"))
    settings, database = _settings()
    conn = _mock_connection()

    def page(notice: RawNotice, _settings: NowonSettings) -> tuple[str, str]:
        if notice.post_sn == "002":
            raise NowonPageError("unavailable")
        return notice.url, EMPTY_ATTACHMENTS

    with (
        patch("pipeline.collect_nowon.collect_all", return_value=_listing(notices)),
        patch("pipeline.collect_nowon.fetch_notice_page", side_effect=page),
        patch("pipeline.collect_nowon.psycopg.connect", return_value=conn) as connect,
        patch("pipeline.collect_nowon.save_notice_with_files", return_value=42) as save,
    ):
        result = collect_and_save_nowon(settings, database)
    assert result.listing_complete is True
    assert result.complete is False
    assert result.saved_count == 2
    assert [(failure.post_sn, failure.stage) for failure in result.failures] == [
        ("002", "page"),
    ]
    assert [call.args[1].post_sn for call in save.call_args_list] == ["001", "003"]
    connect.assert_called_once_with(database.database_url, connect_timeout=5, autocommit=True)
    conn.close.assert_called_once()


def test_missing_page_does_not_save_even_when_api_body_has_file() -> None:
    source = replace(
        _notice("001"),
        body_html='<p>API body</p><img src="/file?q_fileSn=7&amp;q_fileId=image-1">',
    )
    settings, database = _settings()
    with (
        patch("pipeline.collect_nowon.collect_all", return_value=_listing((source,))),
        patch("pipeline.collect_nowon.fetch_notice_page",
              side_effect=NowonPageMissing("데이터가 존재하지 않습니다.")),
        patch("pipeline.collect_nowon.psycopg.connect", return_value=_mock_connection()),
        patch("pipeline.collect_nowon.save_notice_with_files") as full,
    ):
        result = collect_and_save_nowon(settings, database)
    assert result.saved_count == 0
    assert result.complete is False
    assert len(result.failures) == 1
    assert (result.failures[0].stage, result.failures[0].reason_code) == (
        "page_missing", "source_page_missing",
    )
    full.assert_not_called()


def test_real_file_conflict_is_rejected_after_original_page_is_read() -> None:
    source = replace(
        _notice("001"),
        body_html=(
            '<a href="/file?q_fileSn=1&amp;q_fileId=same">first</a>'
            '<a href="/file?q_fileSn=2&amp;q_fileId=same">second</a>'
        ),
    )
    settings, database = _settings()
    with (
        patch("pipeline.collect_nowon.collect_all", return_value=_listing((source,))),
        patch("pipeline.collect_nowon.fetch_notice_page",
              return_value=(source.url, EMPTY_ATTACHMENTS)) as page,
        patch("pipeline.collect_nowon.psycopg.connect", return_value=_mock_connection()),
        patch("pipeline.collect_nowon.save_notice_with_files") as save,
    ):
        result = collect_and_save_nowon(settings, database)
    assert result.saved_count == 0
    assert (result.failures[0].stage, result.failures[0].reason_code) == (
        "attachments", "file_reference_conflict",
    )
    save.assert_not_called()
    page.assert_called_once()


def test_conflicting_id_is_skipped_but_other_notice_is_saved() -> None:
    notices = (_notice("001"), _notice("002"))
    settings, database = _settings()
    with (
        patch("pipeline.collect_nowon.collect_all", return_value=_listing(
            notices, complete=False, conflicts=("001",),
        )),
        patch("pipeline.collect_nowon.fetch_notice_page",
              side_effect=lambda notice, _: (notice.url, EMPTY_ATTACHMENTS)) as page,
        patch("pipeline.collect_nowon.psycopg.connect", return_value=_mock_connection()),
        patch("pipeline.collect_nowon.save_notice_with_files") as save,
    ):
        result = collect_and_save_nowon(settings, database)
    assert result.saved_count == 1
    assert result.complete is False
    assert result.failures[0].stage == "listing_conflict"
    assert page.call_count == 1
    assert save.call_args.args[1].post_sn == "002"


def test_limit_is_partial_and_does_not_process_remaining_notices() -> None:
    notices = (_notice("001"), _notice("002"), _notice("003"))
    settings, database = _settings()
    with (
        patch("pipeline.collect_nowon.collect_all", return_value=_listing(notices)),
        patch("pipeline.collect_nowon.fetch_notice_page",
              side_effect=lambda notice, _: (notice.url, EMPTY_ATTACHMENTS)) as page,
        patch("pipeline.collect_nowon.psycopg.connect", return_value=_mock_connection()),
        patch("pipeline.collect_nowon.save_notice_with_files") as save,
    ):
        result = collect_and_save_nowon(settings, database, limit=1)
    assert result.listed_count == 3
    assert result.attempted_count == result.saved_count == 1
    assert result.limited is True
    assert result.complete is False
    assert page.call_count == save.call_count == 1


def test_transient_page_failure_retries_then_saves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pipeline.collect_nowon.sleep", lambda _: None)
    settings, database = _settings()
    attempts = 0

    def page(notice: RawNotice, _settings: NowonSettings) -> tuple[str, str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise NowonPageError("timeout", retryable=True)
        return notice.url, EMPTY_ATTACHMENTS

    with (
        patch("pipeline.collect_nowon.collect_all",
              return_value=_listing((_notice("001"),))),
        patch("pipeline.collect_nowon.fetch_notice_page", side_effect=page),
        patch("pipeline.collect_nowon.psycopg.connect", return_value=_mock_connection()),
        patch("pipeline.collect_nowon.save_notice_with_files") as save,
    ):
        result = collect_and_save_nowon(settings, database)
    assert attempts == 2
    assert result.complete is True
    save.assert_called_once()


def test_storage_failure_does_not_stop_next_notice() -> None:
    notices = (_notice("001"), _notice("002"))
    settings, database = _settings()
    with (
        patch("pipeline.collect_nowon.collect_all", return_value=_listing(notices)),
        patch("pipeline.collect_nowon.fetch_notice_page",
              side_effect=lambda notice, _: (notice.url, EMPTY_ATTACHMENTS)),
        patch("pipeline.collect_nowon.psycopg.connect", return_value=_mock_connection()),
        patch("pipeline.collect_nowon.save_notice_with_files",
              side_effect=[psycopg.IntegrityError("secret"), 42]) as save,
    ):
        result = collect_and_save_nowon(settings, database)
    assert result.saved_count == 1
    assert result.failures[0].post_sn == "001"
    assert result.failures[0].stage == "storage"
    assert result.complete is False
    assert save.call_count == 2


def test_attachment_parse_failure_does_not_replace_existing_file_list() -> None:
    notices = (_notice("001"), _notice("002"))
    settings, database = _settings()
    with (
        patch("pipeline.collect_nowon.collect_all", return_value=_listing(notices)),
        patch("pipeline.collect_nowon.fetch_notice_page",
              side_effect=lambda notice, _: (notice.url, EMPTY_ATTACHMENTS)),
        patch("pipeline.collect_nowon.extract_page_files",
              side_effect=[AttachmentError(
                  "bad attachment", code="attachment_section_missing",
              ), []]),
        patch("pipeline.collect_nowon.psycopg.connect", return_value=_mock_connection()),
        patch("pipeline.collect_nowon.save_notice_with_files") as save,
    ):
        result = collect_and_save_nowon(settings, database)
    assert result.saved_count == 1
    assert result.complete is False
    assert result.failures[0].stage == "attachments"
    assert result.failures[0].reason_code == "attachment_section_missing"
    assert save.call_count == 1
    assert save.call_args.args[1].post_sn == "002"


def test_incomplete_listing_can_save_seen_notice_but_not_claim_completion() -> None:
    notices = (_notice("001"),)
    listing = NowonCollection(
        notices,
        (PageStatus(1, 1, 1, True), PageStatus(2, 3, 3, False)),
        3, False, (), (),
    )
    settings, database = _settings()
    with (
        patch("pipeline.collect_nowon.collect_all", return_value=listing),
        patch("pipeline.collect_nowon.fetch_notice_page",
              side_effect=lambda notice, _: (notice.url, EMPTY_ATTACHMENTS)),
        patch("pipeline.collect_nowon.psycopg.connect", return_value=_mock_connection()),
        patch("pipeline.collect_nowon.save_notice_with_files") as save,
    ):
        result = collect_and_save_nowon(settings, database)
    assert result.saved_count == 1
    assert result.listing_complete is False
    assert result.complete is False
    assert result.failed_pages == ((2, 3),)
    save.assert_called_once()


def test_incomplete_empty_listing_does_not_connect() -> None:
    settings, database = _settings()
    listing = NowonCollection((), (PageStatus(1, 1000, 3, False),), None, False, (), ())
    with (
        patch("pipeline.collect_nowon.collect_all", return_value=listing),
        patch("pipeline.collect_nowon.psycopg.connect") as connect,
    ):
        result = collect_and_save_nowon(settings, database)
    assert result.complete is False
    assert result.failed_pages == ((1, 1000),)
    connect.assert_not_called()


@pytest.mark.parametrize("limit", [0, -1, True, "3"])
def test_invalid_limit_is_rejected_before_network(limit: object) -> None:
    settings, database = _settings()
    with patch("pipeline.collect_nowon.collect_all") as collect:
        with pytest.raises(ValueError, match="limit"):
            collect_and_save_nowon(settings, database, limit=limit)
    collect.assert_not_called()


def test_real_db_keeps_successes_and_repeated_runs_do_not_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.getenv("PIPELINE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PIPELINE_TEST_DATABASE_URL is required for multi-notice DB integration")
    prefix = uuid4().hex
    notices = tuple(_notice(f"00{prefix}{index}") for index in range(3))
    settings = NowonSettings("test-key", 2.0, 7.0)
    database = DatabaseSettings(database_url)
    fail_middle = True

    def page(notice: RawNotice, _settings: NowonSettings) -> tuple[str, str]:
        if fail_middle and notice.post_sn == notices[1].post_sn:
            raise NowonPageError("unavailable")
        html = (
            '<table><tr><th>첨부파일</th><td><ul class="file-list"><li>'
            f'<a href="/file?q_fileSn=1&amp;q_fileId={notice.post_sn}">'
            "attachment.pdf</a></li></ul></td></tr></table>"
        )
        return notice.url, html

    monkeypatch.setattr("pipeline.collect_nowon.collect_all",
                        lambda _: _listing(notices))
    monkeypatch.setattr("pipeline.collect_nowon.fetch_notice_page", page)
    try:
        first = collect_and_save_nowon(settings, database)
        assert first.saved_count == 2
        assert first.complete is False
        with psycopg.connect(database_url) as conn:
            rows = conn.execute(
                "select post_sn, body_html, is_visible from notices "
                "where post_sn like %s order by post_sn",
                (f"00{prefix}%",),
            ).fetchall()
            assert rows == [
                (notices[0].post_sn, "<p>Body</p>", True),
                (notices[2].post_sn, "<p>Body</p>", True),
            ]
        fail_middle = False
        second = collect_and_save_nowon(settings, database)
        third = collect_and_save_nowon(settings, database)
        assert second.complete is third.complete is True
        with psycopg.connect(database_url) as conn:
            rows = conn.execute(
                "select post_sn, is_modified, is_visible from notices "
                "where post_sn like %s order by post_sn",
                (f"00{prefix}%",),
            ).fetchall()
            assert rows == [(notice.post_sn, False, True) for notice in notices]
            files = conn.execute(
                "select n.post_sn, f.file_id from notice_files f "
                "join notices n on n.id = f.notice_id "
                "where n.post_sn like %s order by n.post_sn",
                (f"00{prefix}%",),
            ).fetchall()
            assert files == [(notice.post_sn, notice.post_sn) for notice in notices]
        fail_middle = True
        notices = (notices[0], replace(notices[1], title="Unverified change"), notices[2])
        incomplete = collect_and_save_nowon(settings, database)
        assert incomplete.complete is False
        assert incomplete.saved_count == 2
        with psycopg.connect(database_url) as conn:
            assert conn.execute(
                "select count(*) from notice_files f join notices n on n.id = f.notice_id "
                "where n.post_sn = %s", (notices[1].post_sn,),
            ).fetchone() == (1,)
            assert conn.execute(
                "select title, body_html, is_visible, is_modified from notices "
                "where post_sn=%s", (notices[1].post_sn,),
            ).fetchone() == ("Notice", "<p>Body</p>", True, False)
        fail_middle = False
        notices = (notices[0], replace(notices[1], title="Notice"), notices[2])
        notices = (replace(notices[0], title="Changed"), *notices[1:])
        changed = collect_and_save_nowon(settings, database)
        assert changed.complete is True
        with psycopg.connect(database_url) as conn:
            row = conn.execute(
                "select title, is_modified from notices where post_sn = %s",
                (notices[0].post_sn,),
            ).fetchone()
            assert row == ("Changed", True)
    finally:
        with psycopg.connect(database_url) as conn:
            conn.execute(
                "delete from notices where post_sn like %s", (f"00{prefix}%",),
            )
