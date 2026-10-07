"""Local PostgreSQL: body-only conversions and title-preserving public reads."""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from psycopg.types.json import Jsonb
from test_collect_easy_text_storage import committed_easy_db as committed_easy_db
from test_easy_text_storage import easy_db as easy_db
from test_easy_text_storage import service_db as service_db

from pipeline.glossary.easy_language import DEFAULT_MODEL, PROMPT_VERSION, NoNoticeBodyError
from pipeline.glossary.easy_language_service import simplify_and_store_notice
from pipeline.glossary.notice_service import load_notice_glossary_input
from pipeline.glossary.source import source_hash
from pipeline.storage.notice_easy_text import get_notice_easy_text

_NOW = datetime(2026, 10, 7, 9, tzinfo=UTC)


def _notice(conn, *, title="익일 안내", body_html="<p>익일 방문하세요.</p>"):
    notice_id = conn.execute(
        "insert into public.notices "
        "(category, source_board, post_sn, title, body_html, registered_on, url) "
        "values ('nowon', '1001', 'title-test', %s, %s, current_date, "
        "'https://www.nowon.kr/title-test') returning id",
        (title, body_html),
    ).fetchone()[0]
    return load_notice_glossary_input(conn, notice_id)


def _insert_legacy(conn, source, *, easy_text, changes):
    """Emulate an old owner worker without going through current Python validation."""
    conn.execute(
        "insert into public.notice_easy_texts "
        "(notice_id, notice_revision, source_hash, original_text, easy_text, changes, "
        "model, prompt_version, attempt_count, generated_at) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s)",
        (
            source.notice_id,
            source.notice_revision,
            source_hash(source),
            source.text,
            easy_text,
            Jsonb(changes),
            DEFAULT_MODEL,
            "easy-language-v6",
            _NOW,
        ),
    )


def _public_rows(conn, role, notice_id):
    with conn.transaction():
        conn.execute(f"set local role {role}")
        rows = conn.execute(
            "select easy_text from public.notice_easy_texts where notice_id = %s",
            (notice_id,),
        ).fetchall()
        conn.execute("reset role")
    return rows


def test_gemini_receives_body_only_and_exact_title_is_retained(service_db):
    title = "익일 안내 😀\n접수 공고"
    source = _notice(service_db, title=title)
    request = MagicMock(
        return_value=json.dumps(
            {
                "changes": [
                    {"original": "익일", "replacement": "다음 날", "context": "익일 방문하세요."}
                ]
            },
            ensure_ascii=False,
        )
    )

    service_db.commit()
    result = simplify_and_store_notice(
        service_db, source, api_key="fake", request=request, clock=lambda: _NOW
    )

    request.assert_called_once()
    assert request.call_args.kwargs["notice_text"] == "익일 방문하세요."
    assert result.original_text == title + "\n익일 방문하세요."
    assert result.easy_text == title + "\n다음 날 방문하세요."
    assert result.changes[0].start == len(title) + 1
    assert result.prompt_version == PROMPT_VERSION
    assert get_notice_easy_text(service_db, source.notice_id) == result
    for role in ("anon", "authenticated"):
        assert _public_rows(service_db, role, source.notice_id) == [(result.easy_text,)]
    assert load_notice_glossary_input(service_db, source.notice_id).text == source.text


@pytest.mark.parametrize(
    "body_html",
    [None, "<p> \t\n </p>", "<img src='notice.png' alt='공고 이미지'>"],
)
def test_title_only_notice_makes_no_api_call_and_stores_no_result(
    service_db, monkeypatch, body_html
):
    source = _notice(service_db, body_html=body_html)
    request = MagicMock(side_effect=AssertionError("a title-only notice must not call Gemini"))
    key_loader = MagicMock(side_effect=AssertionError("no body must not read the API key"))
    monkeypatch.setattr("pipeline.glossary.easy_language_service.load_gemini_api_key", key_loader)

    service_db.commit()
    with pytest.raises(NoNoticeBodyError):
        simplify_and_store_notice(service_db, source, request=request)

    request.assert_not_called()
    key_loader.assert_not_called()
    assert service_db.execute("select count(*) from public.notice_easy_texts").fetchone()[0] == 0


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_legacy_title_change_is_retained_privately_and_hidden_from_reads(easy_db, role):
    source = _notice(easy_db)
    _insert_legacy(
        easy_db,
        source,
        easy_text="다음 날 안내\n익일 방문하세요.",
        changes=[
            {
                "start": 0,
                "end": 2,
                "original": "익일",
                "replacement": "다음 날",
                "context": "익일 안내",
            }
        ],
    )

    assert get_notice_easy_text(easy_db, source.notice_id) is None
    assert _public_rows(easy_db, role, source.notice_id) == []
    assert easy_db.execute("select count(*) from public.notice_easy_texts").fetchone()[0] == 1


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_legacy_title_only_result_is_retained_privately_and_hidden_from_reads(easy_db, role):
    source = _notice(easy_db, body_html=None)
    _insert_legacy(easy_db, source, easy_text=source.text, changes=[])

    assert get_notice_easy_text(easy_db, source.notice_id) is None
    assert _public_rows(easy_db, role, source.notice_id) == []
    assert easy_db.execute("select count(*) from public.notice_easy_texts").fetchone()[0] == 1


@pytest.mark.parametrize(
    "changes",
    [
        None,
        {},
        "not an array",
        [None],
        [False],
        [{}],
        [{"start": None}],
        [{"start": True}],
        [{"start": "999999999999999999999999"}],
        [{"start": {"offset": 3}}],
        [{"start": -1}],
        [{"start": 2}],
        [{"start": 3}, {"start": 0}],
    ],
)
def test_sql_title_helper_rejects_malformed_or_title_offsets_without_cast_failure(easy_db, changes):
    assert (
        easy_db.execute(
            "select public.notice_easy_text_preserves_title(%s, %s, %s, %s)",
            ("제목", "제목\n본문", "제목\n바뀐 본문", Jsonb(changes)),
        ).fetchone()[0]
        is False
    )


@pytest.mark.parametrize(
    "title,original,easy",
    [
        (None, "제목\n본문", "제목\n본문"),
        ("제목", None, "제목\n본문"),
        ("제목", "제목\n본문", None),
        ("제목", "다른 제목\n본문", "제목\n본문"),
        ("제목", "제목\n본문", "다른 제목\n본문"),
        ("제목", "제목", "제목"),
        ("제목", "제목\n", "제목\n본문"),
        ("제목", "제목\n본문", "제목\n"),
        ("제목", "제목\n \t\n", "제목\n본문"),
    ],
)
def test_sql_title_helper_requires_exact_title_prefix_and_nonblank_body(
    easy_db, title, original, easy
):
    assert (
        easy_db.execute(
            "select public.notice_easy_text_preserves_title(%s, %s, %s, '[]'::jsonb)",
            (title, original, easy),
        ).fetchone()[0]
        is False
    )


@pytest.mark.parametrize("start", [3, 10**100])
def test_sql_title_helper_uses_unicode_character_offsets_without_integer_overflow(easy_db, start):
    assert (
        easy_db.execute(
            "select public.notice_easy_text_preserves_title(%s, %s, %s, %s)",
            ("😀제", "😀제\n본문", "😀제\n바뀐 본문", Jsonb([{"start": start}])),
        ).fetchone()[0]
        is True
    )
