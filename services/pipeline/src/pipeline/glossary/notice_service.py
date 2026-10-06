"""Connect existing notice text, dictionary caching and saved term-only results."""

from collections.abc import Callable
from datetime import datetime

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.rows import tuple_row

from pipeline.glossary.config import GlossarySettings
from pipeline.glossary.document import RULES_VERSION, NoticeGlossaryResult
from pipeline.glossary.process import process_notice_glossary
from pipeline.glossary.service import LookupClient, query_glossary
from pipeline.glossary.source import NoticeGlossaryInput, notice_content_revision, source_hash
from pipeline.storage.glossary import get_glossary, save_glossary
from pipeline.storage.notice_glossary import (
    NoticeGlossaryStorageError,
    get_notice_glossary,
    save_notice_glossary,
)
from pipeline.transform.html_text import html_to_notice_text


def load_notice_glossary_input(conn: Connection, notice_id: int) -> NoticeGlossaryInput:
    """Read the saved title/body without updating the collected original HTML.

    Positions refer to the returned title + newline + plain body, not HTML bytes.
    Already extracted attachment text can instead be passed directly in an input.
    """
    if isinstance(notice_id, bool) or not isinstance(notice_id, int) or notice_id < 1:
        raise ValueError("공지 ID는 양의 정수여야 합니다.")
    with conn.cursor(row_factory=tuple_row) as cursor:
        cursor.execute("select title, body_html from public.notices where id = %s", (notice_id,))
        row = cursor.fetchone()
    if row is None:
        raise ValueError("지정한 공지를 찾을 수 없습니다.")
    title, body_html = row
    body = html_to_notice_text(body_html)
    text = title + ("\n" + body if body else "")
    return NoticeGlossaryInput(
        notice_id=notice_id,
        text=text,
        notice_revision=notice_content_revision(title, body_html),
    )


def process_and_store_notice_glossary(
    conn: Connection,
    source: NoticeGlossaryInput,
    *,
    max_queries: int = 100,
    refresh: bool = False,
    settings: GlossarySettings | None = None,
    onterm_client: LookupClient | None = None,
    opendict_client: LookupClient | None = None,
    clock: Callable[[], datetime] | None = None,
) -> NoticeGlossaryResult:
    """Read/resume matching work and save atomically; caller owns commit/rollback.

    max_queries counts different uncached lookup words, not individual provider
    HTTP requests. A lookup may require multiple providers/pages/detail calls.
    """
    if source.notice_id is None:
        raise ValueError("DB 저장에는 공지 ID가 필요합니다.")
    if isinstance(max_queries, bool) or not isinstance(max_queries, int) or max_queries < 1:
        raise ValueError("한 번에 조회할 후보 수는 양의 정수여야 합니다.")
    if conn.autocommit:
        raise ValueError("용어 처리 저장에는 autocommit이 꺼진 연결이 필요합니다.")
    if source.notice_revision is not None:
        current = load_notice_glossary_input(conn, source.notice_id)
        if current.notice_revision != source.notice_revision:
            raise NoticeGlossaryStorageError("공지 원문이 바뀌어 이전 원문 처리를 중단했습니다.")
    previous = get_notice_glossary(
        conn,
        source.notice_id,
        source_hash=source_hash(source),
        rules_version=RULES_VERSION,
    )
    if previous is not None and previous.notice_revision != source.notice_revision:
        previous = None
    if previous is not None and previous.status == "completed" and not refresh:
        return previous.model_copy(update={"new_query_count": 0})

    def lookup(query):
        return query_glossary(
            query,
            settings=settings,
            onterm_client=onterm_client,
            opendict_client=opendict_client,
            clock=clock,
        )

    result = process_notice_glossary(
        source,
        lookup,
        cached_lookup=lambda query: get_glossary(conn, query),
        previous=None if refresh else previous,
        max_queries=max_queries,
        clock=clock,
    )
    # Start an outer implicit transaction before the savepoint, never auto-commit it.
    if conn.info.transaction_status == TransactionStatus.IDLE:
        conn.execute("select 1")
    with conn.transaction():
        for outcome in sorted(result.queries, key=lambda item: item.query):
            if outcome.lookup is not None and not outcome.lookup.from_cache:
                save_glossary(conn, outcome.lookup)
        save_notice_glossary(conn, result)
        stored = get_notice_glossary(
            conn,
            source.notice_id,
            source_hash=result.source_hash,
            rules_version=result.rules_version,
        )
        if stored is None:
            raise RuntimeError("공지 용어 결과 저장 후 재조회를 확인하지 못했습니다.")
        if stored.notice_revision != source.notice_revision:
            raise NoticeGlossaryStorageError("저장 후 공지 원문 버전을 확인하지 못했습니다.")
    return stored
