"""Processing scope stays truthful across DB saves, legacy caches and source edits."""

import json
from datetime import UTC, datetime
from hashlib import sha256
from unittest.mock import MagicMock

import psycopg
import pytest
from psycopg.types.json import Jsonb
from pydantic import ValidationError
from test_easy_text_storage import _easy_db_connection

from pipeline.glossary.easy_language import EasyLanguageResult, simplify_notice
from pipeline.glossary.easy_language_service import simplify_and_store_notice
from pipeline.glossary.notice_service import load_notice_glossary_input
from pipeline.glossary.source import NoticeGlossaryInput, StoredNoticeInput, notice_content_revision
from pipeline.storage.notice_easy_text import (
    EasyTextStorageError,
    fill_notice_easy_text_scope,
    get_notice_easy_text,
    get_notice_easy_text_cache_token,
    save_notice_easy_text,
)

_NOW = datetime(2026, 10, 7, 9, tzinfo=UTC)


@pytest.fixture
def easy_db():
    yield from _easy_db_connection()


def _insert_notice(conn, body_html: str | None, *, title: str = "익일 안내") -> StoredNoticeInput:
    notice_id = conn.execute(
        "insert into public.notices "
        "(category, source_board, post_sn, title, body_html, registered_on, url) "
        "values ('nowon', '1001', 'scope-test', %s, %s, current_date, "
        "'https://www.nowon.kr/scope-test') returning id",
        (title, body_html),
    ).fetchone()[0]
    return load_notice_glossary_input(conn, notice_id)


def _request(**_kwargs: object) -> str:
    return json.dumps({"changes": []}, ensure_ascii=False)


def _result(source: NoticeGlossaryInput) -> EasyLanguageResult:
    return simplify_notice(source, api_key="fake", request=_request, clock=lambda: _NOW)


def _raw_scope(conn, notice_id: int) -> tuple[bool | None, bool | None]:
    return conn.execute(
        "select body_text_present, attachment_content_included "
        "from public.notice_easy_texts where notice_id = %s",
        (notice_id,),
    ).fetchone()


@pytest.mark.parametrize(
    "body_html,body_present,expected_text",
    [
        (None, False, "익일 안내"),
        ("<img src='notice.png' alt='공고 이미지'>", False, "익일 안내"),
        ("<a href='/file.pdf'>PDF 첨부 이름</a>", False, "익일 안내"),
        (
            "<p>방문 신청하세요.</p><a href='/file.pdf'>PDF 첨부 이름</a>",
            True,
            "익일 안내\n방문 신청하세요.",
        ),
    ],
)
def test_db_loader_identifies_text_only_scope(
    easy_db, body_html: str | None, body_present: bool, expected_text: str
) -> None:
    source = _insert_notice(easy_db, body_html)

    assert isinstance(source, StoredNoticeInput)
    assert source.text == expected_text
    assert source.body_text_present is body_present
    assert "PDF 첨부 이름" not in source.text


def test_direct_json_cannot_claim_processing_scope() -> None:
    with pytest.raises(ValidationError):
        NoticeGlossaryInput.model_validate({"text": "익일 안내", "body_text_present": False})
    result = _result(NoticeGlossaryInput(text="익일 안내"))
    assert (result.body_text_present, result.attachment_content_included) == (None, None)


@pytest.mark.parametrize("body_html,expected", [(None, False), ("<p>본문</p>", True)])
def test_fresh_save_and_cache_publish_known_scope_without_second_api_call(
    easy_db, monkeypatch, body_html: str | None, expected: bool
) -> None:
    source = _insert_notice(easy_db, body_html)
    request = MagicMock(side_effect=_request)

    first = simplify_and_store_notice(easy_db, source, api_key="fake", request=request)
    assert (first.body_text_present, first.attachment_content_included) == (expected, False)
    assert _raw_scope(easy_db, source.notice_id) == (expected, False)
    assert get_notice_easy_text(easy_db, source.notice_id) == first

    monkeypatch.setattr(
        "pipeline.glossary.easy_language_service.load_gemini_api_key",
        MagicMock(side_effect=AssertionError("cache must not read the key")),
    )
    assert simplify_and_store_notice(easy_db, source, request=request) == first
    request.assert_called_once()


