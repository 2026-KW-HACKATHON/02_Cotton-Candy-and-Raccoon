"""Persist validated Gemini replacements in a caller-owned transaction."""

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from pipeline.glossary.easy_language import EasyLanguageResult
from pipeline.glossary.source import notice_content_revision
from pipeline.transform.html_text import html_to_notice_text

_SELECT = """
select notice_id, notice_revision, source_hash, original_text, easy_text, changes,
       model, prompt_version, attempt_count, generated_at
from public.notice_easy_texts where notice_id = %s
"""
_NAMES = (
    "notice_id",
    "notice_revision",
    "source_hash",
    "original_text",
    "easy_text",
    "changes",
    "model",
    "prompt_version",
    "attempt_count",
    "generated_at",
)
# Hash the raw row so old prompt results need not pass today's result validator.
# Epoch time keeps the token identical across connections with different time zones.
_CACHE_TOKEN = """
encode(sha256(convert_to(
    ((to_jsonb(notice_easy_texts) - 'generated_at') ||
     jsonb_build_object('generated_at', extract(epoch from notice_easy_texts.generated_at)))::text,
    'UTF8'
)), 'hex')
"""


class EasyTextStorageError(RuntimeError):
    """A saved result is corrupt or belongs to an obsolete notice revision."""


def _notice_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EasyTextStorageError("쉬운말 저장에는 양의 공지 ID가 필요합니다.")
    return value


def get_notice_easy_text(
    conn: Connection,
    notice_id: int,
    *,
    source_hash: str | None = None,
    notice_revision: str | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
) -> EasyLanguageResult | None:
    """Return an intact current snapshot; never connect to an external API."""
    notice_id = _notice_id(notice_id)
    with conn.cursor(row_factory=tuple_row) as cursor:
        cursor.execute(_SELECT, (notice_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        if len(row) != len(_NAMES):
            raise EasyTextStorageError("쉬운말 저장 결과의 형식이 올바르지 않습니다.")
        payload = dict(zip(_NAMES, row, strict=True))
        # Old prompt/model generations may be unreadable under current rules.
        if any(
            expected is not None and payload[name] != expected
            for name, expected in (
                ("source_hash", source_hash),
                ("notice_revision", notice_revision),
                ("model", model),
                ("prompt_version", prompt_version),
            )
        ):
            return None
        cursor.execute("select title, body_html from public.notices where id = %s", (notice_id,))
        parent = cursor.fetchone()
    if parent is None or notice_content_revision(*parent) != payload["notice_revision"]:
        return None
    title, body_html = parent
    body = html_to_notice_text(body_html)
    if payload["original_text"] != title + ("\n" + body if body else ""):
        raise EasyTextStorageError("저장된 쉬운말 원문이 DB 공지와 일치하지 않습니다.")
    try:
        result = EasyLanguageResult.model_validate(payload)
        if result.notice_id != notice_id:
            raise ValueError("wrong notice")
        return result
    except (TypeError, ValueError):
        raise EasyTextStorageError("저장된 쉬운말 결과를 검증하지 못했습니다.") from None


def get_notice_easy_text_cache_token(conn: Connection, notice_id: int) -> str | None:
    """Capture the raw saved state before generation, without validating its old prompt.

    None means no result is stored. Keep this token inside the backend; it is
    only permission to replace the exact snapshot that the caller read.
    """
    notice_id = _notice_id(notice_id)
    with conn.cursor(row_factory=tuple_row) as cursor:
        cursor.execute(
            f"select {_CACHE_TOKEN} from public.notice_easy_texts where notice_id = %s",
            (notice_id,),
        )
        row = cursor.fetchone()
    return row[0] if row is not None else None


def save_notice_easy_text(
    conn: Connection,
    result: EasyLanguageResult,
    *,
    expected_cache_token: str | None = None,
) -> None:
    """Save after validating source revision; preserve existing results on failure.

    Locks the parent notice through the caller's commit so collection cannot
    change it between revision validation and saving. No commit or close here.
    A model/prompt change needs the raw token captured before generation. It
    may replace that exact snapshot even with an earlier worker clock, but
    cannot overwrite work saved since then. Without a token, a different
    generation is preserved. Older timestamps cannot replace the same generation.
    """
    try:
        result = EasyLanguageResult.model_validate(result.model_dump(mode="python"))
    except (AttributeError, TypeError, ValueError):
        raise EasyTextStorageError("쉬운말 저장 결과가 올바르지 않습니다.") from None
    notice_id = _notice_id(result.notice_id)
    if result.notice_revision is None:
        raise EasyTextStorageError("쉬운말 저장에는 수집 원문 버전이 필요합니다.")
    if expected_cache_token is not None and (
        not isinstance(expected_cache_token, str)
        or len(expected_cache_token) != 64
        or any(char not in "0123456789abcdef" for char in expected_cache_token)
    ):
        raise EasyTextStorageError("쉬운말 저장 상태 확인값이 올바르지 않습니다.")
    if conn.autocommit:
        raise EasyTextStorageError("쉬운말 저장에는 autocommit이 꺼진 연결이 필요합니다.")
    if conn.info.transaction_status == TransactionStatus.IDLE:
        conn.execute("select 1")
    with conn.transaction(), conn.cursor(row_factory=tuple_row) as cursor:
        cursor.execute(
            "select title, body_html from public.notices where id = %s for share",
            (notice_id,),
        )
        parent = cursor.fetchone()
        if parent is None or notice_content_revision(*parent) != result.notice_revision:
            raise EasyTextStorageError("공지 원문이 바뀌어 이전 쉬운말 결과를 저장하지 않았습니다.")
        title, body_html = parent
        body = html_to_notice_text(body_html)
        current_text = title + ("\n" + body if body else "")
        if result.original_text != current_text:
            raise EasyTextStorageError("쉬운말 원문이 저장된 DB 공지와 일치하지 않습니다.")
        values = tuple(
            Jsonb([item.model_dump(mode="json") for item in result.changes])
            if name == "changes"
            else getattr(result, name)
            for name in _NAMES
        )
        cursor.execute(
            "insert into public.notice_easy_texts "
            "(notice_id, notice_revision, source_hash, original_text, easy_text, changes, "
            "model, prompt_version, attempt_count, generated_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "on conflict (notice_id) do update set "
            "notice_revision = excluded.notice_revision, source_hash = excluded.source_hash, "
            "original_text = excluded.original_text, easy_text = excluded.easy_text, "
            "changes = excluded.changes, model = excluded.model, "
            "prompt_version = excluded.prompt_version, attempt_count = excluded.attempt_count, "
            "generated_at = excluded.generated_at "
            "where notice_easy_texts.notice_revision != excluded.notice_revision "
            "or (notice_easy_texts.model = excluded.model "
            "and notice_easy_texts.prompt_version = excluded.prompt_version "
            "and notice_easy_texts.generated_at < excluded.generated_at) "
            "or ((notice_easy_texts.model != excluded.model "
            "or notice_easy_texts.prompt_version != excluded.prompt_version) "
            f"and {_CACHE_TOKEN} = %s::text)",
            (*values, expected_cache_token),
        )
