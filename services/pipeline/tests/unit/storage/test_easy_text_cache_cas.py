"""Preserve refreshed cache rows when an earlier generation finishes late."""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from psycopg.types.json import Jsonb
from support.collect_easy_text_storage import committed_easy_db as committed_easy_db
from support.easy_text_cache_cas import _alternative_result
from support.easy_text_storage import _NOW, _candidate_result, _notice, _result
from support.easy_text_storage import easy_db as easy_db
from support.easy_text_storage import service_db as service_db

from pipeline.glossary.easy_language import EasyLanguageResult
from pipeline.glossary.easy_language_service import simplify_and_store_notice
from pipeline.glossary.source import NoticeGlossaryInput
from pipeline.storage.notice_easy_text import (
    EasyTextStorageError,
    get_notice_easy_text,
    get_notice_easy_text_cache_token,
    save_notice_easy_text,
)


def _old_generation(result, generation):
    if generation == "same":
        return result
    field = "model" if generation == "model" else "prompt_version"
    value = "gemini-previous-model" if generation == "model" else "easy-language-v3"
    return EasyLanguageResult.model_validate({**result.model_dump(), field: value})


@pytest.mark.parametrize("generation", ["model", "prompt"])
@pytest.mark.parametrize(
    "clock_offset",
    [timedelta(hours=-1), timedelta(), timedelta(hours=1)],
    ids=["earlier", "equal", "later"],
)
def test_late_old_generation_cannot_replace_current_cache(
    service_db, monkeypatch, generation, clock_offset
):
    source = _notice(service_db)
    current = _result(source)
    save_notice_easy_text(service_db, current)
    delayed = _old_generation(_alternative_result(source, _NOW + clock_offset), generation)

    save_notice_easy_text(service_db, delayed)

    assert get_notice_easy_text(service_db, source.notice_id) == current
    request = MagicMock(side_effect=AssertionError("current cache must avoid another API request"))
    monkeypatch.setattr(
        "pipeline.glossary.easy_language_service.load_gemini_api_key",
        MagicMock(side_effect=AssertionError("current cache must not read credentials")),
    )
    service_db.commit()
    assert simplify_and_store_notice(service_db, source, request=request) == current
    request.assert_not_called()


@pytest.mark.parametrize("generation", ["model", "prompt"])
def test_old_worker_with_a_captured_token_cannot_undo_a_completed_upgrade(service_db, generation):
    source = _notice(service_db)
    previous = _old_generation(_alternative_result(source), generation)
    save_notice_easy_text(service_db, previous)
    token = get_notice_easy_text_cache_token(service_db, source.notice_id)
    current = _result(source, _NOW - timedelta(hours=1))
    save_notice_easy_text(service_db, current, expected_cache_token=token)
    delayed_old = _old_generation(
        _alternative_result(source, _NOW + timedelta(hours=1)), generation
    )

    save_notice_easy_text(service_db, delayed_old, expected_cache_token=token)

    assert get_notice_easy_text(service_db, source.notice_id) == current
    request = MagicMock(side_effect=AssertionError("completed upgrade must still be cached"))
    service_db.commit()
    assert simplify_and_store_notice(service_db, source, request=request) == current
    request.assert_not_called()


@pytest.mark.parametrize("generation", ["model", "prompt"])
def test_captured_token_allows_generation_upgrade_with_an_earlier_clock(easy_db, generation):
    source = _notice(easy_db)
    previous = _old_generation(_alternative_result(source, _NOW + timedelta(hours=1)), generation)
    save_notice_easy_text(easy_db, previous)
    token = get_notice_easy_text_cache_token(easy_db, source.notice_id)
    assert token is not None
    current = _result(source)

    save_notice_easy_text(easy_db, current, expected_cache_token=token)

    assert current.generated_at < previous.generated_at
    assert get_notice_easy_text(easy_db, source.notice_id) == current


