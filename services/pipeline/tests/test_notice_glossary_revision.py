"""Reject work for an older collected notice while preserving the current snapshot."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from test_notice_glossary_storage import notice_glossary_db as notice_glossary_db

from pipeline.glossary import notice_service
from pipeline.glossary.models import GlossaryLookup
from pipeline.glossary.process import process_notice_glossary
from pipeline.storage.notice_glossary import (
    NoticeGlossaryStorageError,
    get_notice_glossary,
    save_notice_glossary,
)

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


def empty_lookup(query):
    return GlossaryLookup(
        query=query,
        status="not_found",
        providers_checked=("onterm", "opendict"),
        queried_at=NOW,
    )


def result(source, *, seconds=0):
    return process_notice_glossary(
        source, empty_lookup, clock=lambda: NOW + timedelta(seconds=seconds)
    )


def set_notice(conn, notice_id, title, body=None):
    conn.execute(
        "update public.notices set title = %s, body_html = %s where id = %s",
        (title, body, notice_id),
    )
    return notice_service.load_notice_glossary_input(conn, notice_id)


def test_db_late_old_source_cannot_overwrite_current_snapshot(notice_glossary_db):
    conn, notice_id = notice_glossary_db
    old = set_notice(conn, notice_id, "익일")
    current = set_notice(conn, notice_id, "공람")
    saved = result(current)
    save_notice_glossary(conn, saved)
    with pytest.raises(NoticeGlossaryStorageError, match="원문이 바뀌어"):
        save_notice_glossary(conn, result(old, seconds=1))
    assert get_notice_glossary(conn, notice_id) == saved
    assert (
        conn.execute("select title from public.notices where id = %s", (notice_id,)).fetchone()[0]
        == "공람"
    )
    assert conn.execute("select 1").fetchone() == (1,)  # Failed savepoint did not abort caller.


def test_db_changed_html_with_same_plain_text_invalidates_loaded_revision(notice_glossary_db):
    conn, notice_id = notice_glossary_db
    old = set_notice(conn, notice_id, "안내", "<p>익일</p>")
    current = set_notice(conn, notice_id, "안내", "<div>익일</div>")
    assert old.text == current.text
    assert old.notice_revision != current.notice_revision
    saved = result(current)
    save_notice_glossary(conn, saved)
    with pytest.raises(NoticeGlossaryStorageError, match="원문이 바뀌어"):
        save_notice_glossary(conn, result(old, seconds=1))
    assert get_notice_glossary(conn, notice_id) == saved


@pytest.mark.parametrize("seconds", [0, -1])
@pytest.mark.parametrize("same_text", [True, False])
def test_db_current_revision_replaces_older_source_even_without_newer_timestamp(
    notice_glossary_db, seconds, same_text
):
    conn, notice_id = notice_glossary_db
    old = set_notice(conn, notice_id, "안내", "<p>익일</p>")
    save_notice_glossary(conn, result(old))
    current = set_notice(conn, notice_id, "안내", "<div>익일</div>" if same_text else "공람")
    saved = result(current, seconds=seconds)
    assert current.notice_revision != old.notice_revision
    assert (current.text == old.text) is same_text
    save_notice_glossary(conn, saved)
    assert get_notice_glossary(conn, notice_id) == saved


@pytest.mark.parametrize("seconds", [0, -1])
def test_db_service_returns_current_html_revision_when_old_timestamp_is_not_older(
    notice_glossary_db, seconds
):
    from pathlib import Path

    conn, notice_id = notice_glossary_db
    migration = (
        Path(__file__).resolve().parents[3] / "supabase/migrations/20261005000000_glossary.sql"
    )
    conn.execute(migration.read_text("utf-8"))
    old = set_notice(conn, notice_id, "안내", "<p>익일</p>")
    save_notice_glossary(conn, result(old))
    current = set_notice(conn, notice_id, "안내", "<div>익일</div>")
    onterm, opendict = MagicMock(), MagicMock()
    onterm.lookup.return_value = opendict.lookup.return_value = ()
    saved = notice_service.process_and_store_notice_glossary(
        conn,
        current,
        onterm_client=onterm,
        opendict_client=opendict,
        clock=lambda: NOW + timedelta(seconds=seconds),
    )
    assert saved.notice_revision == current.notice_revision
    assert saved.generated_at == NOW + timedelta(seconds=seconds)
    assert saved.status == "completed"
    assert get_notice_glossary(conn, notice_id) == saved


def test_db_stale_loaded_source_is_rejected_before_api_or_cache_work(
    notice_glossary_db, monkeypatch
):
    conn, notice_id = notice_glossary_db
    old = set_notice(conn, notice_id, "익일")
    current = set_notice(conn, notice_id, "공람")
    saved = result(current)
    save_notice_glossary(conn, saved)
    lookup = MagicMock(side_effect=AssertionError("stale source must not call the API"))
    cache = MagicMock(side_effect=AssertionError("stale source must not read dictionary cache"))
    monkeypatch.setattr(notice_service, "query_glossary", lookup)
    monkeypatch.setattr(notice_service, "get_glossary", cache)
    with pytest.raises(NoticeGlossaryStorageError, match="원문이 바뀌어"):
        notice_service.process_and_store_notice_glossary(conn, old)
    lookup.assert_not_called()
    cache.assert_not_called()
    assert get_notice_glossary(conn, notice_id) == saved


def test_db_current_loaded_source_roundtrips_revision_without_changing_notice(notice_glossary_db):
    conn, notice_id = notice_glossary_db
    current = set_notice(conn, notice_id, "익일", "<p>원문 유지</p>")
    before = conn.execute("select title, body_html from public.notices where id = %s", (notice_id,))
    before = before.fetchone()
    saved = result(current)
    save_notice_glossary(conn, saved)
    returned = get_notice_glossary(conn, notice_id)
    assert returned == saved
    assert returned.notice_revision == current.notice_revision
    assert (
        conn.execute(
            "select title, body_html from public.notices where id = %s", (notice_id,)
        ).fetchone()
        == before
    )


def test_db_notice_change_during_lookup_rolls_back_dictionary_writes_and_keeps_new_snapshot(
    notice_glossary_db,
):
    from pathlib import Path

    conn, notice_id = notice_glossary_db
    migration = (
        Path(__file__).resolve().parents[3] / "supabase/migrations/20261005000000_glossary.sql"
    )
    conn.execute(migration.read_text("utf-8"))
    old = set_notice(conn, notice_id, "익일")
    newer = []

    def update_during_lookup(query):
        if not newer:
            current = set_notice(conn, notice_id, "공람")
            saved = result(current, seconds=1)
            save_notice_glossary(conn, saved)
            newer.append(saved)
        return ()

    onterm = MagicMock()
    onterm.lookup.side_effect = update_during_lookup
    opendict = MagicMock()
    opendict.lookup.return_value = ()
    with pytest.raises(NoticeGlossaryStorageError, match="원문이 바뀌어"):
        notice_service.process_and_store_notice_glossary(
            conn,
            old,
            onterm_client=onterm,
            opendict_client=opendict,
            clock=lambda: NOW + timedelta(seconds=2),
        )
    assert get_notice_glossary(conn, notice_id) == newer[0]
    assert conn.execute("select count(*) from public.glossary_lookups").fetchone() == (0,)
    assert (
        conn.execute("select title from public.notices where id = %s", (notice_id,)).fetchone()[0]
        == "공람"
    )
