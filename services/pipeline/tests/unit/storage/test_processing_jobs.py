"""Real database queue races, independent cache versions, and bounded recovery."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from support.db import database_uri, owned_migrated_database
from support.easy_text_storage import _notice as easy_notice
from support.easy_text_storage import _result as easy_result
from support.summary_execution_storage import _completed, _metadata, _summary

from pipeline.config import DatabaseSettings
from pipeline.processing_runner import run_processing
from pipeline.processing_worker import process_claim
from pipeline.storage import processing_jobs
from pipeline.storage.notice_easy_text import save_notice_easy_text
from pipeline.storage.processing_context import processing_claim
from pipeline.storage.processing_jobs import (
    ProcessingClaimSuperseded,
    assert_active_claim,
    claim_next,
    enqueue_candidates,
    finish_claim,
    make_contract_key,
    retry_job,
    select_candidates,
)
from pipeline.storage.summaries import (
    begin_summary_execution,
    record_summary_failure,
    save_notice_summary,
)
from pipeline.transform.prepared_summary import PreparedSummaryResult

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def database() -> Iterator[dict[str, str]]:
    with owned_migrated_database(
        env="PIPELINE_TEST_DATABASE_URL", required_prefix="pipeline_schema_test_",
        missing="skip", missing_message="A disposable queue test database is required",
        invalid_message="Use a disposable loopback pipeline_schema_test_* database",
    ) as info:
        yield info


@pytest.fixture
def conn(database: dict[str, str]) -> Iterator[psycopg.Connection]:
    with psycopg.connect(**database, autocommit=True) as connection:
        yield connection
        connection.execute("delete from public.notices")


def _notice(conn: psycopg.Connection, *, visible: bool = True) -> int:
    return conn.execute(
        "insert into public.notices "
        "(category,source_board,post_sn,title,registered_on,url,body_html,is_visible) "
        "values ('nowon','1001',%s,'제목','2026-10-08','https://www.nowon.kr/test',"
        "'<p>본문</p>',%s) returning id", (uuid4().hex, visible),
    ).fetchone()[0]


def _summary_cache(conn: psycopg.Connection, notice_id: int) -> None:
    revision = conn.execute(
        "select content_revision from notices where id=%s", (notice_id,),
    ).fetchone()[0]
    result = {"category": "event", "category_code": 26, "summary": "행사 안내"}
    manifest = {"notice_id": notice_id, "source_revision": revision,
                "original_url": "https://www.nowon.kr/test", "files": [], "media": []}
    conn.execute(
        "insert into public.notice_summaries "
        "(notice_id,status,result,category,category_code,attachment_status,source_hash,"
        "model,prompt_version,generated_at,file_manifest) "
        "values (%s,'summarized',%s,'event',26,'none',%s,'gemini-3.5-flash-lite',"
        "'notice-summary-v6-card-grounding',%s,%s)",
        (notice_id, Jsonb(result), "a" * 64, NOW, Jsonb(manifest)),
    )


def _queue(conn: psycopg.Connection, *, features=("summary",)):
    candidates = select_candidates(conn, features=features, now=NOW)
    enqueue_candidates(conn, candidates, now=NOW)
    return candidates


def test_complete_rows_do_not_starve_older_missing_result(conn: psycopg.Connection) -> None:
    complete = [_notice(conn) for _ in range(4)]
    for notice_id in complete:
        _summary_cache(conn, notice_id)
    missing = _notice(conn)
    _notice(conn, visible=False)
    candidates = select_candidates(conn, features=("summary",), limit=1, now=NOW)
    assert [candidate.notice_id for candidate in candidates] == [missing]
    assert conn.execute("select count(*) from public.notice_processing_jobs").fetchone() == (0,)


def test_enqueuing_again_preserves_lease_and_retry_backoff(conn: psycopg.Connection) -> None:
    _notice(conn)
    candidates = _queue(conn)
    claim = claim_next(conn, now=NOW)
    assert claim is not None
    assert enqueue_candidates(conn, candidates, now=NOW) == 0
    assert claim_next(conn, now=NOW) is None
    later = NOW + timedelta(minutes=1)
    assert finish_claim(conn, claim, state="retry_wait", last_error_code="api_timeout",
                        next_attempt_at=later, now=NOW)
    assert enqueue_candidates(conn, candidates, now=NOW) == 0
    assert select_candidates(conn, features=("summary",), now=NOW) == []
    second = claim_next(conn, now=later)
    assert second is not None and second.attempts == 2


def test_expired_lease_is_recovered_and_late_completion_rejected(conn: psycopg.Connection) -> None:
    _notice(conn)
    _queue(conn)
    first = claim_next(conn, lease_seconds=5, now=NOW)
    later = NOW + timedelta(seconds=6)
    second = claim_next(conn, lease_seconds=5, now=later)
    assert first is not None and second is not None
    assert first.claim_token != second.claim_token and second.attempts == 2
    assert not finish_claim(conn, first, state="succeeded", now=later)
    with pytest.raises(ProcessingClaimSuperseded), conn.transaction():
        assert_active_claim(conn, first, now=later)
    assert finish_claim(conn, second, state="succeeded", now=later)


def test_exhausted_work_requires_manual_retry_or_new_input(conn: psycopg.Connection) -> None:
    notice_id = _notice(conn)
    _queue(conn)
    assert claim_next(conn, max_attempts=1, lease_seconds=1, now=NOW) is not None
    later = NOW + timedelta(seconds=2)
    transitions = []
    assert claim_next(conn, max_attempts=1, transitions=transitions, now=later) is None
    assert transitions == [{
        "notice_id": notice_id, "feature": "summary", "state": "exhausted", "attempts": 1,
        "reason_code": "processing_lease_expired", "next_attempt_at": None, "recovered": True,
    }]
    assert conn.execute("select state,attempts from notice_processing_jobs").fetchone() == (
        "exhausted", 1,
    )
    assert select_candidates(conn, features=("summary",), now=later) == []
    assert retry_job(conn, notice_id, "summary", now=later)
    assert claim_next(conn, now=later).attempts == 1


def test_source_change_and_hidden_notice_reject_old_fence(conn: psycopg.Connection) -> None:
    notice_id = _notice(conn)
    _queue(conn)
    claim = claim_next(conn, now=NOW)
    conn.execute("update notices set body_html='<p>변경</p>' where id=%s", (notice_id,))
    assert not finish_claim(conn, claim, state="succeeded", now=NOW)
    new_candidates = _queue(conn)
    assert new_candidates[0].input_version != claim.input_version
    current = claim_next(conn, now=NOW)
    assert current.attempts == 1
    conn.execute("update notices set is_visible=false where id=%s", (notice_id,))
    assert not finish_claim(conn, current, state="succeeded", now=NOW)
    assert select_candidates(conn, features=("summary",), now=NOW) == []


def test_attachment_revision_does_not_change_easy_text_input(conn: psycopg.Connection) -> None:
    notice_id = _notice(conn)
    before = {c.feature: c for c in _queue(conn, features=("summary", "easy_text"))}
    conn.execute("update notices set content_revision=content_revision+1 where id=%s", (notice_id,))
    after = {c.feature: c for c in select_candidates(conn, now=NOW)}
    assert before["summary"].input_version != after["summary"].input_version
    assert before["easy_text"].input_version == after["easy_text"].input_version


def test_deleted_success_cache_is_reenqueued(conn: psycopg.Connection) -> None:
    notice_id = _notice(conn)
    _queue(conn)
    claim = claim_next(conn, now=NOW)
    _summary_cache(conn, notice_id)
    assert finish_claim(conn, claim, state="succeeded", now=NOW)
    assert select_candidates(conn, features=("summary",), now=NOW) == []
    conn.execute("delete from notice_summaries where notice_id=%s", (notice_id,))
    assert len(_queue(conn)) == 1
    assert claim_next(conn, now=NOW).attempts == 1


def test_completed_result_after_crash_is_reused_without_another_claim(
    conn: psycopg.Connection,
) -> None:
    notice_id = _notice(conn)
    _queue(conn)
    claim_next(conn, lease_seconds=1, now=NOW)
    _summary_cache(conn, notice_id)
    transitions = []
    assert claim_next(conn, transitions=transitions, now=NOW + timedelta(seconds=2)) is None
    assert transitions == [{
        "notice_id": notice_id, "feature": "summary", "state": "succeeded", "attempts": 1,
        "reason_code": None, "next_attempt_at": None, "recovered": True,
    }]
    assert conn.execute("select state,attempts from notice_processing_jobs").fetchone() == (
        "succeeded", 1,
    )


def test_two_workers_only_claim_once(conn: psycopg.Connection, database: dict[str, str]) -> None:
    _notice(conn)
    _queue(conn)

    def run():
        with psycopg.connect(**database, autocommit=True) as worker:
            return claim_next(worker, now=NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: run(), range(2)))
    assert sum(claim is not None for claim in claims) == 1


def test_claim_on_idle_non_autocommit_connection_releases_locks(
    conn: psycopg.Connection, database: dict[str, str],
) -> None:
    _notice(conn)
    _queue(conn)
    with psycopg.connect(**database) as worker:
        assert claim_next(worker, now=NOW) is not None
        assert worker.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    assert conn.execute("select state from notice_processing_jobs").fetchone() == ("running",)


def test_claim_filters_contract_and_changed_contract_releases_blocked_job(
    conn: psycopg.Connection,
) -> None:
    _notice(conn)
    old = _queue(conn)[0]
    assert claim_next(conn, contract_keys={"summary": make_contract_key("other", "v1")},
                      now=NOW) is None
    first = claim_next(conn, contract_keys={"summary": old.contract_key}, now=NOW)
    assert finish_claim(conn, first, state="blocked",
                        last_error_code="configuration_error", now=NOW)
    assert select_candidates(conn, features=("summary",), now=NOW) == []
    assert enqueue_candidates(conn, [old], now=NOW) == 0
    changed = select_candidates(conn, features=("summary",), summary_model="new-model", now=NOW)
    assert enqueue_candidates(conn, changed, now=NOW) == 1
    current = claim_next(conn, contract_keys={"summary": changed[0].contract_key}, now=NOW)
    assert current.attempts == 1 and current.contract_key != old.contract_key


def test_features_fail_independently_and_skipped_does_not_loop(conn: psycopg.Connection) -> None:
    _notice(conn)
    _queue(conn, features=("summary", "easy_text"))
    summary = claim_next(conn, features=("summary",), now=NOW)
    easy = claim_next(conn, features=("easy_text",), now=NOW)
    assert finish_claim(conn, summary, state="blocked", last_error_code="api_error", now=NOW)
    assert finish_claim(conn, easy, state="skipped", last_error_code="no_notice_body", now=NOW)
    assert select_candidates(conn, now=NOW) == []
    assert conn.execute(
        "select feature,state from notice_processing_jobs order by feature"
    ).fetchall() == [("easy_text", "skipped"), ("summary", "blocked")]


@pytest.mark.parametrize("operation", ["register", "success", "failure"])
@pytest.mark.parametrize("reclaimed", [False, True])
def test_late_summary_writer_cannot_change_result_or_execution_registry(
    conn: psycopg.Connection, operation: str, reclaimed: bool,
) -> None:
    notice_id = _notice(conn)
    _queue(conn)
    actual_now = datetime.now(UTC)
    old = claim_next(conn, now=actual_now)
    token = begin_summary_execution(conn, notice_id, expected_source_revision=1)
    record = _completed(notice_id, "본문", "2026-10-20", execution_token=token)
    save_notice_summary(conn, record)
    before = conn.execute("select * from notice_summaries").fetchall()
    registry = conn.execute("select * from notice_summary_executions").fetchall()
    conn.execute("update notice_processing_jobs set lease_expires_at=%s", (actual_now,))
    if reclaimed:
        assert claim_next(conn) is not None
    with processing_claim(old), pytest.raises(ProcessingClaimSuperseded):
        if operation == "register":
            begin_summary_execution(conn, notice_id, expected_source_revision=1)
        elif operation == "success":
            save_notice_summary(conn, record)
        else:
            record_summary_failure(conn, notice_id, _metadata("본문"), reason_code="api_timeout",
                                   execution_token=token)
    assert conn.execute("select * from notice_summaries").fetchall() == before
    assert conn.execute("select * from notice_summary_executions").fetchall() == registry


@pytest.mark.parametrize("reclaimed", [False, True])
def test_late_easy_writer_cannot_replace_public_result(
    conn: psycopg.Connection, database: dict[str, str], reclaimed: bool,
) -> None:
    with psycopg.connect(**database) as writer:
        source = easy_notice(writer)
        previous = easy_result(source)
        save_notice_easy_text(writer, previous)
        writer.commit()
        # Force an intentional new contract so the existing good result is kept
        # while the queued refresh is waiting/running.
        candidates = select_candidates(conn, features=("easy_text",),
                                       easy_text_model="new-model", now=NOW)
        enqueue_candidates(conn, candidates, now=NOW)
        actual_now = datetime.now(UTC)
        old = claim_next(conn, now=actual_now)
        before = conn.execute("select * from notice_easy_texts").fetchall()
        conn.execute("update notice_processing_jobs set lease_expires_at=%s", (actual_now,))
        if reclaimed:
            assert claim_next(conn) is not None
        later = easy_result(source, now=NOW + timedelta(days=1))
        with processing_claim(old), pytest.raises(ProcessingClaimSuperseded):
            save_notice_easy_text(writer, later)
        writer.rollback()
        assert conn.execute("select * from notice_easy_texts").fetchall() == before


def test_lease_expiring_while_waiting_for_job_lock_is_rejected(
    conn: psycopg.Connection, database: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _notice(conn)
    _queue(conn)
    claim = claim_next(conn, now=NOW)
    current = [NOW]
    monkeypatch.setattr(
        processing_jobs, "_now", lambda value: current[0] if value is None else value,
    )
    started = Event()
    worker_pids: list[int] = []

    def delayed_write():
        with psycopg.connect(**database, autocommit=True) as worker, worker.transaction():
            worker_pids.append(worker.info.backend_pid)
            started.set()
            assert_active_claim(worker, claim)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with conn.transaction():
            conn.execute("select notice_id from notice_processing_jobs for update")
            future = pool.submit(delayed_write)
            assert started.wait(2)
            deadline = monotonic() + 2
            while monotonic() < deadline:
                with psycopg.connect(**database, autocommit=True) as observer:
                    waiting = observer.execute(
                        "select wait_event_type from pg_stat_activity where pid=%s",
                        (worker_pids[0],),
                    ).fetchone()
                if waiting == ("Lock",):
                    break
                sleep(0.01)
            else:
                pytest.fail("The writer did not reach its queue lock")
            current[0] = claim.lease_expires_at + timedelta(seconds=1)
        with pytest.raises(ProcessingClaimSuperseded):
            future.result(timeout=2)


@pytest.mark.parametrize("reason,state", [
    ("api_timeout", "retry_wait"),
    ("response_validation_failed", "blocked"),
    ("input_preparation_failed", "blocked"),
])
def test_preserved_legacy_summary_does_not_reset_failed_generation_attempts(
    conn: psycopg.Connection, database: dict[str, str], monkeypatch: pytest.MonkeyPatch,
    reason: str, state: str,
) -> None:
    notice_id = _notice(conn)
    save_notice_summary(conn, _completed(notice_id, "본문", "2026-10-20"))
    before = conn.execute(
        "select result,model,prompt_version from notice_summaries where notice_id=%s", (notice_id,),
    ).fetchone()
    calls = []

    def corrected_candidate(prepared, **kwargs):
        calls.append(prepared.notice_id)
        return PreparedSummaryResult(
            notice_id=prepared.notice_id, summary=_summary("2026-10-20"), warnings=(),
            correction_failure_code=reason, file_manifest=kwargs["file_manifest"],
        )

    monkeypatch.setattr("pipeline.summary_job.summarize_prepared_notice", corrected_candidate)
    settings = DatabaseSettings(database_uri(database))

    def execute(database, claim, api_key, timeout):
        return process_claim(database, claim, api_key=api_key)

    first = run_processing(settings, api_key="synthetic", features=("summary",), executor=execute)
    assert first.records[0]["state"] == state
    second = run_processing(settings, api_key="synthetic", features=("summary",), executor=execute)
    assert second.report()["attempted_count"] == 0
    assert calls == [notice_id]
    assert conn.execute(
        "select state,attempts,last_error_code from notice_processing_jobs"
    ).fetchone() == (state, 1, reason)
    assert conn.execute(
        "select result,model,prompt_version from notice_summaries where notice_id=%s", (notice_id,),
    ).fetchone() == before
    if state == "retry_wait":
        conn.execute("update notice_processing_jobs set next_attempt_at=now()-interval '1 second'")
        final = run_processing(
            settings, api_key="synthetic", features=("summary",), max_attempts=2, executor=execute,
        )
        assert final.records[0]["state"] == "exhausted"
        assert final.records[0]["attempts"] == 2
        again = run_processing(
            settings, api_key="synthetic", features=("summary",), executor=execute,
        )
        assert again.report()["attempted_count"] == 0
        assert calls == [notice_id, notice_id]
        assert conn.execute(
            "select result,model,prompt_version from notice_summaries where notice_id=%s",
            (notice_id,),
        ).fetchone() == before


@pytest.mark.parametrize("category", ["nowon", "dong", "seoul"])
def test_collection_scope_selects_and_claims_only_its_source(conn, category):
    ids = {}
    for source in ("nowon", "dong", "seoul"):
        notice_id = _notice(conn)
        conn.execute(
            "update notices set category=%s, dong_group=%s, source_board=%s where id=%s",
            (source, "wolgye1" if source == "dong" else None,
             {"nowon": "1001", "dong": "1042", "seoul": "25"}[source], notice_id),
        )
        ids[source] = notice_id
    candidates = select_candidates(conn, features=("summary",), source=category)
    assert [item.notice_id for item in candidates] == [ids[category]]
    # Pre-existing jobs from another source must not be stolen by this collector.
    enqueue_candidates(conn, select_candidates(conn, features=("summary",)))
    claim = claim_next(conn, features=("summary",), source=category)
    assert claim.notice_id == ids[category]
    assert claim_next(conn, features=("summary",), source=category) is None


def test_readiness_includes_backoff_stopped_skipped_and_missing_cached_results(conn):
    ids = [_notice(conn) for _ in range(6)]
    enqueue_candidates(conn, select_candidates(conn, features=("summary",)))
    for state in ("retry_wait", "blocked", "skipped"):
        claim = claim_next(conn, features=("summary",), now=NOW)
        assert claim is not None
        finish_claim(
            conn, claim, state=state, last_error_code="api_timeout",
            next_attempt_at=NOW + timedelta(days=1) if state == "retry_wait" else None,
            now=NOW,
        )
    _summary_cache(conn, ids[3])
    counts = processing_jobs.readiness_counts(conn, feature="summary", source="nowon")
    assert counts == {"ready": 1, "skipped": 1, "pending": 2, "running": 0,
                      "retry_wait": 1, "blocked": 1, "exhausted": 0}
    conn.execute("delete from notice_summaries where notice_id=%s", (ids[3],))
    assert processing_jobs.readiness_counts(conn, feature="summary", source="nowon")["pending"] == 3


@pytest.mark.parametrize("source,category", [
    ("nowon", "nowon"), ("wolgye1", "dong"), ("seoul", "seoul"),
])
@pytest.mark.parametrize("unsupported_file", [False, True])
def test_automatic_collection_without_body_keeps_public_original_and_skips_ai(
    conn, database, monkeypatch, source, category, unsupported_file,
):
    from pipeline import collection_processing, processing_runner

    notice_id = _notice(conn)
    conn.execute(
        "update notices set body_html=null, category=%s, dong_group=%s, "
        "source_board=%s where id=%s",
        (category, "wolgye1" if category == "dong" else None,
         {"nowon": "1001", "dong": "1042", "seoul": "25"}[category], notice_id),
    )
    if unsupported_file:
        conn.execute(
            "insert into notice_files (notice_id,kind,file_id,file_key,file_name,url) "
            "values (%s,'attachment','unsupported','id:unsupported','unsupported.zip',"
            "'https://www.nowon.kr/unsupported.zip')", (notice_id,),
        )
    monkeypatch.setattr(collection_processing, "load_gemini_api_key", lambda: "fake")
    monkeypatch.setattr(processing_runner, "execute_claim", lambda db, claim, key, timeout:
                        process_claim(db, claim, api_key=key))
    processor = collection_processing.create_ai_processing(
        DatabaseSettings(database_uri(database)), source=source,
    )
    processor(notice_id)
    processor.finish()
    reports = processor.report()
    assert not processor.complete, str(reports)
    for feature, reason in (
        ("summary", "input_preparation_failed"), ("easy_text", "no_body_text"),
    ):
        report = reports[feature]
        state = "blocked" if feature == "summary" else "skipped"
        assert report["succeeded_count"] == 0 and report[state + "_count"] == 1
        assert report["records"][0]["reason_code"] == reason
        assert report["readiness"][state] == 1
        assert report["published"][0]["id"] == notice_id
        assert report["published"][0]["url"] == "https://www.nowon.kr/test"
    # A subsequent empty collection does not reattempt the same unsupported input.
    again = collection_processing.create_ai_processing(
        DatabaseSettings(database_uri(database)), source=source,
    )
    again.finish()
    assert not again.complete
    assert all(r["attempted_count"] == 0 for r in again.report().values())


def test_short_notice_blocked_job_recovers_only_on_explicit_retry(conn, database, monkeypatch):
    import html
    import json

    from support.paths import FIXTURES_DIR

    from pipeline.processing_runner import ProcessingOutcome

    fixture = json.loads((FIXTURES_DIR / "short_notice_action_card.json").read_text("utf-8"))
    notice_id = _notice(conn)
    original = fixture["notice"]
    conn.execute("update notices set title=%s,body_html=%s where id=%s", (
        original["title"], "<p>" + html.escape(original["body_text"]) + "</p>", notice_id,
    ))
    settings = DatabaseSettings(database_uri(database))
    first = run_processing(
        settings, api_key="test", notice_id=notice_id, features=("summary",),
        executor=lambda *args: ProcessingOutcome("failed", "response_validation_failed"),
    )
    assert first.records[0]["state"] == "blocked"
    corrected = fixture["responses"][1]
    corrected["card_summaries"]["action"] = "안내 장소는 노원수학문화관이에요."
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        return json.dumps(corrected, ensure_ascii=False)

    monkeypatch.setattr("pipeline.transform.summarize.generate_summary_json", generate)

    def execute(database, claim, api_key, timeout):
        return process_claim(database, claim, api_key=api_key)

    assert run_processing(settings, api_key="test", notice_id=notice_id, features=("summary",),
                          executor=execute).report()["attempted_count"] == 0
    assert not calls
    retried = run_processing(settings, api_key="test", notice_id=notice_id, features=("summary",),
                             retry_stopped=True, executor=execute)
    assert retried.records[0]["state"] == "succeeded"
    assert calls
    with psycopg.connect(**database) as reader:
        reader.execute("set local role anon")
        row = reader.execute("select headline,display_status from app_notice_detail where id=%s",
                             (notice_id,)).fetchone()
    assert row[0] and row[1] == "needs_review"


def test_easy_text_is_ready_only_with_a_current_rewrite(conn, database) -> None:
    # #85: a row with the current prompt but no easy_result (for example one an old
    # worker overwrote) is not a finished conversion and is selected again.
    with psycopg.connect(**database) as writer:
        source = easy_notice(writer)
        writer.commit()
        save_notice_easy_text(writer, easy_result(source))
        writer.commit()
    assert select_candidates(conn, features=("easy_text",), now=NOW) == []
    conn.execute("update notice_easy_texts set easy_result = null")
    conn.execute("update notice_easy_texts set dictionary_candidates = '[]'::jsonb")
    assert conn.execute(
        "select prompt_version, dictionary_candidates from notice_easy_texts"
    ).fetchone() == (processing_jobs.EASY_TEXT_PROMPT_VERSION, [])
    assert [c.notice_id for c in select_candidates(conn, features=("easy_text",), now=NOW)] == [
        source.notice_id
    ]
