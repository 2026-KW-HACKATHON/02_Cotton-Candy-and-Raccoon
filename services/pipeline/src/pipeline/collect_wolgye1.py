"""Collect verified Wolgye 1-dong notices and save them independently."""

from collections.abc import Callable
from dataclasses import dataclass
from time import sleep
from typing import Literal

import psycopg

from pipeline.attachments.dong_html import extract_dong_files
from pipeline.attachments.nowon_html import AttachmentError
from pipeline.config import DatabaseSettings, WolgyeSettings
from pipeline.models import FileRecord, NoticeRecord
from pipeline.sources.wolgye1_board import (
    BoardEntry,
    BoardPage,
    WolgyeSourceError,
    fetch_detail_page,
    fetch_list_page,
    parse_board_page,
    parse_detail_page,
    parse_list_page,
)
from pipeline.storage.notice_bundle import save_notice_with_files
from pipeline.transform.dong import DongTransformError, transform_dong_notice


@dataclass(frozen=True, slots=True)
class WolgyeFailure:
    post_sn: str
    stage: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class WolgyeListing:
    total_count: int
    entries: tuple[BoardEntry, ...]
    failed_pages: tuple[int, ...]
    duplicate_count: int
    conflicting_post_sns: tuple[str, ...]
    limited: bool
    complete: bool


@dataclass(frozen=True, slots=True)
class CollectWolgyeResult:
    total_count: int
    listed_count: int
    attempted_count: int
    saved_count: int
    listing_complete: bool
    limited: bool
    failed_pages: tuple[int, ...]
    duplicate_count: int
    failures: tuple[WolgyeFailure, ...]

    @property
    def complete(self) -> bool:
        return (
            self.listing_complete
            and not self.limited
            and not self.failures
            and self.saved_count == self.listed_count
        )


@dataclass(frozen=True, slots=True)
class ScheduledWolgyeResult:
    mode: Literal["new", "refresh"]
    total_count: int
    selected_count: int
    saved_count: int
    pages_read: int
    initial_baseline: bool
    listing_complete: bool
    failed_pages: tuple[int, ...]
    failures: tuple[WolgyeFailure, ...]

    @property
    def complete(self) -> bool:
        """Whether this run's scheduled scope succeeded, not the whole board."""
        return (
            self.listing_complete and not self.failures and self.saved_count == self.selected_count
        )


def _fetch_list_with_retry(settings: WolgyeSettings, page: int) -> BoardPage:
    for attempt in range(3):
        try:
            return parse_board_page(fetch_list_page(settings, page=page), expected_page=page)
        except WolgyeSourceError as error:
            if not error.retryable or attempt == 2:
                raise
            sleep(0.2 * 2**attempt)
    raise AssertionError("retry loop must return or raise")


def _fetch_detail_with_retry(entry: BoardEntry, settings: WolgyeSettings) -> str:
    for attempt in range(3):
        try:
            return fetch_detail_page(entry, settings)
        except WolgyeSourceError as error:
            if not error.retryable or attempt == 2:
                raise
            sleep(0.2 * 2**attempt)
    raise AssertionError("retry loop must return or raise")


def collect_wolgye_listing(
    settings: WolgyeSettings,
    *,
    limit: int | None = None,
) -> WolgyeListing:
    """Read visible list pages; a moving or missing range is never complete."""
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit은 1 이상의 정수여야 합니다.")
    first = _fetch_list_with_retry(settings, 1)
    entries: dict[str, BoardEntry] = {}
    regular_post_sns: set[str] = set()
    failed_pages: list[int] = []
    conflicts: set[str] = set()
    duplicate_count = 0
    limited = False

    for page_number in range(1, first.total_pages + 1):
        try:
            page = first if page_number == 1 else _fetch_list_with_retry(settings, page_number)
        except WolgyeSourceError as error:
            if error.rate_limited:
                raise
            failed_pages.append(page_number)
            continue
        if (
            page.total_count != first.total_count
            or page.total_pages != first.total_pages
            or page.page_size != first.page_size
        ):
            failed_pages.append(page_number)
            continue
        regular_post_sns.update(page.regular_post_sns)
        for entry in page.entries:
            previous = entries.get(entry.post_sn)
            if previous is not None:
                duplicate_count += 1
                if (previous.title, previous.department, previous.registered_on) != (
                    entry.title,
                    entry.department,
                    entry.registered_on,
                ):
                    conflicts.add(entry.post_sn)
                entry = BoardEntry(
                    entry.post_sn,
                    entry.title,
                    entry.department,
                    entry.registered_on,
                    previous.is_pinned or entry.is_pinned,
                )
            entries[entry.post_sn] = entry
        if limit is not None and len(entries) >= limit:
            limited = page_number < first.total_pages or len(entries) > limit
            break

    snapshot_stable = False
    if not limited:
        try:
            latest = _fetch_list_with_retry(settings, 1)
            snapshot_stable = (
                latest.total_count == first.total_count
                and latest.total_pages == first.total_pages
                and latest.page_size == first.page_size
                and latest.regular_post_sns == first.regular_post_sns
                and latest.entries == first.entries
            )
        except WolgyeSourceError:
            failed_pages.append(1)
    complete = (
        not limited
        and not failed_pages
        and snapshot_stable
        and not conflicts
        and len(regular_post_sns) == first.total_count
    )
    return WolgyeListing(
        first.total_count,
        tuple(entries.values()),
        tuple(failed_pages),
        duplicate_count,
        tuple(sorted(conflicts)),
        limited,
        complete,
    )