def test_title_replacement_does_not_mislabel_body_scope(easy_db) -> None:
    source = _insert_notice(easy_db, "<p>본문</p>")

    def title_change(**_kwargs: object) -> str:
        return json.dumps(
            {"changes": [{"original": "익일", "replacement": "다음 날", "context": "익일 안내"}]},
            ensure_ascii=False,
        )

    saved = simplify_and_store_notice(
        easy_db, source, api_key="fake", request=title_change, clock=lambda: _NOW
    )

    assert saved.original_text == "익일 안내\n본문"
    assert saved.easy_text == "다음 날 안내\n본문"
    assert _raw_scope(easy_db, source.notice_id) == (True, False)


def test_getter_derives_legacy_scope_without_mutating_sql(easy_db) -> None:
    source = _insert_notice(easy_db, "<p>본문</p>")
    save_notice_easy_text(easy_db, _result(source))
    easy_db.execute(
        "update public.notice_easy_texts set body_text_present = null, "
        "attachment_content_included = null where notice_id = %s",
        (source.notice_id,),
    )

    cached = get_notice_easy_text(easy_db, source.notice_id)

    assert (cached.body_text_present, cached.attachment_content_included) == (True, False)
    assert _raw_scope(easy_db, source.notice_id) == (None, None)


@pytest.mark.parametrize("body_html,expected", [(None, False), ("<p>본문</p>", True)])
def test_legacy_unknown_scope_is_filled_on_cache_hit_without_gemini(
    easy_db, monkeypatch, body_html: str | None, expected: bool
) -> None:
    source = _insert_notice(easy_db, body_html)
    save_notice_easy_text(easy_db, _result(source))
    easy_db.execute(
        "update public.notice_easy_texts set body_text_present = null, "
        "attachment_content_included = null where notice_id = %s",
        (source.notice_id,),
    )
    request = MagicMock(side_effect=AssertionError("legacy cache must not call Gemini"))
    monkeypatch.setattr(
        "pipeline.glossary.easy_language_service.load_gemini_api_key",
        MagicMock(side_effect=AssertionError("legacy cache must not read the key")),
    )

    cached = simplify_and_store_notice(easy_db, source, request=request)

    assert (cached.body_text_present, cached.attachment_content_included) == (expected, False)
    assert _raw_scope(easy_db, source.notice_id) == (expected, False)
    request.assert_not_called()


def test_changed_parent_cannot_receive_legacy_scope(easy_db) -> None:
    source = _insert_notice(easy_db, None)
    save_notice_easy_text(easy_db, _result(source))
    easy_db.execute(
        "update public.notice_easy_texts set body_text_present = null, "
        "attachment_content_included = null where notice_id = %s",
        (source.notice_id,),
    )
    token = get_notice_easy_text_cache_token(easy_db, source.notice_id)
    old_result = get_notice_easy_text(easy_db, source.notice_id)
    easy_db.execute(
        "update public.notices set body_html = '<p>추가된 본문</p>' where id = %s",
        (source.notice_id,),
    )

    with pytest.raises(EasyTextStorageError, match="원문이 바뀌어"):
        fill_notice_easy_text_scope(easy_db, old_result, expected_cache_token=token)
    assert _raw_scope(easy_db, source.notice_id) == (None, None)
    assert get_notice_easy_text(easy_db, source.notice_id) is None


@pytest.mark.parametrize("claimed_scope", [(True, False), (False, True), (True, True)])
def test_save_rejects_forged_or_incorrect_scope(easy_db, claimed_scope) -> None:
    source = _insert_notice(easy_db, None)
    forged = EasyLanguageResult.model_validate(
        {
            **_result(source).model_dump(),
            "body_text_present": claimed_scope[0],
            "attachment_content_included": claimed_scope[1],
        }
    )

    with pytest.raises(EasyTextStorageError, match="처리 범위"):
        save_notice_easy_text(easy_db, forged)
    assert easy_db.execute("select count(*) from public.notice_easy_texts").fetchone()[0] == 0


