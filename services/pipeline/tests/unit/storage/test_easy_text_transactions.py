"""Real PostgreSQL: Gemini runs without a transaction; concurrent work stays safe."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import psycopg
import pytest
from psycopg.pq import TransactionStatus
from support.collect_easy_text_storage import committed_easy_db as committed_easy_db
from support.easy_text_cache_cas import _alternative_result
from support.easy_text_storage import _NOW, _notice, _request, _result
from support.easy_text_storage import service_db as service_db

from pipeline.glossary.easy_language import EasyLanguageAPIError
from pipeline.glossary.easy_language_service import simplify_and_store_notice
from pipeline.storage.notice_easy_text import (
    EasyTextStorageError,
    get_notice_easy_text,
    save_notice_easy_text,
)


def _ready_notice(conn):
    with conn.transaction():
        source = _notice(conn)
    assert conn.info.transaction_status == TransactionStatus.IDLE
    return source


def test_gemini_runs_while_connection_is_idle_without_advisory_locks(service_db, committed_easy_db):
    source = _ready_notice(service_db)
    worker_pid = service_db.info.backend_pid

    def request(**kwargs):
        assert service_db.info.transaction_status == TransactionStatus.IDLE
        with psycopg.connect(committed_easy_db.database_url, autocommit=True) as observer:
            assert observer.execute(
                "select state, xact_start from pg_stat_activity where pid = %s",
                (worker_pid,),
            ).fetchone() == ("idle", None)
            assert (
                observer.execute(
                    "select count(*) from pg_locks where pid = %s and locktype = 'advisory'",
                    (worker_pid,),
                ).fetchone()[0]
                == 0
            )
        return _request(**kwargs)

    result = simplify_and_store_notice(
        service_db, source, api_key="fake", request=request, clock=lambda: _NOW
    )

    assert service_db.info.transaction_status == TransactionStatus.IDLE
    with psycopg.connect(committed_easy_db.database_url) as observer:
        assert get_notice_easy_text(observer, source.notice_id) == result


def test_open_caller_transaction_is_rejected_without_committing_pending_work(
    service_db, committed_easy_db
):
    source = _ready_notice(service_db)
    service_db.execute(
        "update public.notices set department = '미확정 변경' where id = %s",
        (source.notice_id,),
    )
    request = MagicMock(side_effect=AssertionError("pending caller work must prevent Gemini"))

    with pytest.raises(EasyTextStorageError, match="열린 트랜잭션"):
        simplify_and_store_notice(service_db, source, api_key="fake", request=request)

    request.assert_not_called()
    assert service_db.info.transaction_status == TransactionStatus.INTRANS
    assert (
        service_db.execute(
            "select department from public.notices where id = %s", (source.notice_id,)
        ).fetchone()[0]
        == "미확정 변경"
    )
    with psycopg.connect(committed_easy_db.database_url) as observer:
        assert (
            observer.execute(
                "select department from public.notices where id = %s", (source.notice_id,)
            ).fetchone()[0]
            is None
        )
    service_db.rollback()
    assert service_db.info.transaction_status == TransactionStatus.IDLE
    with psycopg.connect(committed_easy_db.database_url) as observer:
        assert (
            observer.execute(
                "select department from public.notices where id = %s", (source.notice_id,)
            ).fetchone()[0]
            is None
        )
        assert get_notice_easy_text(observer, source.notice_id) is None


def test_source_can_change_during_gemini_and_old_result_is_rejected(service_db, committed_easy_db):
    source = _ready_notice(service_db)
    previous = _result(source)
    with service_db.transaction():
        save_notice_easy_text(service_db, previous)

    def request(**kwargs):
        assert service_db.info.transaction_status == TransactionStatus.IDLE
        with psycopg.connect(committed_easy_db.database_url) as updater:
            updater.execute("set local lock_timeout = '500ms'")
            updater.execute(
                "update public.notices set body_html = '<p>수정된 본문</p>' where id = %s",
                (source.notice_id,),
            )
        return _request(**kwargs)

    with pytest.raises(EasyTextStorageError, match="원문이 바뀌어"):
        simplify_and_store_notice(
            service_db,
            source,
            refresh=True,
            api_key="fake",
            request=request,
            clock=lambda: _NOW + timedelta(seconds=1),
        )

    assert service_db.info.transaction_status == TransactionStatus.IDLE
    with psycopg.connect(committed_easy_db.database_url) as observer:
        assert (
            observer.execute(
                "select body_html from public.notices where id = %s", (source.notice_id,)
            ).fetchone()[0]
            == "<p>수정된 본문</p>"
        )
        assert (
            observer.execute(
                "select easy_text from public.notice_easy_texts where notice_id = %s",
                (source.notice_id,),
            ).fetchone()[0]
            == previous.easy_text
        )
        assert get_notice_easy_text(observer, source.notice_id) is None


@pytest.mark.parametrize("refresh", [False, True], ids=["first-result", "same-generation-refresh"])
def test_late_gemini_returns_the_result_saved_by_another_connection(
    service_db, committed_easy_db, refresh
):
    source = _ready_notice(service_db)
    if refresh:
        with service_db.transaction():
            save_notice_easy_text(service_db, _result(source))
    winner = _alternative_result(source, _NOW + timedelta(seconds=1))

    def request(**kwargs):
        assert service_db.info.transaction_status == TransactionStatus.IDLE
        with psycopg.connect(committed_easy_db.database_url) as writer:
            writer.execute("set local lock_timeout = '500ms'")
            save_notice_easy_text(writer, winner)
        return _request(**kwargs)

    result = simplify_and_store_notice(
        service_db,
        source,
        refresh=refresh,
        api_key="fake",
        request=request,
        clock=lambda: _NOW + timedelta(seconds=2),
    )

    assert result == winner
    assert service_db.info.transaction_status == TransactionStatus.IDLE
    with psycopg.connect(committed_easy_db.database_url) as observer:
        assert get_notice_easy_text(observer, source.notice_id) == winner


def test_deleted_cache_is_preserved_when_an_inflight_refresh_finishes(
    service_db, committed_easy_db
):
    source = _ready_notice(service_db)
    with service_db.transaction():
        save_notice_easy_text(service_db, _result(source))

    def request(**kwargs):
        with psycopg.connect(committed_easy_db.database_url) as writer:
            writer.execute(
                "delete from public.notice_easy_texts where notice_id = %s", (source.notice_id,)
            )
        return _request(**kwargs)

    with pytest.raises(EasyTextStorageError):
        simplify_and_store_notice(
            service_db,
            source,
            refresh=True,
            api_key="fake",
            request=request,
            clock=lambda: _NOW + timedelta(seconds=2),
        )

    assert service_db.info.transaction_status == TransactionStatus.IDLE
    with psycopg.connect(committed_easy_db.database_url) as observer:
        assert get_notice_easy_text(observer, source.notice_id) is None


@pytest.mark.parametrize("failure_kind", ["permanent", "deadline", "deferred"])
def test_gemini_failure_preserves_committed_cache_and_leaves_connection_idle(
    service_db, committed_easy_db, failure_kind
):
    source = _ready_notice(service_db)
    previous = _result(source)
    with service_db.transaction():
        save_notice_easy_text(service_db, previous)

    def fail(**_kwargs):
        assert service_db.info.transaction_status == TransactionStatus.IDLE
        raise EasyLanguageAPIError(
            "simulated failure",
            reason_code="api_timeout" if failure_kind == "deadline" else "api_error",
            failure_kind=failure_kind,
            retryable=failure_kind != "permanent",
            retry_at=datetime(2026, 10, 9, tzinfo=UTC) if failure_kind == "deferred" else None,
        )

    with pytest.raises(EasyLanguageAPIError):
        simplify_and_store_notice(service_db, source, refresh=True, api_key="fake", request=fail)

    assert service_db.info.transaction_status == TransactionStatus.IDLE
    with psycopg.connect(committed_easy_db.database_url) as observer:
        assert get_notice_easy_text(observer, source.notice_id) == previous


def test_valid_response_after_deadline_cannot_replace_committed_cache(
    service_db, committed_easy_db, monkeypatch
):
    from pipeline import gemini_execution

    source = _ready_notice(service_db)
    previous = _result(source)
    with service_db.transaction():
        save_notice_easy_text(service_db, previous)
    now = [0.0]
    monkeypatch.setattr(gemini_execution.time, "monotonic", lambda: now[0])

    def late_request(**kwargs):
        now[0] += 121.0
        return _request(**kwargs)

    with pytest.raises(EasyLanguageAPIError) as captured:
        simplify_and_store_notice(
            service_db, source, refresh=True, api_key="fake", request=late_request
        )
    assert captured.value.failure_kind == "deadline"
    assert service_db.info.transaction_status == TransactionStatus.IDLE
    with psycopg.connect(committed_easy_db.database_url) as observer:
        assert get_notice_easy_text(observer, source.notice_id) == previous
