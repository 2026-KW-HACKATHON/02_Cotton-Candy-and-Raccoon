"""Prepare or atomically store one Seoul notice; no file downloads/Gemini calls."""

import httpx
import psycopg

from pipeline.attachments.seoul_html import extract_files
from pipeline.config import DatabaseSettings, SeoulNewsSettings
from pipeline.models import FileRecord, NoticeRecord, RawSeoulNotice
from pipeline.sources.seoul_api import collect_one
from pipeline.storage.notice_bundle import save_notice_with_files
from pipeline.transform.seoul import transform_seoul_notice


def prepare_one(
    settings: SeoulNewsSettings,
    *,
    api_transport: httpx.BaseTransport | None = None,
    source_board: str | None = None,
    index: int = 1,
) -> tuple[NoticeRecord, tuple[FileRecord, ...]]:
    notice = collect_one(settings, transport=api_transport, source_board=source_board, index=index)
    return prepare_notice(notice)


def prepare_notice(
    notice: RawSeoulNotice,
) -> tuple[NoticeRecord, tuple[FileRecord, ...]]:
    """Use API HTML only: no original-page requests or file downloads."""
    record = transform_seoul_notice(notice)
    return record, extract_files(record)


class SeoulStorageError(ValueError):
    """Safe storage error without database credentials or raw SQL details."""


def collect_and_save_one(
    settings: SeoulNewsSettings,
    database: DatabaseSettings,
    *,
    source_board: str | None = None,
    index: int = 1,
    api_transport: httpx.BaseTransport | None = None,
) -> tuple[int, NoticeRecord, tuple[FileRecord, ...]]:
    # All remote reads and validation finish before opening a DB transaction.
    record, files = prepare_one(
        settings,
        source_board=source_board,
        index=index,
        api_transport=api_transport,
    )
    try:
        with psycopg.connect(database.database_url, connect_timeout=5) as conn:
            notice_id = save_notice_with_files(conn, record, files)
    except (psycopg.Error, ValueError):
        raise SeoulStorageError("서울시 DB 저장 실패: 연결 또는 저장 작업을 확인하세요.") from None
    return notice_id, record, files