@pytest.mark.parametrize("flags", [(None, False), (True, None), (0, False), (False, "false")])
def test_result_model_rejects_partial_or_nonboolean_scope(flags) -> None:
    source = NoticeGlossaryInput(text="익일 안내")
    with pytest.raises(ValidationError):
        EasyLanguageResult.model_validate(
            {
                **_result(source).model_dump(),
                "body_text_present": flags[0],
                "attachment_content_included": flags[1],
            }
        )


@pytest.mark.parametrize("flags", [(None, False), (True, None), (False, True)])
def test_sql_check_rejects_partial_or_attachment_included_scope(easy_db, flags) -> None:
    source = _insert_notice(easy_db, None)
    save_notice_easy_text(easy_db, _result(source))

    with pytest.raises(psycopg.errors.CheckViolation):
        with easy_db.transaction():
            easy_db.execute(
                "update public.notice_easy_texts set body_text_present = %s, "
                "attachment_content_included = %s where notice_id = %s",
                (*flags, source.notice_id),
            )
    assert _raw_scope(easy_db, source.notice_id) == (False, False)


def test_scope_fill_requires_unchanged_cache_token(easy_db) -> None:
    source = _insert_notice(easy_db, None)
    save_notice_easy_text(easy_db, _result(source))
    easy_db.execute(
        "update public.notice_easy_texts set body_text_present = null, "
        "attachment_content_included = null where notice_id = %s",
        (source.notice_id,),
    )
    old_token = get_notice_easy_text_cache_token(easy_db, source.notice_id)
    cached = get_notice_easy_text(easy_db, source.notice_id)
    easy_db.execute(
        "update public.notice_easy_texts set generated_at = generated_at + interval '1 second' "
        "where notice_id = %s",
        (source.notice_id,),
    )

    assert fill_notice_easy_text_scope(easy_db, cached, expected_cache_token=old_token) is False
    assert _raw_scope(easy_db, source.notice_id) == (None, None)


def test_old_worker_source_change_clears_scope_then_cache_fills_without_api(easy_db) -> None:
    source = _insert_notice(easy_db, None)
    save_notice_easy_text(easy_db, _result(source))
    assert _raw_scope(easy_db, source.notice_id) == (False, False)
    new_html = "<p>추가된 본문</p>"
    new_text = "익일 안내\n추가된 본문"
    easy_db.execute(
        "update public.notices set body_html = %s where id = %s",
        (new_html, source.notice_id),
    )
    # An old worker writes a new source but has no SQL columns for the scope.
    easy_db.execute(
        "update public.notice_easy_texts set notice_revision = %s, source_hash = %s, "
        "original_text = %s, easy_text = %s, changes = %s, "
        "generated_at = generated_at + interval '1 second' where notice_id = %s",
        (
            notice_content_revision("익일 안내", new_html),
            sha256(new_text.encode("utf-8")).hexdigest(),
            new_text,
            new_text,
            Jsonb([]),
            source.notice_id,
        ),
    )
    assert _raw_scope(easy_db, source.notice_id) == (None, None)

    current = load_notice_glossary_input(easy_db, source.notice_id)
    request = MagicMock(side_effect=AssertionError("old cache must not call Gemini"))
    cached = simplify_and_store_notice(easy_db, current, request=request)
    assert (cached.body_text_present, cached.attachment_content_included) == (True, False)
    assert _raw_scope(easy_db, source.notice_id) == (True, False)
    request.assert_not_called()


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_app_reads_scope_columns_only_for_current_visible_notice(easy_db, role: str) -> None:
    source = _insert_notice(easy_db, "<p>본문</p>")
    save_notice_easy_text(easy_db, _result(source))
    with easy_db.transaction():
        easy_db.execute(f"set local role {role}")
        assert easy_db.execute(
            "select body_text_present, attachment_content_included "
            "from public.notice_easy_texts where notice_id = %s",
            (source.notice_id,),
        ).fetchall() == [(True, False)]
        easy_db.execute("reset role")
    easy_db.execute(
        "update public.notices set title = '수정된 제목' where id = %s", (source.notice_id,)
    )
    with easy_db.transaction():
        easy_db.execute(f"set local role {role}")
        assert (
            easy_db.execute(
                "select body_text_present, attachment_content_included "
                "from public.notice_easy_texts where notice_id = %s",
                (source.notice_id,),
            ).fetchall()
            == []
        )
        easy_db.execute("reset role")