def collect_one_wolgye1(
    settings: WolgyeSettings,
    *,
    post_sn: str | None = None,
    page: int = 1,
) -> tuple[NoticeRecord, list[FileRecord]]:
    """Verify the post on a selected list page before saving its detail."""
    if type(page) is not int or page < 1:
        raise ValueError("page는 1 이상의 정수여야 합니다.")
    if page != 1 and post_sn is None:
        raise ValueError("첫 페이지 밖에서는 --post-sn이 필요합니다.")
    entries = parse_list_page(fetch_list_page(settings, page=page))
    if post_sn is not None:
        if not post_sn.isdigit():
            raise WolgyeSourceError("--post-sn은 숫자로 된 게시물 번호여야 합니다.")
        entry = next((item for item in entries if item.post_sn == post_sn), None)
        if entry is None:
            page_name = "첫 페이지" if page == 1 else f"{page}페이지"
            raise WolgyeSourceError(f"--post-sn 게시물이 월계1동 목록 {page_name}에 없습니다.")
    else:
        entry = next(
            (
                item
                for item in entries
                if not item.is_pinned and item.department.startswith("월계1동")
            ),
            None,
        )
        if entry is None:
            raise WolgyeSourceError("월계1동 목록 첫 페이지에 일반 공지가 없습니다.")

    page_html = fetch_detail_page(entry, settings)
    notice = parse_detail_page(entry, page_html)
    files = extract_dong_files(notice, page_html)
    record = transform_dong_notice(notice)
    return record, files


def _save_entries(
    conn: psycopg.Connection[tuple],
    selected: tuple[BoardEntry, ...],
    settings: WolgyeSettings,
    database_url: str,
    *,
    conflicts: tuple[str, ...] = (),
    paced: bool = False,
    after_save: Callable[[int], None] | None = None,
) -> tuple[int, tuple[WolgyeFailure, ...]]:
    failures: list[WolgyeFailure] = []
    saved_count = 0
    try:
        for index, entry in enumerate(selected):
            if entry.post_sn in conflicts:
                failures.append(
                    WolgyeFailure(
                        entry.post_sn,
                        "listing_conflict",
                        "listing_conflict",
                    )
                )
                continue
            if paced and index:
                sleep(1)
            try:
                page_html = _fetch_detail_with_retry(entry, settings)
                notice = parse_detail_page(entry, page_html)
            except WolgyeSourceError as error:
                reason = "rate_limited" if error.rate_limited else "detail_unavailable"
                failures.append(WolgyeFailure(entry.post_sn, "detail", reason))
                if error.rate_limited:
                    failures.extend(
                        WolgyeFailure(rest.post_sn, "detail", "rate_limited_not_attempted")
                        for rest in selected[index + 1 :]
                    )
                    break
                continue
            try:
                files = extract_dong_files(notice, page_html)
            except AttachmentError as error:
                failures.append(WolgyeFailure(entry.post_sn, "attachments", error.code))
                continue
            try:
                record = transform_dong_notice(notice)
            except DongTransformError:
                failures.append(WolgyeFailure(entry.post_sn, "transform", "invalid_notice"))
                continue
            if conn.closed:
                try:
                    conn = psycopg.connect(
                        database_url,
                        connect_timeout=5,
                        autocommit=True,
                    )
                except psycopg.Error:
                    failures.extend(
                        WolgyeFailure(remaining.post_sn, "storage", "db_unavailable")
                        for remaining in selected[index:]
                    )
                    break
            try:
                notice_id = save_notice_with_files(conn, record, files)
            except (psycopg.Error, ValueError):
                failures.append(WolgyeFailure(entry.post_sn, "storage", "db_save_failed"))
                continue
            saved_count += 1
            if after_save is not None:
                after_save(notice_id)
    finally:
        conn.close()
    return saved_count, tuple(failures)


def collect_and_save_wolgye1(
    settings: WolgyeSettings,
    database: DatabaseSettings,
    *,
    limit: int | None = None,
    after_save: Callable[[int], None] | None = None,
) -> CollectWolgyeResult:
    """Save complete notices independently without any bulk visibility changes."""
    listing = collect_wolgye_listing(settings, limit=limit)
    selected = listing.entries[:limit] if limit is not None else listing.entries
    if not selected:
        return CollectWolgyeResult(
            listing.total_count,
            len(listing.entries),
            0,
            0,
            listing.complete,
            listing.limited,
            listing.failed_pages,
            listing.duplicate_count,
            (),
        )

    conn = psycopg.connect(database.database_url, connect_timeout=5, autocommit=True)
    saved_count, failures = _save_entries(
        conn,
        selected,
        settings,
        database.database_url,
        conflicts=listing.conflicting_post_sns,
        after_save=after_save,
    )
    return CollectWolgyeResult(
        listing.total_count,
        len(listing.entries),
        len(selected),
        saved_count,
        listing.complete,
        listing.limited,
        listing.failed_pages,
        listing.duplicate_count,
        failures,
    )


