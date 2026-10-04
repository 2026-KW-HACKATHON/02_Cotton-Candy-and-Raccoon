"""Per-board initial 25 / regular 10 collection, without visibility sweeps."""

from dataclasses import dataclass
from time import sleep
from typing import Literal

import psycopg

from pipeline.attachments.seoul_html import SeoulAttachmentError
from pipeline.collect_seoul import SeoulStorageError, prepare_notice
from pipeline.config import DatabaseSettings, SeoulNewsSettings
from pipeline.models import FileRecord, NoticeRecord, RawSeoulNotice
from pipeline.sources.seoul_api import SeoulApiPage, SeoulSourceError, collect_page
from pipeline.sources.seoul_page import BOARD_SLUGS, SeoulPageError
from pipeline.storage.notice_bundle import save_notice_with_files
from pipeline.transform.seoul import SeoulTransformError

INITIAL_COUNT = 25
RECENT_COUNT = 10
PAGE_OVERLAP = 2


@dataclass(frozen=True, slots=True)
class SeoulFailure:
    post_sn: str | None
    stage: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class BoardResult:
    source_board: str
    total_count: int | None
    selected_count: int
    saved_count: int
    pages_read: int
    initial_baseline: bool | None
    listing_complete: bool
    failures: tuple[SeoulFailure, ...]

    @property
    def complete(self) -> bool:
        return (
            self.listing_complete and not self.failures and self.saved_count == self.selected_count
        )


@dataclass(frozen=True, slots=True)
class ScheduledSeoulResult:
    mode: Literal["new", "refresh"]
    boards: tuple[BoardResult, ...]

    @property
    def complete(self) -> bool:
        return bool(self.boards) and all(b.complete for b in self.boards)


def _api_page(settings: SeoulNewsSettings, board: str, start: int, end: int) -> SeoulApiPage:
    for attempt in range(3):
        sleep(1 if attempt == 0 else 2**attempt)
        try:
            return collect_page(settings, source_board=board, start_index=start, end_index=end)
        except SeoulSourceError as error:
            if error.rate_limited or not error.retryable or attempt == 2:
                raise
    raise AssertionError("retry loop must return or raise")


def _prepare(
    notice: RawSeoulNotice,
    settings: SeoulNewsSettings,
) -> tuple[NoticeRecord, tuple[FileRecord, ...]]:
    for attempt in range(3):
        sleep(1 if attempt == 0 else 2**attempt)
        try:
            return prepare_notice(notice, settings)
        except SeoulPageError as error:
            if error.rate_limited or not error.retryable or attempt == 2:
                raise
    raise AssertionError("retry loop must return or raise")


def _known(conn: psycopg.Connection, board: str, rows: tuple[RawSeoulNotice, ...]) -> set[str]:
    if not rows:
        return set()
    return {
        row[0]
        for row in conn.execute(
            "select post_sn from notices where category='seoul' and source_board=%s "
            "and is_visible and post_sn=ANY(%s)",
            (board, [n.post_sn for n in rows]),
        ).fetchall()
    }


