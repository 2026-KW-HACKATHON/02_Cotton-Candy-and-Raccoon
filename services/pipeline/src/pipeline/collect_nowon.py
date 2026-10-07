"""Collect and persist Nowon notices one complete notice at a time."""

from dataclasses import dataclass
from time import sleep
from typing import Literal

import psycopg

from pipeline.attachments.nowon_html import (
    AttachmentError,
    extract_files,
    extract_page_files,
    merge_files,
)
from pipeline.config import DatabaseSettings, NowonSettings
from pipeline.models import FileRecord, NoticeRecord, RawNotice
from pipeline.sources.nowon_api import (
    NowonCollection,
    NowonPage,
    NowonSourceError,
    collect_all,
    collect_page,
)
from pipeline.sources.nowon_page import NowonPageError, NowonPageMissing, fetch_notice_page
from pipeline.storage.notice_bundle import save_notice_with_files
from pipeline.transform.nowon import TransformError, transform_nowon_notice

FailureStage = Literal[
    "listing_conflict",
    "page_missing",
    "page",
    "attachments",
    "transform",
    "storage",
]


@dataclass(frozen=True, slots=True)
class NoticeFailure:
    post_sn: str
    stage: FailureStage
    reason_code: str


@dataclass(frozen=True, slots=True)
class PreparedNotice:
    record: NoticeRecord
    files: list[FileRecord]
    failure: NoticeFailure | None


@dataclass(frozen=True, slots=True)
class CollectNowonResult:
    total_count: int | None
    listed_count: int
    attempted_count: int
    saved_count: int
    listing_complete: bool
    limited: bool
    failed_pages: tuple[tuple[int, int], ...]
    failures: tuple[NoticeFailure, ...]

    @property
    def complete(self) -> bool:
        return (
            self.listing_complete
            and not self.limited
            and not self.failures
            and self.saved_count == self.listed_count
        )


@dataclass(frozen=True, slots=True)
class ScheduledNowonResult:
    mode: Literal["new", "refresh"]
    total_count: int
    selected_count: int
    saved_count: int
    pages_read: int
    initial_baseline: bool
    listing_complete: bool
    failed_ranges: tuple[tuple[int, int], ...]
    failures: tuple[NoticeFailure, ...]

    @property
    def complete(self) -> bool:
        """Whether this run's selected scope succeeded, not the whole API."""
        return (
            self.listing_complete and not self.failures and self.saved_count == self.selected_count
        )


def _fetch_page_with_retry(
    notice: RawNotice,
    settings: NowonSettings,
) -> tuple[str, str]:
    for attempt in range(3):
        try:
            return fetch_notice_page(notice, settings)
        except NowonPageError as error:
            if error.rate_limited or not error.retryable or attempt == 2:
                raise
            sleep(0.2 * 2**attempt)
    raise AssertionError("retry loop must return or raise")


def _prepare_notice(
    notice: RawNotice,
    settings: NowonSettings,
) -> PreparedNotice:
    record = transform_nowon_notice(notice)
    try:
        body_files = extract_files(notice)
    except AttachmentError as error:
        return PreparedNotice(
            record,
            [],
            NoticeFailure(notice.post_sn, "attachments", error.code),
        )
    try:
        page_url, page_html = _fetch_page_with_retry(notice, settings)
    except NowonPageMissing:
        return PreparedNotice(
            record,
            [],
            NoticeFailure(notice.post_sn, "page_missing", "source_page_missing"),
        )
    except NowonPageError as error:
        return PreparedNotice(
            record,
            [],
            NoticeFailure(
                notice.post_sn,
                "page",
                "rate_limited" if error.rate_limited else "page_unavailable",
            ),
        )
    try:
        page_files = extract_page_files(notice, page_html, page_url)
        files = merge_files(body_files, page_files)
    except AttachmentError as error:
        return PreparedNotice(
            record,
            [],
            NoticeFailure(notice.post_sn, "attachments", error.code),
        )
    return PreparedNotice(record, files, None)


def _save_notices(
    conn: psycopg.Connection[tuple],
    notices: tuple[RawNotice, ...],
    settings: NowonSettings,
    database_url: str,
    *,
    conflicts: set[str] | None = None,
    paced: bool = False,
) -> tuple[int, tuple[NoticeFailure, ...]]:
    failures: list[NoticeFailure] = []
    saved_count = 0
    try:
        for index, notice in enumerate(notices):
            if conflicts and notice.post_sn in conflicts:
                failures.append(
                    NoticeFailure(
                        notice.post_sn,
                        "listing_conflict",
                        "listing_conflict",
                    )
                )
                continue
            if paced and index:
                sleep(1)
            try:
                prepared = _prepare_notice(notice, settings)
            except TransformError:
                failures.append(NoticeFailure(notice.post_sn, "transform", "invalid_notice"))
                continue
            if prepared.failure is not None:
                failures.append(prepared.failure)
                if prepared.failure.reason_code == "rate_limited":
                    failures.extend(
                        NoticeFailure(rest.post_sn, "page", "rate_limited_not_attempted")
                        for rest in notices[index + 1 :]
                    )
                    break
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
                        NoticeFailure(rest.post_sn, "storage", "db_unavailable")
                        for rest in notices[index:]
                    )
                    break
            try:
                save_notice_with_files(conn, prepared.record, prepared.files)
            except (psycopg.Error, ValueError):
                failures.append(NoticeFailure(notice.post_sn, "storage", "db_save_failed"))
                continue
            saved_count += 1
    finally:
        conn.close()
    return saved_count, tuple(failures)


