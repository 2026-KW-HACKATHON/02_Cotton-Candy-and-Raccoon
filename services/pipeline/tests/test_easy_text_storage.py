"""Real local PostgreSQL: preservation, cache reuse, stale source and read policies."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from pipeline.glossary.easy_language import (
    DEFAULT_MODEL,
    PROMPT_VERSION,
    EasyLanguageAPIError,
    EasyLanguageResult,
    simplify_notice,
)
from pipeline.glossary.easy_language_service import simplify_and_store_notice
from pipeline.glossary.notice_service import load_notice_glossary_input
from pipeline.glossary.source import NoticeGlossaryInput, notice_content_revision
from pipeline.storage.notice_easy_text import (
    EasyTextStorageError,
    get_notice_easy_text,
    save_notice_easy_text,
)

_ROOT = Path(__file__).resolve().parents[3]
_NOW = datetime(2026, 10, 7, 9, tzinfo=UTC)


def _easy_db_connection():
    url = os.getenv("GLOSSARY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("GLOSSARY_TEST_DATABASE_URL 미설정: 로컬 DB 통합 테스트 생략")
    info = conninfo_to_dict(url)
    if (
        info.get("host") not in {"localhost", "127.0.0.1", "::1"}
        or info.get("hostaddr") not in {None, "127.0.0.1", "::1"}
        or not info.get("dbname", "").startswith("pipeline_glossary_test_")
    ):
        raise ValueError("쉬운말 검증은 전용 로컬 테스트 DB에서만 가능합니다.")
    conn = psycopg.connect(**info)
    try:
        assert conn.execute("select to_regclass('public.notices')").fetchone()[0] is None
        for role in ("anon", "authenticated"):
            if (
                conn.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone()
                is None
            ):
                conn.execute(f"create role {role} nologin")
        conn.execute("grant usage on schema public to anon, authenticated")
        for name in (
            "20260922053900_init.sql",
            "20260922053901_rls.sql",
            "20261007000000_notice_easy_text.sql",
            "20261007000004_notice_easy_text_scope.sql",
        ):
            conn.execute((_ROOT / "supabase/migrations" / name).read_text("utf-8"))
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def easy_db():
    yield from _easy_db_connection()


def _notice(conn):
    notice_id = conn.execute(
        "insert into public.notices "
        "(category, source_board, post_sn, title, body_html, registered_on, url) "
        "values ('nowon', '1001', 'easy-test', '참가자 모집', "
        "'<p>구비서류를 지참하세요. 참가비는 30,000원입니다.</p>', "
        "current_date, 'https://www.nowon.kr/test') returning id"
    ).fetchone()[0]
    return load_notice_glossary_input(conn, notice_id)


def _request(**kwargs):
    return json.dumps(
        {
            "changes": [
                {
                    "original": "구비서류를",
                    "replacement": "준비할 서류를",
                    "context": "구비서류를 지참하세요.",
                }
            ],
        },
        ensure_ascii=False,
    )


def _result(source, now=_NOW):
    return simplify_notice(source, api_key="fake", request=_request, clock=lambda: now)


def test_repeated_notice_read_and_conversion_have_zero_additional_api_calls(easy_db, monkeypatch):
    source = _notice(easy_db)
    request = MagicMock(side_effect=_request)
    result = simplify_and_store_notice(easy_db, source, api_key="fake", request=request)
    assert result.easy_text == source.text.replace("구비서류를", "준비할 서류를")
    assert get_notice_easy_text(easy_db, source.notice_id) == result
    monkeypatch.setattr(
        "pipeline.glossary.easy_language_service.load_gemini_api_key",
        MagicMock(side_effect=AssertionError("cached conversion must not read secrets")),
    )
    assert simplify_and_store_notice(easy_db, source, request=request) == result
    assert simplify_and_store_notice(easy_db, source, request=request) == result
    request.assert_called_once()
    assert load_notice_glossary_input(easy_db, source.notice_id).text == source.text


@pytest.mark.parametrize(
    "old_changes",
    [
        [],
        [
            {
                "original": "구비서류를",
                "replacement": "물건을",
                "context": "구비서류를 지참하세요.",
            }
        ],
    ],
)
def test_new_prompt_replaces_old_cache_once_then_reuses_it(easy_db, old_changes):
    source = _notice(easy_db)
    previous = simplify_notice(
        source,
        api_key="fake",
        request=lambda **kwargs: json.dumps({"changes": old_changes}, ensure_ascii=False),
        clock=lambda: _NOW,
    )
    previous = EasyLanguageResult.model_validate(
        {**previous.model_dump(), "prompt_version": "easy-language-v3"}
    )
    save_notice_easy_text(easy_db, previous)
    request = MagicMock(side_effect=_request)
    result = simplify_and_store_notice(
        easy_db,
        source,
        api_key="fake",
        request=request,
        clock=lambda: _NOW + timedelta(seconds=1),
    )
    assert result.prompt_version == PROMPT_VERSION != previous.prompt_version
    assert result.easy_text != source.text
    assert simplify_and_store_notice(easy_db, source, request=request) == result
    request.assert_called_once()


@pytest.mark.parametrize("generation", ["model", "prompt"])
@pytest.mark.parametrize("clock_ahead", [timedelta(), timedelta(hours=1)])
def test_new_generation_replaces_old_cache_despite_equal_or_earlier_clock(
    easy_db, monkeypatch, generation, clock_ahead
):
    source = _notice(easy_db)
    old_field = "model" if generation == "model" else "prompt_version"
    old_value = "gemini-previous-model" if generation == "model" else "easy-language-v3"
    previous = EasyLanguageResult.model_validate(
        {**_result(source, _NOW + clock_ahead).model_dump(), old_field: old_value}
    )
    save_notice_easy_text(easy_db, previous)
    request = MagicMock(side_effect=_request)

    current = simplify_and_store_notice(
        easy_db,
        source,
        api_key="fake",
        request=request,
        clock=lambda: _NOW,
    )

    assert current.model == DEFAULT_MODEL
    assert current.prompt_version == PROMPT_VERSION
    assert current.generated_at == _NOW <= previous.generated_at
    assert get_notice_easy_text(easy_db, source.notice_id) == current
    monkeypatch.setattr(
        "pipeline.glossary.easy_language_service.load_gemini_api_key",
        MagicMock(side_effect=AssertionError("current cache must not read API credentials")),
    )
    assert simplify_and_store_notice(easy_db, source, request=request) == current
    assert simplify_and_store_notice(easy_db, source, request=request) == current
    request.assert_called_once()


def test_failed_refresh_preserves_prior_result_and_callers_other_work(easy_db):
    source = _notice(easy_db)
    stored = _result(source)
    save_notice_easy_text(easy_db, stored)
    request = MagicMock(side_effect=EasyLanguageAPIError("secret"))
    with pytest.raises(EasyLanguageAPIError):
        simplify_and_store_notice(easy_db, source, refresh=True, api_key="fake", request=request)
    assert get_notice_easy_text(easy_db, source.notice_id) == stored
    assert load_notice_glossary_input(easy_db, source.notice_id) == source


def test_changed_parent_rejects_old_result_and_hides_stale_cache(easy_db):
    source = _notice(easy_db)
    result = _result(source)
    save_notice_easy_text(easy_db, result)
    easy_db.execute(
        "update public.notices set body_html = '<p>수정된 본문</p>' where id = %s",
        (source.notice_id,),
    )
    assert get_notice_easy_text(easy_db, source.notice_id) is None
    with pytest.raises(EasyTextStorageError, match="원문이 바뀌어"):
        save_notice_easy_text(easy_db, result)
    with pytest.raises(EasyTextStorageError, match="원문이 바뀌었습니다"):
        simplify_and_store_notice(easy_db, source, api_key="fake", request=MagicMock())


@pytest.mark.parametrize(
    "title,body",
    [
        ('한글 "따옴표" / 역슬래시\\\n😀', None),
        ("표제", '<p>\t\r\n한글\\"/</p>'),
        ("문자\u2028\u2029", "<p>원문</p>"),
    ],
)
def test_sql_revision_matches_python_exact_json(easy_db, title, body):
    computed = easy_db.execute(
        "select public.notice_easy_text_revision(%s, %s)", (title, body)
    ).fetchone()[0]
    assert computed == notice_content_revision(title, body)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_app_read_policy_excludes_hidden_and_outdated_notices(easy_db, role):
    source = _notice(easy_db)
    save_notice_easy_text(easy_db, _result(source))
    with easy_db.transaction():
        easy_db.execute(f"set local role {role}")
        assert easy_db.execute("select count(*) from public.notice_easy_texts").fetchone()[0] == 1
        easy_db.execute("reset role")
    easy_db.execute("update public.notices set is_visible = false")
    with easy_db.transaction():
        easy_db.execute(f"set local role {role}")
        assert easy_db.execute("select count(*) from public.notice_easy_texts").fetchone()[0] == 0
        easy_db.execute("reset role")
    easy_db.execute("update public.notices set is_visible = true, title = '수정된 제목'")
    with easy_db.transaction():
        easy_db.execute(f"set local role {role}")
        assert easy_db.execute("select count(*) from public.notice_easy_texts").fetchone()[0] == 0
        easy_db.execute("reset role")


def test_older_timestamp_cannot_replace_same_revision(easy_db):
    source = _notice(easy_db)
    recent = _result(source, _NOW)
    save_notice_easy_text(easy_db, recent)
    save_notice_easy_text(easy_db, _result(source, _NOW - timedelta(hours=1)))
    assert get_notice_easy_text(easy_db, source.notice_id) == recent


def test_wrong_text_cannot_be_saved_even_with_current_revision(easy_db):
    source = _notice(easy_db)
    wrong = NoticeGlossaryInput(
        notice_id=source.notice_id,
        notice_revision=source.notice_revision,
        text="다른 공지 구비서류를 지참하세요.",
    )
    with pytest.raises(EasyTextStorageError, match="DB 공지와 일치하지"):
        save_notice_easy_text(easy_db, _result(wrong))
    assert get_notice_easy_text(easy_db, source.notice_id) is None


@pytest.mark.parametrize("with_revision", [False, True])
def test_input_must_match_database_notice(easy_db, with_revision):
    source = _notice(easy_db)
    with pytest.raises(EasyTextStorageError, match="연결할 수 없습니다"):
        simplify_and_store_notice(
            easy_db,
            NoticeGlossaryInput(
                notice_id=source.notice_id,
                text="다른 공지",
                notice_revision=source.notice_revision if with_revision else None,
            ),
            api_key="fake",
        )