def _known_post_sns(
    conn: psycopg.Connection[tuple],
    entries: tuple[BoardEntry, ...],
) -> set[str]:
    if not entries:
        return set()
    rows = conn.execute(
        "SELECT post_sn FROM notices WHERE category = 'dong' "
        "AND source_board = '1042' AND post_sn = ANY(%s)",
        ([entry.post_sn for entry in entries],),
    ).fetchall()
    return {row[0] for row in rows}


def collect_and_save_wolgye1_scheduled(
    settings: WolgyeSettings,
    database: DatabaseSettings,
    *,
    mode: Literal["new", "refresh"],
    after_save: Callable[[int], None] | None = None,
) -> ScheduledWolgyeResult:
    """Check new regular posts or refresh five regular posts and all pinned posts."""
    if mode not in ("new", "refresh"):
        raise ValueError("mode must be 'new' or 'refresh'")
    conn = psycopg.connect(database.database_url, connect_timeout=5, autocommit=True)
    try:
        initial_baseline = (
            mode == "new"
            and not conn.execute(
                "SELECT EXISTS (SELECT 1 FROM notices WHERE category = 'dong' "
                "AND source_board = '1042' "
                "AND dong_group = 'wolgye1' AND NOT is_pinned)",
            ).fetchone()[0]
        )
        first = _fetch_list_with_retry(settings, 1)
        selected: list[BoardEntry] = []
        pinned_sns: set[str] = set()
        if mode == "refresh":
            for entry in first.entries:
                if entry.is_pinned and entry.post_sn not in pinned_sns:
                    pinned_sns.add(entry.post_sn)
                    selected.append(entry)
        regular_seen: dict[str, BoardEntry] = {}
        seen_numbered: set[str] = set()
        conflicts: set[str] = set()
        first_five_new = True
        found_boundary = False
        listing_complete = True
        failed_pages: list[int] = []
        pages_read = 0
        for page_number in range(1, first.total_pages + 1):
            if page_number > 1:
                sleep(1)
            try:
                page = (
                    first
                    if page_number == 1
                    else _fetch_list_with_retry(
                        settings,
                        page_number,
                    )
                )
            except WolgyeSourceError as error:
                if error.rate_limited:
                    raise
                listing_complete = False
                failed_pages.append(page_number)
                break
            pages_read += 1
            if (
                page.total_count != first.total_count
                or page.total_pages != first.total_pages
                or page.page_size != first.page_size
            ):
                listing_complete = False
                failed_pages.append(page_number)
                break
            seen_numbered.update(page.regular_post_sns)
            regular = tuple(entry for entry in page.entries if not entry.is_pinned)
            known = _known_post_sns(conn, regular) if mode == "new" else set()
            for entry in regular:
                if entry.post_sn in pinned_sns:
                    continue
                previous = regular_seen.get(entry.post_sn)
                if previous is not None:
                    if previous != entry:
                        conflicts.add(entry.post_sn)
                    continue
                regular_seen[entry.post_sn] = entry
                if len(regular_seen) <= 5:
                    if mode == "refresh" or entry.post_sn not in known:
                        selected.append(entry)
                    else:
                        first_five_new = False
                    if len(regular_seen) == 5 and (
                        mode == "refresh" or initial_baseline or not first_five_new
                    ):
                        found_boundary = True
                        break
                elif entry.post_sn in known:
                    found_boundary = True
                    break
                else:
                    selected.append(entry)
            if found_boundary:
                break
        if not found_boundary and not failed_pages and len(seen_numbered) != first.total_count:
            listing_complete = False
        if pages_read > 1:
            sleep(1)
            try:
                latest = _fetch_list_with_retry(settings, 1)
                if latest != first:
                    listing_complete = False
                    failed_pages.append(1)
            except WolgyeSourceError as error:
                if error.rate_limited:
                    raise
                listing_complete = False
                failed_pages.append(1)
        chosen = tuple(selected)
    except (psycopg.Error, WolgyeSourceError):
        conn.close()
        raise
    saved_count, failures = (
        _save_entries(
            conn,
            chosen,
            settings,
            database.database_url,
            conflicts=tuple(conflicts),
            paced=True,
            after_save=after_save,
        )
        if chosen
        else (0, ())
    )
    if not chosen:
        conn.close()
    return ScheduledWolgyeResult(
        mode,
        first.total_count,
        len(chosen),
        saved_count,
        pages_read,
        initial_baseline,
        listing_complete,
        tuple(failed_pages),
        failures,
    )
