"""Notice snapshots and resumable progress; real DB cases use a disposable local DB."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from pipeline.glossary.document import (
    RULES_VERSION,
    DocumentTerm,
    NoticeGlossaryResult,
    QueryOutcome,
    apply_changes,
    make_changes,
)
from pipeline.glossary.models import GlossaryEntry, GlossaryLookup
from pipeline.glossary.notice_service import process_and_store_notice_glossary
from pipeline.glossary.source import (
    NoticeGlossaryInput,
    TermCandidate,
    locate_candidates,
    source_hash,
)
from pipeline.storage.notice_glossary import (
    NoticeGlossaryStorageError,
    get_notice_glossary,
    save_notice_glossary,
)

_TIME = datetime(2026, 10, 6, 9, tzinfo=UTC)
_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION = _ROOT / "supabase/migrations/20261006000000_notice_glossary.sql"
_FAILURE_CODES = (
    "configuration",
    "authentication",
    "transport",
    "timeout",
    "rate_limit",
    "http",
    "response_too_large",
    "too_many_results",
    "invalid_response",
    "api",
)


def _entry(query="익일", replacement="다음 날") -> GlossaryEntry:
    return GlossaryEntry(
        provider="opendict",
        entry_id="123" if query == "익일" else "456",
        sense_id="1",
        headword=query,
        definition="테스트용 사전 뜻풀이.",
        easy_terms=(replacement,) if replacement else (),
        source_url="https://opendict.korean.go.kr/word/123",
    )


def _query(query="익일", replacement="다음 날") -> QueryOutcome:
    return QueryOutcome(
        query=query,
        lookup=GlossaryLookup(
            query=query,
            status="found",
            entries=(_entry(query, replacement),),
            providers_checked=("opendict",),
            queried_at=_TIME,
        ),
    )


def _result(**changes) -> NoticeGlossaryResult:
    original = changes.pop("original_text", " \t?!\n")
    values = {
        "notice_id": 7,
        "original_text": original,
        "easy_text": original,
        "source_hash": source_hash(original),
        "rules_version": RULES_VERSION,
        "generated_at": _TIME,
        "status": "completed",
        **changes,
    }
    return NoticeGlossaryResult.model_validate(values)


def _progress(*, complete=False, first_replaced=True, **changes) -> NoticeGlossaryResult:
    source = NoticeGlossaryInput(notice_id=7, text=" \t익일 공람.\n")
    located = locate_candidates(
        source, (TermCandidate(surface=word, query=word) for word in ("익일", "공람"))
    )
    first_query = _query(replacement="다음 날" if first_replaced else None)
    first = DocumentTerm(
        word="익일",
        surface="익일",
        query="익일",
        status="replaced" if first_replaced else "explained",
        occurrences=located[0].occurrences,
        entries=first_query.lookup.entries,
        replacement="다음 날" if first_replaced else None,
    )
    second = DocumentTerm(
        word="공람",
        surface="공람",
        query="공람",
        status="explained" if complete else "pending",
        occurrences=located[1].occurrences,
        entries=(_entry("공람", None),) if complete else (),
    )
    terms = (first, second)
    replacements = make_changes(source.text, terms)
    return _result(
        original_text=source.text,
        status="completed" if complete else "partial",
        terms=terms,
        queries=(first_query, _query("공람", None)) if complete else (first_query,),
        changes=replacements,
        easy_text=apply_changes(source.text, replacements),
        pending_queries=() if complete else ("공람",),
        new_query_count=2 if complete else 1,
        **changes,
    )


def _all_pending(result: NoticeGlossaryResult) -> NoticeGlossaryResult:
    return NoticeGlossaryResult.model_validate(
        {
            **result.model_dump(),
            "terms": tuple(
                term.model_copy(
                    update={
                        "status": "pending",
                        "entries": (),
                        "replacement": None,
                    }
                )
                for term in result.terms
            ),
            "queries": (),
            "changes": (),
            "easy_text": result.original_text,
            "pending_queries": tuple(term.query for term in result.terms),
            "generated_at": _TIME + timedelta(seconds=1),
        }
    )


def _failed_progress(error_code: str, **changes) -> NoticeGlossaryResult:
    result = _progress(**changes)
    return NoticeGlossaryResult.model_validate(
        {
            **result.model_dump(),
            "terms": (
                result.terms[0],
                result.terms[1].model_copy(update={"status": "failed", "error_code": error_code}),
            ),
            "queries": (*result.queries, QueryOutcome(query="공람", error_code=error_code)),
            "new_query_count": 2,
        }
    )


def _row(result: NoticeGlossaryResult) -> tuple:
    return (
        result.notice_id,
        result.source_hash,
        result.rules_version,
        result.generated_at,
        result.status,
        result.model_dump(mode="json"),
    )


def _mock_conn(*rows):
    conn = MagicMock()
    conn.autocommit = False
    conn.info.transaction_status = TransactionStatus.INTRANS
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = rows
    return conn, cursor


def test_read_exact_original_replacements_and_attribution() -> None:
    result = _progress()
    conn, cursor = _mock_conn(_row(result))
    assert (
        get_notice_glossary(
            conn, 7, source_hash=result.source_hash, rules_version=result.rules_version
        )
        == result
    )
    statement, values = cursor.execute.call_args.args
    assert "where notice_id = %s" in statement
    assert values == (7,)
    conn.commit.assert_not_called()
    conn.close.assert_not_called()


@pytest.mark.parametrize("expected", [{"source_hash": "a" * 64}, {"rules_version": "new"}])
def test_stale_generation_is_not_returned(expected) -> None:
    conn, _ = _mock_conn(_row(_result()))
    assert get_notice_glossary(conn, 7, **expected) is None


def test_missing_snapshot_is_distinct_from_completed_without_candidates() -> None:
    conn, _ = _mock_conn(None, _row(_result()))
    assert get_notice_glossary(conn, 7) is None
    result = get_notice_glossary(conn, 7)
    assert result.status == "completed"
    assert result.original_text == result.easy_text
    assert result.terms == ()


@pytest.mark.parametrize(
    "index,value",
    [(0, 8), (1, "a" * 64), (2, "different"), (3, _TIME + timedelta(seconds=1)), (4, "partial")],
)
def test_corrupt_metadata_is_rejected_with_safe_error(index, value) -> None:
    row = list(_row(_result()))
    row[index] = value
    conn, _ = _mock_conn(tuple(row))
    with pytest.raises(NoticeGlossaryStorageError, match="저장된") as caught:
        get_notice_glossary(conn, 7)
    assert "익일" not in str(caught.value)


def test_corrupt_payload_is_not_treated_as_absent() -> None:
    valid = _result()
    row = list(_row(valid))
    row[5]["easy_text"] = "근거 없는 변경"
    conn, _ = _mock_conn(tuple(row))
    with pytest.raises(NoticeGlossaryStorageError, match="저장된"):
        get_notice_glossary(
            conn, 7, source_hash=valid.source_hash, rules_version=valid.rules_version
        )


def _obsolete_row(*, malformed=False) -> tuple:
    row = list(_row(_progress(complete=True)))
    row[2] = "dictionary-replacement-v0"
    row[5].update(
        {
            "rules_version": row[2],
            "terms": [],
            "queries": [],
            "changes": [],
            "easy_text": row[5]["original_text"],
        }
    )
    if malformed:
        row[5] = None
    return tuple(row)


@pytest.mark.parametrize("malformed", [False, True])
def test_old_rules_payload_is_ignored_before_current_validation(malformed) -> None:
    row = _obsolete_row(malformed=malformed)
    conn, _ = _mock_conn(row)
    assert get_notice_glossary(conn, 7, source_hash=row[1], rules_version=RULES_VERSION) is None


def test_old_source_payload_is_ignored_before_current_validation() -> None:
    row = list(_row(_result()))
    row[5] = None
    conn, _ = _mock_conn(tuple(row))
    assert get_notice_glossary(conn, 7, source_hash=_progress().source_hash) is None


@pytest.mark.parametrize("malformed", [False, True])
def test_new_rules_can_replace_obsolete_payload(malformed) -> None:
    incoming = _progress(generated_at=_TIME + timedelta(seconds=1))
    conn, cursor = _mock_conn(None, _obsolete_row(malformed=malformed))
    save_notice_glossary(conn, incoming)
    assert cursor.execute.call_args.args[0].startswith("update public.notice_glossary_results")
    assert cursor.execute.call_args.args[1][-2].obj == incoming.model_dump(mode="json")


def test_v1_completed_snapshot_is_invalidated_for_current_rules_with_identical_original() -> None:
    previous = _progress(complete=True, rules_version="dictionary-replacement-v1")
    current = _progress(generated_at=_TIME + timedelta(seconds=1))
    assert previous.original_text == current.original_text
    assert previous.source_hash == current.source_hash
    assert previous.rules_version != RULES_VERSION == current.rules_version
    conn, _ = _mock_conn(_row(previous))
    assert (
        get_notice_glossary(
            conn, current.notice_id, source_hash=current.source_hash, rules_version=RULES_VERSION
        )
        is None
    )
    conn, cursor = _mock_conn(None, _row(previous))
    save_notice_glossary(conn, current)
    statement, values = cursor.execute.call_args.args
    assert statement.startswith("update public.notice_glossary_results")
    assert values[1] == RULES_VERSION
    assert values[-2].obj == current.model_dump(mode="json")


def test_new_source_can_replace_malformed_old_generation() -> None:
    row = list(_row(_result()))
    row[5] = None
    incoming = _progress(generated_at=_TIME + timedelta(seconds=1))
    conn, cursor = _mock_conn(None, tuple(row))
    save_notice_glossary(conn, incoming)
    assert cursor.execute.call_args.args[0].startswith("update public.notice_glossary_results")


@pytest.mark.parametrize("generated_at", [_TIME, _TIME + timedelta(seconds=1)])
def test_stale_generation_still_honors_newer_or_equal_metadata(generated_at) -> None:
    row = list(_obsolete_row(malformed=True))
    row[3] = generated_at
    conn, cursor = _mock_conn(None, tuple(row))
    save_notice_glossary(conn, _progress())
    assert len(cursor.execute.call_args_list) == 2


@pytest.mark.parametrize(
    "generated_at", [_TIME - timedelta(seconds=1), _TIME + timedelta(seconds=1)]
)
def test_same_generation_bad_payload_cannot_be_silently_replaced(generated_at) -> None:
    row = list(_row(_progress()))
    row[5] = None
    conn, cursor = _mock_conn(None, tuple(row))
    with pytest.raises(NoticeGlossaryStorageError, match="저장된"):
        save_notice_glossary(conn, _progress(generated_at=generated_at))
    assert len(cursor.execute.call_args_list) == 2


@pytest.mark.parametrize(
    "index,value", [(0, 8), (1, "bad hash"), (2, ""), (3, datetime(2026, 10, 6)), (4, "unknown")]
)
def test_stale_generation_requires_typed_metadata_and_correct_notice_id(index, value) -> None:
    row = list(_obsolete_row(malformed=True))
    row[index] = value
    conn, _ = _mock_conn(tuple(row))
    with pytest.raises(NoticeGlossaryStorageError):
        get_notice_glossary(conn, 7, rules_version=RULES_VERSION)
    conn, cursor = _mock_conn(None, tuple(row))
    with pytest.raises(NoticeGlossaryStorageError):
        save_notice_glossary(conn, _progress(generated_at=_TIME + timedelta(seconds=1)))
    assert len(cursor.execute.call_args_list) == 2


@pytest.mark.parametrize("notice_id", [None, 0, -1, True, "7"])
def test_save_requires_positive_integer_notice_id(notice_id) -> None:
    conn, cursor = _mock_conn()
    invalid = _result().model_copy(update={"notice_id": notice_id})
    with pytest.raises(NoticeGlossaryStorageError):
        save_notice_glossary(conn, invalid)
    cursor.execute.assert_not_called()


def test_save_binds_json_and_leaves_caller_transaction_open() -> None:
    result = _progress()
    conn, cursor = _mock_conn((7,), _row(result))
    save_notice_glossary(conn, result)
    statement, values = cursor.execute.call_args_list[0].args
    assert "on conflict (notice_id) do nothing" in statement
    assert result.original_text not in statement
    assert values[:5] == _row(result)[:5]
    assert isinstance(values[5], Jsonb)
    assert values[5].obj == result.model_dump(mode="json")
    assert "for update" in cursor.execute.call_args_list[1].args[0]
    assert conn.transaction.called
    conn.commit.assert_not_called()
    conn.rollback.assert_not_called()
    conn.close.assert_not_called()


@pytest.mark.parametrize("error_code", _FAILURE_CODES)
def test_failure_code_is_preserved_in_json_binding_and_readback(error_code) -> None:
    saved = _failed_progress(error_code)
    conn, cursor = _mock_conn((7,), _row(saved))
    save_notice_glossary(conn, saved)
    payload = cursor.execute.call_args_list[0].args[1][5]
    assert isinstance(payload, Jsonb)
    assert payload.obj["queries"][1]["error_code"] == error_code
    assert payload.obj["terms"][1]["error_code"] == error_code
    assert payload.obj["queries"][1]["lookup"] is None
    row = (*_row(saved)[:5], json.loads(json.dumps(payload.obj)))
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = row
    restored = get_notice_glossary(
        conn, 7, source_hash=saved.source_hash, rules_version=RULES_VERSION
    )
    assert restored == saved
    assert restored.original_text == " \t익일 공람.\n"
    assert restored.easy_text == " \t다음 날 공람.\n"
    assert restored.status == "partial" and restored.pending_queries == ("공람",)
    assert restored.queries[0].lookup == saved.queries[0].lookup
    assert restored.queries[1].error_code == restored.terms[1].error_code == error_code
    assert restored.changes == saved.changes
    conn.commit.assert_not_called()
    conn.close.assert_not_called()


@pytest.mark.parametrize("error_code", _FAILURE_CODES)
def test_legacy_api_failure_can_update_and_complete_without_losing_success(error_code) -> None:
    legacy = _failed_progress("api")
    retry = _failed_progress(error_code, generated_at=_TIME + timedelta(seconds=1))
    conn, cursor = _mock_conn(None, _row(legacy))
    save_notice_glossary(conn, retry)
    assert cursor.execute.call_args.args[0].startswith("update public.notice_glossary_results")
    assert cursor.execute.call_args.args[1][-2].obj == retry.model_dump(mode="json")
    completed = _progress(complete=True, generated_at=_TIME + timedelta(seconds=2))
    conn, cursor = _mock_conn(None, _row(retry))
    save_notice_glossary(conn, completed)
    assert cursor.execute.call_args.args[1][-2].obj == completed.model_dump(mode="json")
    assert completed.queries[0] == legacy.queries[0] == retry.queries[0]
    assert completed.terms[0] == legacy.terms[0] == retry.terms[0]
    assert completed.changes == legacy.changes == retry.changes


@pytest.mark.parametrize("error_code", _FAILURE_CODES)
def test_detailed_failure_cannot_replace_completed_success(error_code) -> None:
    completed = _progress(complete=True)
    failed = _failed_progress(error_code, generated_at=_TIME + timedelta(seconds=1))
    conn, cursor = _mock_conn(None, _row(completed))
    with pytest.raises(NoticeGlossaryStorageError, match="완료된"):
        save_notice_glossary(conn, failed)
    assert len(cursor.execute.call_args_list) == 2
    conn.commit.assert_not_called()


def test_idle_connection_keeps_an_outer_transaction_for_caller() -> None:
    result = _result()
    conn, _ = _mock_conn((7,), _row(result))
    conn.info.transaction_status = TransactionStatus.IDLE
    save_notice_glossary(conn, result)
    conn.execute.assert_called_once_with("select 1")
    conn.commit.assert_not_called()


def test_autocommit_and_invalid_result_are_rejected_before_writes() -> None:
    conn, cursor = _mock_conn()
    conn.autocommit = True
    with pytest.raises(NoticeGlossaryStorageError, match="autocommit"):
        save_notice_glossary(conn, _result())
    with pytest.raises(NoticeGlossaryStorageError, match="올바르지"):
        save_notice_glossary(conn, _result().model_copy(update={"easy_text": "다른 내용"}))
    cursor.execute.assert_not_called()


@pytest.mark.parametrize("saved_time", [_TIME, _TIME + timedelta(seconds=1)])
def test_older_or_equal_snapshot_does_not_overwrite(saved_time) -> None:
    conn, cursor = _mock_conn(None, _row(_result(generated_at=saved_time)))
    save_notice_glossary(conn, _result())
    assert len(cursor.execute.call_args_list) == 2


def test_partial_progress_can_complete_without_losing_successes() -> None:
    stored = _progress()
    incoming = _progress(complete=True, generated_at=_TIME + timedelta(seconds=1))
    conn, cursor = _mock_conn(None, _row(stored))
    save_notice_glossary(conn, incoming)
    statement, values = cursor.execute.call_args_list[-1].args
    assert statement.startswith("update public.notice_glossary_results")
    assert values[-1] == incoming.notice_id
    assert values[-2].obj == incoming.model_dump(mode="json")


@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("stored_cached", [False, True])
def test_partial_progress_allows_only_cache_provenance_to_change(complete, stored_cached) -> None:
    previous = _progress()
    stored = NoticeGlossaryResult.model_validate(
        {
            **previous.model_dump(),
            "queries": (
                previous.queries[0].model_copy(
                    update={
                        "lookup": previous.queries[0].lookup.model_copy(
                            update={"from_cache": stored_cached}
                        )
                    }
                ),
            ),
        }
    )
    next_progress = _progress(complete=complete, generated_at=_TIME + timedelta(seconds=1))
    incoming = NoticeGlossaryResult.model_validate(
        {
            **next_progress.model_dump(),
            "queries": tuple(
                outcome.model_copy(
                    update={
                        "lookup": outcome.lookup.model_copy(
                            update={"from_cache": not stored_cached}
                        )
                    }
                )
                for outcome in next_progress.queries
            ),
        }
    )
    conn, cursor = _mock_conn(None, _row(stored))
    save_notice_glossary(conn, incoming)
    assert cursor.execute.call_args.args[0].startswith("update public.notice_glossary_results")
    assert cursor.execute.call_args.args[1][-2].obj == incoming.model_dump(mode="json")
    assert incoming.queries[0].lookup.entries == stored.queries[0].lookup.entries
    assert incoming.queries[0].lookup.queried_at == stored.queries[0].lookup.queried_at
    assert incoming.changes == stored.changes
    assert incoming.terms[0] == stored.terms[0]
    conn.commit.assert_not_called()


@pytest.mark.parametrize("changed", ["definition", "source_url", "queried_at", "providers_checked"])
def test_cache_provenance_change_does_not_allow_success_evidence_to_change(changed) -> None:
    stored = _progress()
    lookup = stored.queries[0].lookup.model_copy(update={"from_cache": True})
    if changed in {"definition", "source_url"}:
        value = (
            "갱신한 뜻풀이."
            if changed == "definition"
            else "https://opendict.korean.go.kr/word/999"
        )
        lookup = lookup.model_copy(
            update={"entries": (lookup.entries[0].model_copy(update={changed: value}),)}
        )
    else:
        value = _TIME + timedelta(seconds=1) if changed == "queried_at" else ("onterm", "opendict")
        lookup = lookup.model_copy(update={changed: value})
    incoming = NoticeGlossaryResult.model_validate(
        {
            **stored.model_dump(),
            "queries": (stored.queries[0].model_copy(update={"lookup": lookup}),),
            "terms": (
                stored.terms[0].model_copy(update={"entries": lookup.entries}),
                stored.terms[1],
            ),
            "generated_at": _TIME + timedelta(seconds=1),
        }
    )
    conn, cursor = _mock_conn(None, _row(stored))
    with pytest.raises(NoticeGlossaryStorageError, match="성공한 사전 조회"):
        save_notice_glossary(conn, incoming)
    assert len(cursor.execute.call_args_list) == 2
    conn.commit.assert_not_called()


def test_completed_snapshot_cannot_downgrade_to_partial() -> None:
    conn, cursor = _mock_conn(None, _row(_progress(complete=True)))
    with pytest.raises(NoticeGlossaryStorageError, match="완료된"):
        save_notice_glossary(conn, _progress(generated_at=_TIME + timedelta(seconds=1)))
    assert len(cursor.execute.call_args_list) == 2
    assert conn.transaction.return_value.__exit__.call_args.args[0] is NoticeGlossaryStorageError


def test_partial_snapshot_cannot_drop_a_successful_query() -> None:
    stored = _progress()
    incoming = _all_pending(stored)
    conn, _ = _mock_conn(None, _row(stored))
    with pytest.raises(NoticeGlossaryStorageError, match="성공한 사전 조회"):
        save_notice_glossary(conn, incoming)


def test_partial_snapshot_cannot_drop_a_confirmed_change() -> None:
    stored = _progress()
    terms = (
        stored.terms[0].model_copy(update={"status": "explained", "replacement": None}),
        stored.terms[1],
    )
    incoming = NoticeGlossaryResult.model_validate(
        {
            **stored.model_dump(),
            "terms": terms,
            "changes": (),
            "easy_text": stored.original_text,
            "generated_at": _TIME + timedelta(seconds=1),
        }
    )
    conn, _ = _mock_conn(None, _row(stored))
    with pytest.raises(NoticeGlossaryStorageError, match="용어 치환"):
        save_notice_glossary(conn, incoming)


def test_partial_snapshot_cannot_drop_a_confirmed_explanation() -> None:
    stored = _progress(first_replaced=False)
    incoming = NoticeGlossaryResult.model_validate(
        {
            **stored.model_dump(),
            "terms": (
                stored.terms[0].model_copy(update={"status": "pending", "entries": ()}),
                stored.terms[1],
            ),
            "pending_queries": ("익일", "공람"),
            "generated_at": _TIME + timedelta(seconds=1),
        }
    )
    conn, _ = _mock_conn(None, _row(stored))
    with pytest.raises(NoticeGlossaryStorageError, match="용어 설명"):
        save_notice_glossary(conn, incoming)


@pytest.mark.parametrize(
    "changes", [{"original_text": " \t!!\n"}, {"rules_version": "different-rules"}]
)
def test_new_source_or_rules_can_replace_an_old_generation(changes) -> None:
    conn, cursor = _mock_conn(None, _row(_progress(complete=True)))
    incoming = _result(generated_at=_TIME + timedelta(seconds=1), **changes)
    save_notice_glossary(conn, incoming)
    assert cursor.execute.call_args.args[0].startswith("update public.notice_glossary_results")


def test_db_write_failure_exits_savepoint_without_committing() -> None:
    conn, cursor = _mock_conn(None, _row(_result()))
    cursor.execute.side_effect = [None, None, psycopg.DatabaseError("synthetic failure")]
    with pytest.raises(psycopg.DatabaseError):
        save_notice_glossary(conn, _result(generated_at=_TIME + timedelta(seconds=1)))
    assert conn.transaction.return_value.__exit__.call_args.args[0] is psycopg.DatabaseError
    conn.commit.assert_not_called()
    conn.rollback.assert_not_called()


def _safe_test_database(database_url: str) -> dict[str, str]:
    info = conninfo_to_dict(database_url)
    if (
        info.get("host") not in {"localhost", "127.0.0.1", "::1"}
        or info.get("hostaddr") not in {None, "127.0.0.1", "::1"}
        or not info.get("dbname", "").startswith("pipeline_glossary_test_")
    ):
        raise ValueError("공지 용어 통합 테스트는 전용 로컬 테스트 DB에서만 실행할 수 있습니다.")
    return info


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://localhost/postgres",
        "postgresql://remote.example/pipeline_glossary_test_demo",
        "dbname=pipeline_glossary_test_demo",
        "host=localhost hostaddr=203.0.113.1 dbname=pipeline_glossary_test_demo",
    ],
)
def test_integration_target_must_be_dedicated_and_loopback(database_url) -> None:
    with pytest.raises(ValueError, match="전용 로컬"):
        _safe_test_database(database_url)


@pytest.fixture
def notice_glossary_db():
    database_url = os.getenv("GLOSSARY_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("GLOSSARY_TEST_DATABASE_URL 미설정: 전용 PostgreSQL 테스트 미실행")
    conn = psycopg.connect(**_safe_test_database(database_url))
    try:
        assert conn.execute("select to_regclass('public.notices')").fetchone()[0] is None
        for role in ("anon", "authenticated"):
            existing = conn.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone()
            if existing is None:
                conn.execute(f"create role {role} nologin")
        conn.execute("grant usage on schema public to anon, authenticated")
        conn.execute((_ROOT / "supabase/migrations/20260922053900_init.sql").read_text("utf-8"))
        conn.execute((_ROOT / "supabase/migrations/20260922053901_rls.sql").read_text("utf-8"))
        conn.execute(_MIGRATION.read_text("utf-8"))
        conn.execute(
            "insert into public.notices"
            "(category, source_board, post_sn, title, registered_on, url) "
            "values ('nowon', '1001', 'test', 'test', current_date, "
            "'https://www.nowon.kr/test')"
        )
        notice_id = conn.execute("select id from public.notices").fetchone()[0]
        yield conn, notice_id
    finally:
        conn.rollback()
        conn.close()


def test_db_roundtrip_stale_invalidation_and_resume(notice_glossary_db) -> None:
    conn, notice_id = notice_glossary_db
    first = _progress(notice_id=notice_id)
    save_notice_glossary(conn, first)
    assert get_notice_glossary(conn, notice_id) == first
    assert get_notice_glossary(conn, notice_id, source_hash="a" * 64) is None
    assert get_notice_glossary(conn, notice_id, rules_version="new-rules") is None
    complete = _progress(
        complete=True, notice_id=notice_id, generated_at=_TIME + timedelta(seconds=1)
    )
    save_notice_glossary(conn, complete)
    save_notice_glossary(conn, first)
    assert get_notice_glossary(conn, notice_id) == complete
    with pytest.raises(NoticeGlossaryStorageError, match="완료된"):
        save_notice_glossary(
            conn, _progress(notice_id=notice_id, generated_at=_TIME + timedelta(seconds=2))
        )
    assert get_notice_glossary(conn, notice_id) == complete


def test_db_partial_refresh_can_complete_using_the_same_cached_meaning(notice_glossary_db) -> None:
    conn, notice_id = notice_glossary_db
    conn.execute((_ROOT / "supabase/migrations/20261005000000_glossary.sql").read_text("utf-8"))
    onterm, opendict = MagicMock(), MagicMock()
    onterm.lookup.return_value = ()
    opendict.lookup.side_effect = lambda query: (_entry(),) if query == "익일" else ()
    source = NoticeGlossaryInput(notice_id=notice_id, text="익일 공람")
    first = process_and_store_notice_glossary(
        conn,
        source,
        max_queries=1,
        onterm_client=onterm,
        opendict_client=opendict,
        clock=lambda: _TIME,
    )
    assert first.status == "partial" and not first.queries[0].lookup.from_cache
    refreshed = process_and_store_notice_glossary(
        conn,
        source,
        max_queries=1,
        refresh=True,
        onterm_client=onterm,
        opendict_client=opendict,
        clock=lambda: _TIME + timedelta(seconds=1),
    )
    assert refreshed.status == "completed" and refreshed.new_query_count == 1
    assert refreshed.queries[0].lookup.from_cache
    assert refreshed.queries[0].lookup.entries == first.queries[0].lookup.entries
    assert refreshed.queries[0].lookup.queried_at == first.queries[0].lookup.queried_at
    assert refreshed.terms[0] == first.terms[0] and refreshed.changes == first.changes
    assert refreshed.original_text == first.original_text == "익일 공람"
    assert refreshed.easy_text == first.easy_text == "다음 날 공람"
    assert [call.args[0] for call in opendict.lookup.call_args_list] == ["익일", "공람"]
    assert get_notice_glossary(conn, notice_id) == refreshed


@pytest.mark.parametrize("error_code", _FAILURE_CODES)
def test_db_detailed_failure_roundtrip_and_legacy_api_retry_preserve_success(
    notice_glossary_db, error_code
) -> None:
    conn, notice_id = notice_glossary_db
    legacy = _failed_progress("api", notice_id=notice_id)
    save_notice_glossary(conn, legacy)
    assert get_notice_glossary(conn, notice_id) == legacy
    retry = _failed_progress(
        error_code, notice_id=notice_id, generated_at=_TIME + timedelta(seconds=1)
    )
    save_notice_glossary(conn, retry)
    payload = conn.execute(
        "select result from public.notice_glossary_results where notice_id = %s", (notice_id,)
    ).fetchone()[0]
    assert payload["terms"][1]["error_code"] == payload["queries"][1]["error_code"] == error_code
    restored = get_notice_glossary(conn, notice_id)
    assert restored == retry
    assert restored.queries[0] == legacy.queries[0]
    assert restored.terms[0] == legacy.terms[0]
    assert restored.changes == legacy.changes
    completed = _progress(
        complete=True, notice_id=notice_id, generated_at=_TIME + timedelta(seconds=2)
    )
    save_notice_glossary(conn, completed)
    assert get_notice_glossary(conn, notice_id) == completed
    save_notice_glossary(conn, retry)
    assert get_notice_glossary(conn, notice_id) == completed
    with pytest.raises(NoticeGlossaryStorageError, match="완료된"):
        save_notice_glossary(
            conn,
            _failed_progress(
                error_code, notice_id=notice_id, generated_at=_TIME + timedelta(seconds=3)
            ),
        )
    assert get_notice_glossary(conn, notice_id) == completed


def test_db_foreign_key_metadata_and_failed_write_preserve_saved_result(notice_glossary_db) -> None:
    conn, notice_id = notice_glossary_db
    saved = _progress(notice_id=notice_id)
    save_notice_glossary(conn, saved)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        save_notice_glossary(conn, _result(notice_id=notice_id + 1))
    assert get_notice_glossary(conn, notice_id) == saved
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute(
                "update public.notice_glossary_results set status = 'completed' "
                "where notice_id = %s",
                (notice_id,),
            )
    assert get_notice_glossary(conn, notice_id) == saved
    with pytest.raises(NoticeGlossaryStorageError, match="성공한"):
        save_notice_glossary(conn, _all_pending(saved))
    assert get_notice_glossary(conn, notice_id) == saved
    conn.execute("delete from public.notices where id = %s", (notice_id,))
    assert get_notice_glossary(conn, notice_id) is None


def test_db_failed_refresh_preserves_previous_snapshot(notice_glossary_db) -> None:
    conn, notice_id = notice_glossary_db
    saved = _progress(notice_id=notice_id)
    save_notice_glossary(conn, saved)
    conn.execute(
        "create function pg_temp.reject_notice_glossary_update() returns trigger "
        "language plpgsql as $$ begin raise exception 'synthetic refresh failure'; end; $$"
    )
    conn.execute(
        "create trigger reject_notice_glossary_update before update "
        "on public.notice_glossary_results for each row "
        "execute function pg_temp.reject_notice_glossary_update()"
    )
    incoming = _progress(
        complete=True, notice_id=notice_id, generated_at=_TIME + timedelta(seconds=1)
    )
    with pytest.raises(psycopg.errors.RaiseException):
        save_notice_glossary(conn, incoming)
    assert get_notice_glossary(conn, notice_id) == saved


def test_db_obsolete_rules_snapshot_is_ignored_and_replaced(notice_glossary_db) -> None:
    conn, notice_id = notice_glossary_db
    row = list(_obsolete_row())
    row[0] = notice_id
    row[5]["notice_id"] = notice_id
    conn.execute(
        "insert into public.notice_glossary_results "
        "(notice_id, source_hash, rules_version, generated_at, status, result) "
        "values (%s, %s, %s, %s, %s, %s)",
        (*row[:5], Jsonb(row[5])),
    )
    assert get_notice_glossary(conn, notice_id, rules_version=RULES_VERSION) is None
    current = _progress(notice_id=notice_id, generated_at=_TIME + timedelta(seconds=1))
    save_notice_glossary(conn, current)
    assert get_notice_glossary(conn, notice_id) == current


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_db_app_roles_can_only_read(notice_glossary_db, role) -> None:
    conn, notice_id = notice_glossary_db
    saved = _progress(notice_id=notice_id)
    save_notice_glossary(conn, saved)
    with conn.transaction():
        conn.execute(f"set local role {role}")
        assert get_notice_glossary(conn, notice_id) == saved
        for statement in (
            "delete from public.notice_glossary_results",
            "truncate public.notice_glossary_results",
            "update public.notice_glossary_results set status = 'completed'",
            "insert into public.notice_glossary_results "
            "select * from public.notice_glossary_results",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.transaction():
                    conn.execute(statement)
        conn.execute("reset role")


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_db_notice_result_visibility_follows_its_parent_notice(notice_glossary_db, role) -> None:
    conn, notice_id = notice_glossary_db
    saved = _progress(notice_id=notice_id)
    save_notice_glossary(conn, saved)
    for visible in (True, False, True):
        conn.execute(
            "update public.notices set is_visible = %s where id = %s", (visible, notice_id)
        )
        with conn.transaction():
            conn.execute(f"set local role {role}")
            assert get_notice_glossary(conn, notice_id) == (saved if visible else None)
            row = conn.execute(
                "select result ->> 'original_text' from public.notice_glossary_results "
                "where notice_id = %s",
                (notice_id,),
            ).fetchone()
            assert row == ((saved.original_text,) if visible else None)
            assert conn.execute(
                "select count(*) from public.notices where id = %s", (notice_id,)
            ).fetchone()[0] == int(visible)
            conn.execute("reset role")