def _board(
    conn: psycopg.Connection,
    settings: SeoulNewsSettings,
    board: str,
    mode: Literal["new", "refresh"],
) -> BoardResult:
    initial = not conn.execute(
        "select exists(select 1 from notices where category='seoul' and source_board=%s)",
        (board,),
    ).fetchone()[0]
    window = INITIAL_COUNT if initial else RECENT_COUNT
    first = _api_page(settings, board, 1, window)
    target = min(window, first.total_count)
    selected: dict[str, RawSeoulNotice] = {}
    seen: dict[str, RawSeoulNotice] = {}
    conflicts: set[str] = set()
    failures: list[SeoulFailure] = []
    listing_complete = True
    pages_read = 0
    all_new = True
    boundary = target == 0
    page = first
    while not boundary:
        pages_read += 1
        if page.total_count != first.total_count:
            failures.append(SeoulFailure(None, "listing", "total_count_changed"))
            listing_complete = False
            break
        known = _known(conn, board, page.notices)
        before = len(seen)
        page_ids: set[str] = set()
        for row in page.notices:
            if row.post_sn in page_ids:
                listing_complete = False
                failures.append(SeoulFailure(row.post_sn, "listing", "duplicate_row"))
            page_ids.add(row.post_sn)
            previous = seen.get(row.post_sn)
            if previous is not None:
                if previous != row:
                    conflicts.add(row.post_sn)
                    listing_complete = False
                continue
            seen[row.post_sn] = row
            if len(seen) <= target:
                if row.post_sn in known:
                    all_new = False
                if initial or mode == "refresh" or row.post_sn not in known:
                    selected[row.post_sn] = row
                if len(seen) == target and (initial or not all_new):
                    boundary = True
                    break
            elif row.post_sn in known:
                boundary = True
                break
            else:
                selected[row.post_sn] = row
        if boundary or page.end_index >= first.total_count:
            if not boundary and len(seen) != first.total_count:
                listing_complete = False
                failures.append(SeoulFailure(None, "listing", "unique_count_mismatch"))
            break
        if len(seen) == before:
            listing_complete = False
            failures.append(SeoulFailure(None, "listing", "no_listing_progress"))
            break
        start = page.end_index - PAGE_OVERLAP + 1
        end = min(start + RECENT_COUNT - 1, first.total_count)
        try:
            page = _api_page(settings, board, start, end)
        except SeoulSourceError as error:
            if error.rate_limited:
                raise
            failures.append(SeoulFailure(None, "listing", "api_page_failed"))
            listing_complete = False
            break
    if target == 0:
        pages_read = 1
    if pages_read > 1:
        try:
            head = _api_page(settings, board, 1, 1)
            if (
                head.total_count != first.total_count
                or not head.notices
                or head.notices[0] != first.notices[0]
            ):
                listing_complete = False
                failures.append(SeoulFailure(None, "listing", "listing_changed"))
        except SeoulSourceError as error:
            if error.rate_limited:
                raise
            listing_complete = False
            failures.append(SeoulFailure(None, "listing", "head_check_failed"))
    saved = 0
    for index, row in enumerate(selected.values()):
        if row.post_sn in conflicts:
            failures.append(SeoulFailure(row.post_sn, "listing", "listing_conflict"))
            continue
        try:
            record, files = _prepare(row, settings)
        except SeoulPageError as error:
            reason = error.reason_code
            failures.append(SeoulFailure(row.post_sn, "page", reason))
            if error.rate_limited:
                failures.extend(
                    SeoulFailure(n.post_sn, "page", "rate_limited_not_attempted")
                    for n in tuple(selected.values())[index + 1 :]
                )
                break
            continue
        except SeoulAttachmentError:
            failures.append(SeoulFailure(row.post_sn, "attachments", "invalid_file_reference"))
            continue
        except SeoulTransformError:
            failures.append(SeoulFailure(row.post_sn, "transform", "invalid_notice"))
            continue
        try:
            save_notice_with_files(conn, record, files)
        except (psycopg.Error, ValueError):
            failures.append(SeoulFailure(row.post_sn, "storage", "db_save_failed"))
            continue
        saved += 1
    return BoardResult(
        board,
        first.total_count,
        len(selected),
        saved,
        pages_read,
        initial,
        listing_complete,
        tuple(failures),
    )


def collect_scheduled(
    settings: SeoulNewsSettings,
    database: DatabaseSettings,
    *,
    mode: Literal["new", "refresh"],
    source_board: str | None = None,
) -> ScheduledSeoulResult:
    if mode not in ("new", "refresh"):
        raise ValueError("mode must be 'new' or 'refresh'")
    if settings.seoul_news_api_key == "sample":
        raise ValueError("서울시 최초25건·정기10건 수집에는 발급 API 키가 필요합니다.")
    if source_board is not None and source_board not in BOARD_SLUGS:
        raise ValueError("unsupported source_board")
    boards = (source_board,) if source_board is not None else tuple(BOARD_SLUGS)
    results: list[BoardResult] = []
    stopped: str | None = None
    try:
        with psycopg.connect(database.database_url, connect_timeout=5, autocommit=True) as conn:
            for board in boards:
                if stopped:
                    results.append(
                        BoardResult(
                            board,
                            None,
                            0,
                            0,
                            0,
                            None,
                            False,
                            (SeoulFailure(None, "listing", stopped),),
                        )
                    )
                    continue
                try:
                    result = _board(conn, settings, board, mode)
                except SeoulSourceError as error:
                    reason = "rate_limited" if error.rate_limited else "api_page_failed"
                    result = BoardResult(
                        board, None, 0, 0, 0, None, False, (SeoulFailure(None, "listing", reason),)
                    )
                    if error.rate_limited:
                        stopped = "rate_limited_not_attempted"
                except psycopg.Error:
                    result = BoardResult(
                        board,
                        None,
                        0,
                        0,
                        0,
                        None,
                        False,
                        (SeoulFailure(None, "storage", "db_unavailable"),),
                    )
                    stopped = "db_unavailable_not_attempted"
                results.append(result)
                if any(f.reason_code == "rate_limited" for f in result.failures):
                    stopped = "rate_limited_not_attempted"
    except psycopg.Error:
        raise SeoulStorageError("서울시 DB 연결 실패: 저장 대상 설정을 확인하세요.") from None
    return ScheduledSeoulResult(mode, tuple(results))
