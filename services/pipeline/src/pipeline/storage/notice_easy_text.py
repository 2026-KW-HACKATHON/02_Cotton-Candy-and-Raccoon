"""Persist validated Gemini replacements in a caller-owned transaction."""

from enum import Enum

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from pipeline.glossary.easy_language import EasyLanguageResult
from pipeline.glossary.source import notice_content_revision
from pipeline.transform.html_text import html_to_notice_text

_SELECT = """
select notice_id, notice_revision, source_hash, original_text, easy_text, changes,
       model, prompt_version, attempt_count, generated_at,
       body_text_present, attachment_content_included, dictionary_candidates
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
    "body_text_present",
    "attachment_content_included",
    "dictionary_candidates",
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


class _CacheTokenDefault(Enum):
    UNSPECIFIED = "unspecified"


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
    # Older workers could convert the title. Keep those rows for history, but
    # never return them as a body-only conversion or reuse a title-only result.
    if not isinstance(payload["easy_text"], str):
        raise EasyTextStorageError("저장된 쉬운말 결과의 형식이 올바르지 않습니다.")
    if not body or not payload["easy_text"].startswith(title + "\n"):
        return None
    scope = (payload["body_text_present"], payload["attachment_content_included"])
    if scope != (None, None) and scope != (bool(body), False):
        raise EasyTextStorageError("저장된 쉬운말 처리 범위가 DB 원문과 일치하지 않습니다.")
    # Old rows stay unknown in SQL until a validated service read fills them.
    # A backend read can already derive truthful scope from the verified parent.
    payload.update(original_title=title, body_text_present=True, attachment_content_included=False)
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


def fill_notice_easy_text_scope(
    conn: Connection, result: EasyLanguageResult, *, expected_cache_token: str
) -> bool:
    """Fill only a verified legacy snapshot's unknown scope; never call Gemini.

    The parent lock and raw row token protect against concurrent source changes
    and cache refreshes. False means the row was already known or has changed;
    callers must re-read rather than returning their previous snapshot.
    """
    notice_id = _notice_id(result.notice_id)
    if result.notice_revision is None or conn.autocommit:
        raise EasyTextStorageError("처리 범위 저장에는 원문 버전과 트랜잭션이 필요합니다.")
    if (
        not isinstance(expected_cache_token, str)
        or len(expected_cache_token) != 64
        or any(char not in "0123456789abcdef" for char in expected_cache_token)
    ):
        raise EasyTextStorageError("쉬운말 저장 상태 확인값이 올바르지 않습니다.")
    if conn.info.transaction_status == TransactionStatus.IDLE:
        conn.execute("select 1")
    with conn.transaction(), conn.cursor(row_factory=tuple_row) as cursor:
        cursor.execute(
            "select title, body_html from public.notices where id = %s for share", (notice_id,)
        )
        parent = cursor.fetchone()
        if parent is None or notice_content_revision(*parent) != result.notice_revision:
            raise EasyTextStorageError("공지 원문이 바뀌어 처리 범위를 보충하지 않았습니다.")
        title, body_html = parent
        body = html_to_notice_text(body_html)
        if result.original_text != title + ("\n" + body if body else ""):
            raise EasyTextStorageError("처리 범위를 확인할 DB 공지 원문이 일치하지 않습니다.")
        if not body or result.original_title != title:
            raise EasyTextStorageError("제목을 보존한 본문 결과에만 처리 범위를 보충합니다.")
        cursor.execute(
            "update public.notice_easy_texts "
            "set body_text_present = %s, attachment_content_included = false "
            "where notice_id = %s and notice_revision = %s and source_hash = %s "
            "and original_text = %s and body_text_present is null "
            "and attachment_content_included is null "
            f"and {_CACHE_TOKEN} = %s::text",
            (
                bool(body),
                notice_id,
                result.notice_revision,
                result.source_hash,
                result.original_text,
                expected_cache_token,
            ),
        )
        return cursor.rowcount == 1


def save_notice_easy_text(
    conn: Connection,
    result: EasyLanguageResult,
    *,
    expected_cache_token: str | None | _CacheTokenDefault = _CacheTokenDefault.UNSPECIFIED,
) -> None:
    """Save after validating source revision; preserve existing results on failure.

    Locks the parent notice through the caller's commit so collection cannot
    change it between revision validation and saving. No commit or close here.
    A model/prompt change needs the raw token captured before generation. It
    may replace that exact snapshot even with an earlier worker clock, but
    cannot overwrite work saved since then. Without a token, a different
    generation is preserved. Older timestamps cannot replace the same generation.
    Supplying a token also guards same-generation updates. Explicit None means
    that no cached result existed before generation; omission keeps the standalone
    timestamp/generation rules. A changed snapshot is preserved without a write.
    """
    try:
        result = EasyLanguageResult.model_validate(result.model_dump(mode="python"))
    except (AttributeError, TypeError, ValueError):
        raise EasyTextStorageError("쉬운말 저장 결과가 올바르지 않습니다.") from None
    notice_id = _notice_id(result.notice_id)
    if result.notice_revision is None:
        raise EasyTextStorageError("쉬운말 저장에는 수집 원문 버전이 필요합니다.")
    check_cache_token = expected_cache_token is not _CacheTokenDefault.UNSPECIFIED
    cache_token = expected_cache_token if check_cache_token else None
    if cache_token is not None and (
        not isinstance(cache_token, str)
        or len(cache_token) != 64
        or any(char not in "0123456789abcdef" for char in cache_token)
    ):
        raise EasyTextStorageError("쉬운말 저장 상태 확인값이 올바르지 않습니다.")
    if conn.autocommit:
        raise EasyTextStorageError("쉬운말 저장에는 autocommit이 꺼진 연결이 필요합니다.")
    if conn.info.transaction_status == TransactionStatus.IDLE:
        conn.execute("select 1")
    with conn.transaction(), conn.cursor(row_factory=tuple_row) as cursor:
        cursor.execute(
            "select title, body_html from public.notices where id = %s for update",
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
        # Serialize only the brief save phase. Checking under the parent lock
        # also handles a cached row being deleted while Gemini was running.
        if check_cache_token and get_notice_easy_text_cache_token(conn, notice_id) != cache_token:
            return
        if not body:
            raise EasyTextStorageError("본문 없는 공지는 쉬운말 성공 결과로 저장하지 않습니다.")
        if result.original_title is not None and result.original_title != title:
            raise EasyTextStorageError("쉬운말 결과의 원문 제목이 DB 공지와 일치하지 않습니다.")
        scope = (result.body_text_present, result.attachment_content_included)
        if scope != (None, None) and scope != (bool(body), False):
            raise EasyTextStorageError("쉬운말 처리 범위가 DB 원문과 일치하지 않습니다.")
        # Standalone JSON has unknown provenance. DB storage establishes it from
        # the locked parent, rather than trusting any caller-provided assertion.
        try:
            result = EasyLanguageResult.model_validate(
                {
                    **result.model_dump(),
                    "original_title": title,
                    "body_text_present": bool(body),
                    "attachment_content_included": False,
                }
            )
        except (TypeError, ValueError):
            raise EasyTextStorageError("쉬운말 결과의 본문 범위를 검증하지 못했습니다.") from None
        values = tuple(
            Jsonb([item.model_dump(mode="json") for item in getattr(result, name)])
            if name in {"changes", "dictionary_candidates"} and getattr(result, name) is not None
            else getattr(result, name)
            for name in _NAMES
        )
        cursor.execute(
            "insert into public.notice_easy_texts "
            "(notice_id, notice_revision, source_hash, original_text, easy_text, changes, "
            "model, prompt_version, attempt_count, generated_at, "
            "body_text_present, attachment_content_included, dictionary_candidates) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "on conflict (notice_id) do update set "
            "notice_revision = excluded.notice_revision, source_hash = excluded.source_hash, "
            "original_text = excluded.original_text, easy_text = excluded.easy_text, "
            "changes = excluded.changes, model = excluded.model, "
            "prompt_version = excluded.prompt_version, attempt_count = excluded.attempt_count, "
            "generated_at = excluded.generated_at, "
            "body_text_present = excluded.body_text_present, "
            "attachment_content_included = excluded.attachment_content_included, "
            "dictionary_candidates = excluded.dictionary_candidates "
            "where (notice_easy_texts.notice_revision != excluded.notice_revision "
            "or (notice_easy_texts.model = excluded.model "
            "and notice_easy_texts.prompt_version = excluded.prompt_version "
            "and notice_easy_texts.generated_at < excluded.generated_at) "
            "or ((notice_easy_texts.model != excluded.model "
            "or notice_easy_texts.prompt_version != excluded.prompt_version) "
            f"and {_CACHE_TOKEN} = %s::text)) "
            f"and (%s::boolean or {_CACHE_TOKEN} = %s::text)",
            (*values, cache_token, not check_cache_token, cache_token),
        )
        if cursor.rowcount == 1 and result.dictionary_candidates is not None:
            # An older worker omits this column, so SQL clears unchanged candidates
            # when source/conversion/generation metadata changes. Explicit [] and
            # identical nonempty candidates are indistinguishable at the trigger.
            # Restore our validated payload only after a successful upsert while
            # its row lock is still held; a losing CAS must never restore anything.
            cursor.execute(
                "update public.notice_easy_texts set dictionary_candidates = %s "
                "where notice_id = %s and dictionary_candidates is null",
                (
                    Jsonb([item.model_dump(mode="json") for item in result.dictionary_candidates]),
                    notice_id,
                ),
            )
        # An unchanged scope across a source change is cleared by the migration
        # trigger to protect old workers; restore it after exact source validation.
        token = get_notice_easy_text_cache_token(conn, notice_id)
        if token is not None:
            fill_notice_easy_text_scope(conn, result, expected_cache_token=token)
