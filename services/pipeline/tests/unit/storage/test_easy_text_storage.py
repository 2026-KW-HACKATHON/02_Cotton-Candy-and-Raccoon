"""Real local PostgreSQL: preservation, cache reuse, stale source and read policies."""

import json
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from psycopg.pq import TransactionStatus
from support.collect_easy_text_storage import committed_easy_db as committed_easy_db
from support.easy_text_storage import _NOW, _candidate_result, _notice, _request, _result
from support.easy_text_storage import easy_db as easy_db
from support.easy_text_storage import service_db as service_db

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
    get_notice_easy_text_cache_token,
    save_notice_easy_text,
)


def test_repeated_notice_read_and_conversion_have_zero_additional_api_calls(
    service_db, monkeypatch
):
    source = _notice(service_db)
    request = MagicMock(side_effect=_request)
    service_db.commit()
    result = simplify_and_store_notice(service_db, source, api_key="fake", request=request)
    assert result.easy_text == source.text.replace("구비서류를", "준비할 서류를")
    assert get_notice_easy_text(service_db, source.notice_id) == result
    monkeypatch.setattr(
        "pipeline.glossary.easy_language_service.load_gemini_api_key",
        MagicMock(side_effect=AssertionError("cached conversion must not read secrets")),
    )
    service_db.commit()
    assert simplify_and_store_notice(service_db, source, request=request) == result
    assert service_db.info.transaction_status == TransactionStatus.IDLE
    assert simplify_and_store_notice(service_db, source, request=request) == result
    request.assert_called_once()
    assert load_notice_glossary_input(service_db, source.notice_id).text == source.text


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
def test_new_prompt_replaces_old_cache_once_then_reuses_it(service_db, old_changes):
    source = _notice(service_db)
    previous = simplify_notice(
        source,
        api_key="fake",
        request=lambda **kwargs: json.dumps(
            {"changes": old_changes, "dictionary_candidates": []}, ensure_ascii=False
        ),
        clock=lambda: _NOW,
    )
    previous = EasyLanguageResult.model_validate(
        {**previous.model_dump(), "prompt_version": "easy-language-v3"}
    )
    save_notice_easy_text(service_db, previous)
    request = MagicMock(side_effect=_request)
    service_db.commit()
    result = simplify_and_store_notice(
        service_db,
        source,
        api_key="fake",
        request=request,
        clock=lambda: _NOW + timedelta(seconds=1),
    )
    assert result.prompt_version == PROMPT_VERSION != previous.prompt_version
    assert result.easy_text != source.text
    assert service_db.info.transaction_status == TransactionStatus.IDLE
    assert simplify_and_store_notice(service_db, source, request=request) == result
    request.assert_called_once()


@pytest.mark.parametrize("generation", ["model", "prompt"])
@pytest.mark.parametrize("clock_ahead", [timedelta(), timedelta(hours=1)])
def test_new_generation_replaces_old_cache_despite_equal_or_earlier_clock(
    service_db, monkeypatch, generation, clock_ahead
):
    source = _notice(service_db)
    old_field = "model" if generation == "model" else "prompt_version"
    old_value = "gemini-previous-model" if generation == "model" else "easy-language-v3"
    previous = EasyLanguageResult.model_validate(
        {**_result(source, _NOW + clock_ahead).model_dump(), old_field: old_value}
    )
    save_notice_easy_text(service_db, previous)
    request = MagicMock(side_effect=_request)

    service_db.commit()
    current = simplify_and_store_notice(
        service_db,
        source,
        api_key="fake",
        request=request,
        clock=lambda: _NOW,
    )

    assert current.model == DEFAULT_MODEL
    assert current.prompt_version == PROMPT_VERSION
    assert current.generated_at == _NOW <= previous.generated_at
    assert get_notice_easy_text(service_db, source.notice_id) == current
    monkeypatch.setattr(
        "pipeline.glossary.easy_language_service.load_gemini_api_key",
        MagicMock(side_effect=AssertionError("current cache must not read API credentials")),
    )
    service_db.commit()
    assert simplify_and_store_notice(service_db, source, request=request) == current
    assert service_db.info.transaction_status == TransactionStatus.IDLE
    assert simplify_and_store_notice(service_db, source, request=request) == current
    request.assert_called_once()