@pytest.mark.parametrize("generation", ["model", "prompt"])
def test_stale_token_cannot_replace_an_intervening_same_generation_refresh(easy_db, generation):
    source = _notice(easy_db)
    previous = _old_generation(_result(source), generation)
    save_notice_easy_text(easy_db, previous)
    token = get_notice_easy_text_cache_token(easy_db, source.notice_id)
    assert token is not None
    refreshed = _old_generation(_alternative_result(source, _NOW + timedelta(hours=1)), generation)
    save_notice_easy_text(easy_db, refreshed)
    assert get_notice_easy_text(easy_db, source.notice_id) == refreshed
    assert get_notice_easy_text_cache_token(easy_db, source.notice_id) != token
    delayed_upgrade = _result(source, _NOW + timedelta(hours=2))

    save_notice_easy_text(easy_db, delayed_upgrade, expected_cache_token=token)

    assert get_notice_easy_text(easy_db, source.notice_id) == refreshed


@pytest.mark.parametrize("generation", ["model", "prompt", "same"])
def test_absent_token_cannot_clobber_a_generation_inserted_during_the_request(easy_db, generation):
    source = _notice(easy_db)
    token = get_notice_easy_text_cache_token(easy_db, source.notice_id)
    assert token is None
    intervening = _old_generation(_alternative_result(source), generation)
    save_notice_easy_text(easy_db, intervening)
    delayed_first_result = _result(source, _NOW + timedelta(hours=1))

    save_notice_easy_text(easy_db, delayed_first_result, expected_cache_token=token)

    assert get_notice_easy_text(easy_db, source.notice_id) == intervening


@pytest.mark.parametrize("generation", ["model", "prompt", "same"])
def test_changed_conversion_invalidates_token_with_generation_and_clock_unchanged(
    easy_db, generation
):
    source = _notice(easy_db)
    previous = _old_generation(_result(source), generation)
    save_notice_easy_text(easy_db, previous)
    token = get_notice_easy_text_cache_token(easy_db, source.notice_id)
    assert token is not None
    edited = _old_generation(_alternative_result(source), generation)
    assert edited.generated_at == previous.generated_at
    assert edited.model == previous.model
    assert edited.prompt_version == previous.prompt_version
    assert edited.easy_text != previous.easy_text
    easy_db.execute(
        "update public.notice_easy_texts set easy_text = %s, changes = %s, easy_result = %s "
        "where notice_id = %s",
        (
            edited.easy_text,
            Jsonb([change.model_dump(mode="json") for change in edited.changes]),
            Jsonb(edited.easy_result.model_dump(mode="json")),
            source.notice_id,
        ),
    )
    # A worker that omits candidates cannot attach the old list to its conversion.
    edited = EasyLanguageResult.model_validate(
        {**edited.model_dump(), "dictionary_candidates": None}
    )
    assert get_notice_easy_text(easy_db, source.notice_id) == edited
    assert get_notice_easy_text_cache_token(easy_db, source.notice_id) != token

    save_notice_easy_text(
        easy_db,
        _result(source, _NOW + timedelta(hours=1)),
        expected_cache_token=token,
    )

    assert get_notice_easy_text(easy_db, source.notice_id) == edited


def test_stale_token_cannot_replace_same_generation_with_a_later_clock(easy_db):
    source = _notice(easy_db)
    previous = _result(source)
    save_notice_easy_text(easy_db, previous)
    token = get_notice_easy_text_cache_token(easy_db, source.notice_id)
    assert token is not None
    refreshed = _alternative_result(source, _NOW + timedelta(hours=1))
    save_notice_easy_text(easy_db, refreshed)
    assert get_notice_easy_text(easy_db, source.notice_id) == refreshed

    save_notice_easy_text(
        easy_db,
        _result(source, _NOW + timedelta(hours=2)),
        expected_cache_token=token,
    )

    assert get_notice_easy_text(easy_db, source.notice_id) == refreshed


def test_captured_token_cannot_recreate_a_deleted_cache(easy_db):
    source = _notice(easy_db)
    save_notice_easy_text(easy_db, _result(source))
    token = get_notice_easy_text_cache_token(easy_db, source.notice_id)
    assert token is not None
    easy_db.execute(
        "delete from public.notice_easy_texts where notice_id = %s", (source.notice_id,)
    )

    save_notice_easy_text(
        easy_db,
        _alternative_result(source, _NOW + timedelta(hours=1)),
        expected_cache_token=token,
    )

    assert get_notice_easy_text(easy_db, source.notice_id) is None