def collect_and_save_nowon(
    settings: NowonSettings,
    database: DatabaseSettings,
    *,
    limit: int | None = None,
) -> CollectNowonResult:
    """Save complete notices independently; never change visibility in bulk."""
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit은 1 이상의 정수여야 합니다.")

    listing: NowonCollection = collect_all(settings)
    notices = listing.notices[:limit] if limit is not None else listing.notices
    limited = limit is not None and len(notices) < len(listing.notices)
    failed_pages = tuple(
        (page.start_index, page.end_index) for page in listing.pages if not page.succeeded
    )
    if not notices:
        return CollectNowonResult(
            listing.total_count,
            len(listing.notices),
            0,
            0,
            listing.complete,
            limited,
            failed_pages,
            (),
        )

    # autocommit keeps save_notice_with_files' transaction scoped to one notice.
    conn = psycopg.connect(database.database_url, connect_timeout=5, autocommit=True)
    saved_count, failures = _save_notices(
        conn,
        notices,
        settings,
        database.database_url,
        conflicts=set(listing.conflicting_post_sns),
    )

    return CollectNowonResult(
        listing.total_count,
        len(listing.notices),
        len(notices),
        saved_count,
        listing.complete,
        limited,
        failed_pages,
        failures,
    )


def _fetch_api_page_with_retry(
    settings: NowonSettings,
    start: int,
    end: int,
) -> NowonPage:
    for attempt in range(3):
        try:
            return collect_page(settings, start_index=start, end_index=end)
        except NowonSourceError as error:
            if error.rate_limited or not error.retryable or attempt == 2:
                raise
            sleep(0.2 * 2**attempt)
    raise AssertionError("retry loop must return or raise")


def _known_nowon_ids(
    conn: psycopg.Connection[tuple],
    notices: tuple[RawNotice, ...],
) -> set[str]:
    if not notices:
        return set()
    rows = conn.execute(
        "SELECT post_sn FROM notices WHERE category = 'nowon' "
        "AND source_board = '1001' AND post_sn = ANY(%s)",
        ([notice.post_sn for notice in notices],),
    ).fetchall()
    return {row[0] for row in rows}


def collect_and_save_nowon_scheduled(
    settings: NowonSettings,
    database: DatabaseSettings,
    *,
    mode: Literal["new", "refresh"],
) -> ScheduledNowonResult:
    """Process 50 API rows on first run, then 10 recent rows by schedule."""
    if mode not in ("new", "refresh"):
        raise ValueError("mode must be 'new' or 'refresh'")
    if settings.nowon_notice_api_key == "sample":
        raise ValueError("예약 모드의 최초 50건 조회에는 발급받은 API 키가 필요합니다.")
    conn = psycopg.connect(database.database_url, connect_timeout=5, autocommit=True)
    try:
        initial_baseline = not conn.execute(
            "SELECT EXISTS (SELECT 1 FROM notices WHERE category = 'nowon')",
        ).fetchone()[0]
        first = _fetch_api_page_with_retry(settings, 1, 50)
        target = min(50 if initial_baseline else 10, first.total_count)
        selected: list[RawNotice] = []
        seen: dict[str, RawNotice] = {}
        conflicts: set[str] = set()
        failed_ranges: list[tuple[int, int]] = []
        first_window_all_new = True
        found_boundary = False
        listing_complete = True
        pages_read = 0
        start, end = 1, 50
        page = first
        while True:
            pages_read += 1
            if page.total_count != first.total_count:
                listing_complete = False
                failed_ranges.append((start, end))
                break
            known = _known_nowon_ids(conn, page.notices) if mode == "new" else set()
            for notice in page.notices:
                previous = seen.get(notice.post_sn)
                if previous is not None:
                    if previous != notice:
                        conflicts.add(notice.post_sn)
                        listing_complete = False
                    continue
                seen[notice.post_sn] = notice
                if len(seen) <= target:
                    if initial_baseline or mode == "refresh" or notice.post_sn not in known:
                        selected.append(notice)
                    else:
                        first_window_all_new = False
                    if len(seen) == target and (
                        initial_baseline or mode == "refresh" or not first_window_all_new
                    ):
                        found_boundary = True
                        break
                elif notice.post_sn in known:
                    found_boundary = True
                    break
                else:
                    selected.append(notice)
            if found_boundary or end >= first.total_count:
                break
            start = end - 4
            end = min(start + 49, first.total_count)
            sleep(1)
            try:
                page = _fetch_api_page_with_retry(settings, start, end)
            except NowonSourceError as error:
                if error.rate_limited:
                    raise
                listing_complete = False
                failed_ranges.append((start, end))
                break
        if not found_boundary and not failed_ranges and len(seen) != first.total_count:
            listing_complete = False
        if pages_read > 1:
            sleep(1)
            try:
                latest = _fetch_api_page_with_retry(settings, 1, 1)
                if (
                    latest.total_count != first.total_count
                    or not latest.notices
                    or latest.notices[0].post_sn != first.notices[0].post_sn
                ):
                    listing_complete = False
                    failed_ranges.append((1, 1))
            except NowonSourceError as error:
                if error.rate_limited:
                    raise
                listing_complete = False
                failed_ranges.append((1, 1))
        chosen = tuple(selected)
    except (psycopg.Error, NowonSourceError):
        conn.close()
        raise
    saved_count, failures = (
        _save_notices(
            conn,
            chosen,
            settings,
            database.database_url,
            conflicts=conflicts,
            paced=True,
        )
        if chosen
        else (0, ())
    )
    if not chosen:
        conn.close()
    return ScheduledNowonResult(
        mode,
        first.total_count,
        len(chosen),
        saved_count,
        pages_read,
        initial_baseline,
        listing_complete,
        tuple(failed_ranges),
        failures,
    )
