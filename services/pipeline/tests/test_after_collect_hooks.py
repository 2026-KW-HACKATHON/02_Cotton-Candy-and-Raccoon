"""Collection hooks run only after an original notice has been saved."""

from collections.abc import Callable
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from pipeline.collect_nowon import (
    collect_and_save_nowon,
    collect_and_save_nowon_scheduled,
)
from pipeline.collect_wolgye1 import (
    WolgyeListing,
    collect_and_save_wolgye1,
    collect_and_save_wolgye1_scheduled,
)
from pipeline.config import DatabaseSettings, NowonSettings, WolgyeSettings
from pipeline.models import FileRecord, NoticeRecord, RawNotice
from pipeline.sources.nowon_api import NowonCollection, NowonPage, PageStatus
from pipeline.sources.wolgye1_board import BoardEntry, BoardPage

EMPTY_ATTACHMENTS = "<table><tr><th>첨부파일</th><td>첨부파일이 없습니다</td></tr></table>"


def _notice(source: str, post_sn: str) -> RawNotice:
    return RawNotice(
        category="nowon" if source == "nowon" else "dong",
        dong_group=None if source == "nowon" else "wolgye1",
        is_pinned=False,
        post_sn=post_sn,
        title="공지 제목",
        department="행정팀" if source == "nowon" else "월계1동 행정민원팀",
        registered_on="2026-09-28",
        url=(
            f"https://www.nowon.kr/www/user/bbs/BD_selectBbs.do?q_bbsCode=1001&q_bbscttSn={post_sn}"
            if source == "nowon"
            else BoardEntry(post_sn, "공지 제목", "월계1동", "2026-09-28", False).url
        ),
        body_html="<p>본문</p>",
        license_type="KOGL-4" if source == "nowon" else "KOGL-1",
    )


def _run_collector(
    source: str,
    scheduled: bool,
    save: Callable[[MagicMock, NoticeRecord, list[FileRecord]], int],
    after_save: Callable[[int], None],
):
    database = DatabaseSettings("postgresql://pipeline@localhost/test")
    conn = MagicMock()
    conn.closed = False
    conn.execute.return_value.fetchone.return_value = (True,)
    notices = tuple(_notice(source, post_sn) for post_sn in ("001", "002"))
    module = f"pipeline.collect_{source}"
    with ExitStack() as stack:
        stack.enter_context(patch(f"{module}.psycopg.connect", return_value=conn))
        stack.enter_context(patch(f"{module}.save_notice_with_files", side_effect=save))
        stack.enter_context(patch(f"{module}.sleep"))
        if source == "nowon":
            settings = NowonSettings("test-key", 2.0, 7.0)
            stack.enter_context(
                patch(
                    f"{module}.fetch_notice_page",
                    side_effect=lambda notice, _: (notice.url, EMPTY_ATTACHMENTS),
                )
            )
            if scheduled:
                stack.enter_context(
                    patch(
                        f"{module}._fetch_api_page_with_retry",
                        return_value=NowonPage(1, 50, 2, notices),
                    )
                )
                return collect_and_save_nowon_scheduled(
                    settings,
                    database,
                    mode="refresh",
                    after_save=after_save,
                )
            stack.enter_context(
                patch(
                    f"{module}.collect_all",
                    return_value=NowonCollection(
                        notices,
                        (PageStatus(1, 2, 2, True),),
                        2,
                        True,
                        (),
                        (),
                    ),
                )
            )
            return collect_and_save_nowon(settings, database, after_save=after_save)

        settings = WolgyeSettings(1, 2)
        entries = tuple(
            BoardEntry(notice.post_sn, notice.title, notice.department, notice.registered_on, False)
            for notice in notices
        )
        stack.enter_context(patch(f"{module}._fetch_detail_with_retry", return_value="detail"))
        stack.enter_context(
            patch(
                f"{module}.parse_detail_page",
                side_effect=lambda entry, _: _notice(source, entry.post_sn),
            )
        )
        stack.enter_context(patch(f"{module}.extract_dong_files", return_value=[]))
        if scheduled:
            stack.enter_context(
                patch(
                    f"{module}._fetch_list_with_retry",
                    return_value=BoardPage(1, 2, 1, 2, entries, ("001", "002")),
                )
            )
            return collect_and_save_wolgye1_scheduled(
                settings,
                database,
                mode="refresh",
                after_save=after_save,
            )
        stack.enter_context(
            patch(
                f"{module}.collect_wolgye_listing",
                return_value=WolgyeListing(2, entries, (), 0, (), False, True),
            )
        )
        return collect_and_save_wolgye1(settings, database, after_save=after_save)


@pytest.mark.parametrize("source", ["nowon", "wolgye1"])
@pytest.mark.parametrize("scheduled", [False, True], ids=["normal", "scheduled"])
def test_after_save_receives_stored_id_only_after_save_returns(
    source: str,
    scheduled: bool,
) -> None:
    events: list[tuple[str, str | int]] = []

    def save(_conn: MagicMock, record: NoticeRecord, _files: list[FileRecord]) -> int:
        events.append(("save_started", record.post_sn))
        notice_id = 400 + int(record.post_sn)
        events.append(("save_finished", notice_id))
        return notice_id

    def after_save(notice_id: int) -> None:
        assert events[-1] == ("save_finished", notice_id)
        events.append(("after_save", notice_id))

    result = _run_collector(source, scheduled, save, after_save)

    assert events == [
        ("save_started", "001"),
        ("save_finished", 401),
        ("after_save", 401),
        ("save_started", "002"),
        ("save_finished", 402),
        ("after_save", 402),
    ]
    assert result.saved_count == 2
    assert result.complete is True
    assert result.failures == ()


@pytest.mark.parametrize("source", ["nowon", "wolgye1"])
@pytest.mark.parametrize("scheduled", [False, True], ids=["normal", "scheduled"])
def test_failed_original_save_skips_hook_and_later_notice_still_proceeds(
    source: str,
    scheduled: bool,
) -> None:
    events: list[tuple[str, str | int]] = []

    def save(_conn: MagicMock, record: NoticeRecord, _files: list[FileRecord]) -> int:
        events.append(("save_started", record.post_sn))
        if record.post_sn == "001":
            raise psycopg.IntegrityError("original save failed")
        events.append(("save_finished", 402))
        return 402

    def after_save(notice_id: int) -> None:
        assert events[-1] == ("save_finished", notice_id)
        events.append(("after_save", notice_id))

    result = _run_collector(source, scheduled, save, after_save)

    assert events == [
        ("save_started", "001"),
        ("save_started", "002"),
        ("save_finished", 402),
        ("after_save", 402),
    ]
    assert result.saved_count == 1
    assert result.complete is False
    assert [
        (failure.post_sn, failure.stage, failure.reason_code) for failure in result.failures
    ] == [("001", "storage", "db_save_failed")]