def test_failed_refresh_preserves_prior_saved_result_and_original(service_db):
    source = _notice(service_db)
    stored = _result(source)
    save_notice_easy_text(service_db, stored)
    request = MagicMock(side_effect=EasyLanguageAPIError("secret"))
    service_db.commit()
    with pytest.raises(EasyLanguageAPIError):
        simplify_and_store_notice(service_db, source, refresh=True, api_key="fake", request=request)
    assert service_db.info.transaction_status == TransactionStatus.IDLE
    assert get_notice_easy_text(service_db, source.notice_id) == stored
    assert load_notice_glossary_input(service_db, source.notice_id) == source


def test_changed_parent_rejects_old_result_and_hides_stale_cache(service_db):
    source = _notice(service_db)
    result = _result(source)
    save_notice_easy_text(service_db, result)
    service_db.execute(
        "update public.notices set body_html = '<p>수정된 본문</p>' where id = %s",
        (source.notice_id,),
    )
    assert get_notice_easy_text(service_db, source.notice_id) is None
    with pytest.raises(EasyTextStorageError, match="원문이 바뀌어"):
        save_notice_easy_text(service_db, result)
    service_db.commit()
    with pytest.raises(EasyTextStorageError, match="원문이 바뀌었습니다"):
        simplify_and_store_notice(service_db, source, api_key="fake", request=MagicMock())


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
def test_input_must_match_database_notice(service_db, with_revision):
    source = _notice(service_db)
    service_db.commit()
    with pytest.raises(EasyTextStorageError, match="연결할 수 없습니다"):
        simplify_and_store_notice(
            service_db,
            NoticeGlossaryInput(
                notice_id=source.notice_id,
                text="다른 공지",
                notice_revision=source.notice_revision if with_revision else None,
            ),
            api_key="fake",
        )


@pytest.mark.parametrize("populated", [False, True], ids=["empty", "populated"])
@pytest.mark.parametrize("changed", ["source", "model", "prompt_version", "generated_at"])
def test_successful_save_restores_identical_candidates_after_provenance_change(
    easy_db, populated, changed
):
    source = _notice(easy_db)
    build_result = _candidate_result if populated else _result
    previous = build_result(source)
    save_notice_easy_text(easy_db, previous)
    token = get_notice_easy_text_cache_token(easy_db, source.notice_id)
    if changed == "source":
        # Markup changes advance the source revision while keeping candidate bytes.
        easy_db.execute(
            "update public.notices set body_html = replace(replace(body_html, '<p>', '<div>'), "
            "'</p>', '</div>') where id = %s",
            (source.notice_id,),
        )
        source = load_notice_glossary_input(easy_db, source.notice_id)
    current = build_result(source, _NOW + timedelta(seconds=1))
    if changed in {"model", "prompt_version"}:
        current = EasyLanguageResult.model_validate(
            {**current.model_dump(), changed: "new-generation"}
        )
    assert current.dictionary_candidates == previous.dictionary_candidates

    save_notice_easy_text(easy_db, current, expected_cache_token=token)

    assert get_notice_easy_text(easy_db, source.notice_id) == current
    assert easy_db.execute(
        "select dictionary_candidates from public.notice_easy_texts where notice_id = %s",
        (source.notice_id,),
    ).fetchone()[0] == [item.model_dump(mode="json") for item in current.dictionary_candidates]


def test_legacy_null_candidates_remain_unknown_when_read(easy_db):
    source = _notice(easy_db)
    legacy = EasyLanguageResult.model_validate(
        {**_result(source).model_dump(), "dictionary_candidates": None}
    )
    save_notice_easy_text(easy_db, legacy)

    assert get_notice_easy_text(easy_db, source.notice_id) == legacy
    assert easy_db.execute(
        "select dictionary_candidates is null from public.notice_easy_texts where notice_id = %s",
        (source.notice_id,),
    ).fetchone() == (True,)


def test_save_rejects_standalone_candidate_that_resolves_inside_database_title(easy_db):
    source = _notice(easy_db)
    standalone = NoticeGlossaryInput(
        notice_id=source.notice_id, notice_revision=source.notice_revision, text=source.text
    )
    result = simplify_notice(
        standalone,
        api_key="fake",
        request=lambda **kwargs: json.dumps(
            {
                "changes": [],
                "dictionary_candidates": [
                    {"original": "모집", "query_word": "모집", "context": "참가자 모집"}
                ],
            },
            ensure_ascii=False,
        ),
        clock=lambda: _NOW,
    )

    with pytest.raises(EasyTextStorageError, match="본문 범위"):
        save_notice_easy_text(easy_db, result)

    assert get_notice_easy_text(easy_db, source.notice_id) is None
