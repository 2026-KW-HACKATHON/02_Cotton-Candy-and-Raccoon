"""Reuse a saved notice conversion before requesting Gemini again."""

from collections.abc import Callable
from datetime import datetime

from psycopg import Connection
from psycopg.pq import TransactionStatus

from pipeline.glossary.easy_language import (
    DEFAULT_MODEL,
    PROMPT_VERSION,
    EasyLanguageResult,
    simplify_notice,
)
from pipeline.glossary.notice_service import load_notice_glossary_input
from pipeline.glossary.source import NoticeGlossaryInput, source_hash
from pipeline.storage.notice_easy_text import (
    EasyTextStorageError,
    get_notice_easy_text,
    get_notice_easy_text_cache_token,
    save_notice_easy_text,
)
from pipeline.transform.gemini_prompt import load_gemini_api_key


def simplify_and_store_notice(
    conn: Connection,
    source: NoticeGlossaryInput,
    *,
    refresh: bool = False,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    request: Callable[..., str] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> EasyLanguageResult:
    """Read cached conversion or generate/save once; never call a dictionary.

    Serialize duplicate first requests for one notice. A matching cached result
    needs neither API credentials nor network access. Caller owns the transaction.
    """
    if source.notice_id is None:
        raise ValueError("쉬운말 DB 저장에는 공지 ID가 필요합니다.")
    if conn.autocommit:
        raise EasyTextStorageError("쉬운말 처리에는 autocommit이 꺼진 연결이 필요합니다.")
    if conn.info.transaction_status == TransactionStatus.IDLE:
        conn.execute("select 1")
    with conn.transaction():
        conn.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 2026100702))",
            (f"notice-easy-text:{source.notice_id}",),
        )
        current = load_notice_glossary_input(conn, source.notice_id)
        if source.notice_revision is not None and current.notice_revision != source.notice_revision:
            raise EasyTextStorageError("공지 원문이 바뀌었습니다. 최신 공지로 다시 요청하세요.")
        if source.text != current.text:
            raise EasyTextStorageError("저장할 입력을 DB 공지 원문과 연결할 수 없습니다.")
        source = current
        expected_cache_token = get_notice_easy_text_cache_token(conn, source.notice_id)
        if not refresh:
            cached = get_notice_easy_text(
                conn,
                source.notice_id,
                source_hash=source_hash(source),
                notice_revision=source.notice_revision,
                model=model,
                prompt_version=PROMPT_VERSION,
            )
            if cached is not None:
                return cached
        result = simplify_notice(
            source,
            api_key=api_key if api_key is not None else load_gemini_api_key(),
            model=model,
            request=request,
            clock=clock,
        )
        save_notice_easy_text(conn, result, expected_cache_token=expected_cache_token)
        saved = get_notice_easy_text(
            conn,
            source.notice_id,
            source_hash=result.source_hash,
            notice_revision=result.notice_revision,
            model=result.model,
            prompt_version=result.prompt_version,
        )
        if saved is None:
            raise EasyTextStorageError("쉬운말 저장 후 재조회를 확인하지 못했습니다.")
    return saved