@pytest.mark.parametrize(
    "clock_offset", [timedelta(hours=-1), timedelta()], ids=["earlier", "equal"]
)
def test_matching_token_does_not_replace_same_generation_with_older_or_equal_clock(
    easy_db, clock_offset
):
    source = _notice(easy_db)
    current = _result(source)
    save_notice_easy_text(easy_db, current)
    token = get_notice_easy_text_cache_token(easy_db, source.notice_id)
    assert token is not None
    delayed = _alternative_result(source, _NOW + clock_offset)

    save_notice_easy_text(easy_db, delayed, expected_cache_token=token)

    assert get_notice_easy_text(easy_db, source.notice_id) == current


def test_cache_token_remains_usable_after_database_timezone_changes(easy_db):
    source = _notice(easy_db)
    previous = _old_generation(_alternative_result(source), "prompt")
    save_notice_easy_text(easy_db, previous)
    easy_db.execute("set local time zone 'UTC'")
    token = get_notice_easy_text_cache_token(easy_db, source.notice_id)
    assert token is not None
    easy_db.execute("set local time zone 'Asia/Seoul'")
    assert get_notice_easy_text_cache_token(easy_db, source.notice_id) == token
    current = _result(source, _NOW - timedelta(hours=1))

    save_notice_easy_text(easy_db, current, expected_cache_token=token)

    assert get_notice_easy_text(easy_db, source.notice_id) == current


@pytest.mark.parametrize(
    "token",
    [True, b"a" * 64, "", "a" * 63, "a" * 65, "A" * 64, "g" * 64],
    ids=["boolean", "bytes", "empty", "short", "long", "uppercase", "non-hex"],
)
def test_invalid_cache_token_is_rejected_before_database_access(token):
    source = NoticeGlossaryInput(
        notice_id=1,
        notice_revision="a" * 64,
        text="구비서류를 지참하세요.",
    )
    conn = MagicMock()

    with pytest.raises(EasyTextStorageError, match="상태 확인값이 올바르지"):
        save_notice_easy_text(conn, _result(source), expected_cache_token=token)

    conn.cursor.assert_not_called()
    conn.execute.assert_not_called()
    conn.transaction.assert_not_called()


@pytest.mark.parametrize("loses_cas", [False, True], ids=["equal-clock", "stale-token"])
def test_skipped_save_never_restores_candidates_into_another_snapshot(easy_db, loses_cas):
    source = _notice(easy_db)
    current = EasyLanguageResult.model_validate(
        {**_result(source).model_dump(), "dictionary_candidates": None}
    )
    save_notice_easy_text(easy_db, current)
    token = get_notice_easy_text_cache_token(easy_db, source.notice_id)
    if loses_cas:
        current = _result(source, _NOW + timedelta(seconds=1))
        save_notice_easy_text(easy_db, current)
        assert get_notice_easy_text_cache_token(easy_db, source.notice_id) != token

    save_notice_easy_text(
        easy_db,
        _candidate_result(source, _NOW + timedelta(seconds=2) if loses_cas else _NOW),
        expected_cache_token=token,
    )

    assert get_notice_easy_text(easy_db, source.notice_id) == current


def test_candidate_only_change_invalidates_raw_cache_token(easy_db):
    source = _notice(easy_db)
    current = _result(source)
    save_notice_easy_text(easy_db, current)
    token = get_notice_easy_text_cache_token(easy_db, source.notice_id)
    winner = _candidate_result(source)
    easy_db.execute(
        "update public.notice_easy_texts set dictionary_candidates = %s where notice_id = %s",
        (
            Jsonb([item.model_dump(mode="json") for item in winner.dictionary_candidates]),
            source.notice_id,
        ),
    )
    assert get_notice_easy_text_cache_token(easy_db, source.notice_id) != token

    save_notice_easy_text(
        easy_db, _result(source, _NOW + timedelta(seconds=1)), expected_cache_token=token
    )

    assert get_notice_easy_text(easy_db, source.notice_id) == winner
