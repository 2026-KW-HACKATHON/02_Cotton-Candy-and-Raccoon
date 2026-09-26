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
from pipeline.sources.nowon_api import NowonCollection, collect_all
from pipeline.sources.nowon_page import NowonPageError, NowonPageMissing, fetch_notice_page
from pipeline.storage.notice_bundle import save_notice_with_files
from pipeline.transform.nowon import TransformError, transform_nowon_notice

FailureStage = Literal[
    "listing_conflict", "page_missing", "page", "attachments", "transform", "storage",
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


def _fetch_page_with_retry(
    notice: RawNotice, settings: NowonSettings,
) -> tuple[str, str]:
    for attempt in range(3):
        try:
            return fetch_notice_page(notice, settings)
        except NowonPageError as error:
            if not error.retryable or attempt == 2:
                raise
            sleep(0.2 * 2 ** attempt)
    raise AssertionError("retry loop must return or raise")


def _prepare_notice(
    notice: RawNotice, settings: NowonSettings,
) -> PreparedNotice:
    record = transform_nowon_notice(notice)
    try:
        body_files = extract_files(notice)
    except AttachmentError as error:
        return PreparedNotice(
            record, [], NoticeFailure(notice.post_sn, "attachments", error.code),
        )
    try:
        page_url, page_html = _fetch_page_with_retry(notice, settings)
    except NowonPageMissing:
        return PreparedNotice(
            record, [],
            NoticeFailure(notice.post_sn, "page_missing", "source_page_missing"),
        )
    except NowonPageError:
        return PreparedNotice(
            record, [],
            NoticeFailure(notice.post_sn, "page", "page_unavailable"),
        )
    try:
        page_files = extract_page_files(notice, page_html, page_url)
        files = merge_files(body_files, page_files)
    except AttachmentError as error:
        return PreparedNotice(
            record, [], NoticeFailure(notice.post_sn, "attachments", error.code),
        )
    return PreparedNotice(record, files, None)


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
    conflicts = set(listing.conflicting_post_sns)
    failed_pages = tuple(
        (page.start_index, page.end_index)
        for page in listing.pages if not page.succeeded
    )
    failures: list[NoticeFailure] = []
    saved_count = 0
    if not notices:
        return CollectNowonResult(
            listing.total_count, len(listing.notices), 0, 0,
            listing.complete, limited, failed_pages, (),
        )

    # autocommit keeps save_notice_with_files' transaction scoped to one notice.
    conn = psycopg.connect(database.database_url, connect_timeout=5, autocommit=True)
    try:
        for index, notice in enumerate(notices):
            if notice.post_sn in conflicts:
                failures.append(NoticeFailure(
                    notice.post_sn, "listing_conflict", "listing_conflict",
                ))
                continue
            try:
                prepared = _prepare_notice(notice, settings)
            except TransformError:
                failures.append(NoticeFailure(notice.post_sn, "transform", "invalid_notice"))
                continue
            if prepared.failure is not None:
                failures.append(prepared.failure)
                continue
            if conn.closed:
                try:
                    conn = psycopg.connect(
                        database.database_url, connect_timeout=5, autocommit=True,
                    )
                except psycopg.Error:
                    failures.extend(
                        NoticeFailure(remaining.post_sn, "storage", "db_unavailable")
                        for remaining in notices[index:]
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

    return CollectNowonResult(
        listing.total_count, len(listing.notices), len(notices), saved_count,
        listing.complete, limited, failed_pages, tuple(failures),
    )
