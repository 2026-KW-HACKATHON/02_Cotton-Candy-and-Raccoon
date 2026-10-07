"""Three distinct audits: SQL privileges, hostile DB schedules, public job replay."""

import json
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from threading import Barrier, Event
from time import monotonic
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from test_gemini_multimodal import PreparedInput, _media
from test_summary_execution_storage import (
    NEW_BODY,
    OLD_BODY,
    _completed,
    _metadata,
    _row,
    _summary,
)
from test_summary_execution_storage import (
    _notice as _base_notice,
)
from test_threepass_collection_audit import EMPTY_ATTACHMENTS, _nowon

from pipeline.collect_nowon import collect_and_save_nowon
from pipeline.collect_wolgye1 import WolgyeListing, collect_and_save_wolgye1
from pipeline.config import DatabaseSettings, NowonSettings, WolgyeSettings
from pipeline.models import RawNotice
from pipeline.sources.nowon_api import NowonCollection, PageStatus
from pipeline.sources.nowon_page import NowonPageError
from pipeline.sources.wolgye1_board import BoardEntry, WolgyeSourceError
from pipeline.storage.summaries import (
    SummaryExecutionSuperseded,
    SummaryStorageError,
    begin_summary_execution,
    record_summary_failure,
    save_notice_summary,
)
from pipeline.storage.summary_metadata import compute_source_hash
from pipeline.storage.summary_record import SummaryMetadata, SummaryRecord
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.summary_job import (
    StoredSummaryFailure,
    StoredSummarySuperseded,
    summarize_and_save_prepared_notice,
)
from pipeline.transform import summarize as summarize_module
from pipeline.transform.gemini_client import GeminiRequestError
from pipeline.transform.gemini_prompt import SUMMARY_PROMPT_VERSION
from pipeline.transform.notice_input import NoticeInput, render_notice_input

_OWNED_NOTICES: dict[int, list[int]] = {}


def _notice(conn: psycopg.Connection) -> int:
    notice_id = _base_notice(conn)
    _OWNED_NOTICES[id(conn)].append(notice_id)
    return notice_id


@pytest.fixture
def live_db() -> Iterator[psycopg.Connection]:
    dsn = os.environ.get("AUDIT_TEST_DATABASE_URL") or os.environ.get("PIPELINE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("A disposable AUDIT_TEST_DATABASE_URL is required")
    conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True, connect_timeout=5)
    if conn.info.host != "127.0.0.1" or not conn.info.dbname.startswith("pipeline_schema_test_"):
        conn.close()
        pytest.fail("Use a disposable loopback pipeline_schema_test_* database")
    owned = _OWNED_NOTICES[id(conn)] = []
    try:
        yield conn
    finally:
        conn.execute("reset role")
        conn.execute("delete from notices where id=any(%s)", (owned,))
        _OWNED_NOTICES.pop(id(conn))
        conn.close()


class TestPassOneSqlContract:
    @pytest.mark.parametrize("role", ["anon", "authenticated"])
    def test_app_cannot_read_registry_sequence_or_private_summary_columns(
        self, live_db: psycopg.Connection, role: str,
    ) -> None:
        live_db.execute("set role " + role)
        for query in (
            "select * from notice_summary_executions",
            "select last_value from notice_summary_execution_token_seq",
            "select source_hash from notice_summaries",
            "select model,prompt_version,attempt_count,last_error_code from notice_summaries",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                live_db.execute(query)

    @pytest.mark.parametrize("role", ["anon", "authenticated"])
    @pytest.mark.parametrize("status", ["summarized", "needs_review"])
    def test_visible_review_content_is_readable_but_hidden_parent_filters_it(
        self, live_db: psycopg.Connection, role: str, status: str,
    ) -> None:
        notice_id = _notice(live_db)
        summary = _completed(notice_id, OLD_BODY, "2026-10-20")
        if status == "needs_review":
            summary = replace(summary, status="needs_review", deadline_on=None)
        save_notice_summary(live_db, summary)
        live_db.execute("set role " + role)
        assert live_db.execute(
            "select notice_id,status,result,card_summaries "
            "from notice_summaries where notice_id=%s",
            (notice_id,),
        ).fetchone()["notice_id"] == notice_id
        live_db.execute("reset role")
        live_db.execute("update notices set is_visible=false where id=%s", (notice_id,))
        live_db.execute("set role " + role)
        assert live_db.execute(
            "select notice_id,status,result,card_summaries "
            "from notice_summaries where notice_id=%s",
            (notice_id,),
        ).fetchone() is None

    def test_service_role_without_bypassrls_can_register_and_save(self, live_db) -> None:
        notice_id = _notice(live_db)
        live_db.execute("set role service_role")
        token = begin_summary_execution(live_db, notice_id)
        assert save_notice_summary(
            live_db, _completed(notice_id, OLD_BODY, "2026-10-20", execution_token=token)
        ) == notice_id
        flags = live_db.execute(
            "select rolsuper,rolbypassrls from pg_roles where rolname='service_role'"
        )
        assert flags.fetchone() == {"rolsuper": False, "rolbypassrls": False}


class TestPassTwoHostileSchedules:
    def test_opposite_file_moves_lock_parent_notices_in_the_same_order(self, live_db):
        parents = [_notice(live_db), _notice(live_db)]
        files = []
        for index, parent in enumerate(parents):
            files.append(live_db.execute(
                "insert into notice_files(notice_id,kind,file_id,file_key,url) "
                "values (%s,'attachment',%s,%s,'https://www.nowon.kr/file') returning id",
                (parent, "move-" + str(index), "id:move-" + str(index)),
            ).fetchone()["id"])
            save_notice_summary(live_db, _completed(parent, OLD_BODY, "2026-10-20"))
        before = live_db.execute(
            "select id,content_revision from notices where id=any(%s) order by id", (parents,),
        ).fetchall()
        barrier = Barrier(2)

        def move(index):
            with psycopg.connect(live_db.info.dsn) as worker:
                worker.execute("set local lock_timeout='3s'")
                barrier.wait(timeout=5)
                worker.execute(
                    "update notice_files set notice_id=%s where id=%s",
                    (parents[1 - index], files[index]),
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(move, index) for index in range(2)]
            for future in futures:
                future.result(timeout=10)
        after = live_db.execute(
            "select id,content_revision from notices where id=any(%s) order by id", (parents,),
        ).fetchall()
        assert [row["content_revision"] for row in after] == [
            row["content_revision"] + 2 for row in before
        ]
        assert all(_row(live_db, parent)["result"] is None for parent in parents)

    def test_rollback_of_bad_file_change_restores_source_revision_and_public_summary(self, live_db):
        notice_id = _notice(live_db)
        save_notice_summary(live_db, _completed(notice_id, OLD_BODY, "2026-10-20"))
        before = _row(live_db, notice_id)
        version = live_db.execute(
            "select body_html,content_revision from notices where id=%s", (notice_id,),
        ).fetchone()
        with pytest.raises(psycopg.errors.CheckViolation):
            with live_db.transaction():
                live_db.execute(
                    "update notices set body_html=%s where id=%s", (NEW_BODY, notice_id)
                )
                assert _row(live_db, notice_id)["result"] is None
                live_db.execute(
                    "insert into notice_files(notice_id,kind,file_key,url) "
                    "values (%s,'attachment','invalid','https://www.nowon.kr/file')", (notice_id,),
                )
        assert _row(live_db, notice_id) == before
        assert live_db.execute(
            "select body_html,content_revision from notices where id=%s", (notice_id,),
        ).fetchone() == version

    def test_source_edit_waits_for_guard_then_invalidates_just_stored_result(self, live_db):
        notice_id = _notice(live_db)
        token = begin_summary_execution(live_db, notice_id)
        ready = Event()
        backend: list[int] = []

        def collect_new_source():
            with psycopg.connect(live_db.info.dsn, autocommit=True) as collector:
                backend.append(collector.info.backend_pid)
                ready.set()
                collector.execute(
                    "update notices set body_html=%s where id=%s", (NEW_BODY, notice_id),
                )

        with psycopg.connect(live_db.info.dsn) as writer:
            save_notice_summary(writer, _completed(
                notice_id, OLD_BODY, "2026-10-20", execution_token=token,
            ))
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(collect_new_source)
                try:
                    assert ready.wait(5)
                    until = monotonic() + 5
                    waiting = False
                    while monotonic() < until:
                        waiting = live_db.execute(
                            "select wait_event_type='Lock' as waiting "
                            "from pg_stat_activity where pid=%s", (backend[0],),
                        ).fetchone()["waiting"]
                        if waiting:
                            break
                        Event().wait(0.02)
                    assert waiting, "Source edit must wait for the guarded publication lock"
                    writer.commit()
                finally:
                    writer.rollback()
                future.result(timeout=10)
        assert _row(live_db, notice_id)["status"] == "needs_review"
        assert _row(live_db, notice_id)["result"] is None

    @pytest.mark.parametrize("column,new_value", [
        ("title", "제목 변경"), ("body_html", NEW_BODY), ("department", "담당부서 변경"),
        ("registered_on", "2026-10-01"), ("url", "https://www.nowon.kr/revised"),
        ("license_type", "KOGL-1"),
    ])
    def test_source_edit_immediately_withholds_old_content_without_counting_a_job(
        self, live_db, column, new_value,
    ):
        notice_id = _notice(live_db)
        save_notice_summary(live_db, _completed(notice_id, OLD_BODY, "2026-10-20"))
        before = _row(live_db, notice_id)
        version = live_db.execute(
            "select content_revision from notices where id=%s", (notice_id,)
        ).fetchone()["content_revision"]
        # Column names are controlled by this finite test matrix, not external data.
        live_db.execute("update notices set " + column + "=%s where id=%s", (new_value, notice_id))
        after = _row(live_db, notice_id)
        assert after["status"] == "needs_review"
        for field in ("result", "card_summaries", "category", "category_code", "deadline_on"):
            assert after[field] is None
        for field in ("source_hash", "model", "prompt_version", "attachment_status",
                      "generated_at", "last_error_code", "attempt_count"):
            assert after[field] == before[field]
        assert live_db.execute(
            "select content_revision from notices where id=%s", (notice_id,)
        ).fetchone()["content_revision"] == version + 1
        live_db.execute("set role anon")
        assert live_db.execute(
            "select result,card_summaries,deadline_on from notice_summaries where notice_id=%s",
            (notice_id,),
        ).fetchone() == {"result": None, "card_summaries": None, "deadline_on": None}

    def test_same_transaction_edits_distinguish_versions_even_when_timestamps_match(self, live_db):
        notice_id = _notice(live_db)
        with psycopg.connect(live_db.info.dsn, row_factory=dict_row) as worker:
            worker.execute("update notices set body_html=%s where id=%s", (NEW_BODY, notice_id))
            version = worker.execute(
                "select content_revision,content_updated_at from notices where id=%s", (notice_id,)
            ).fetchone()
            token = begin_summary_execution(worker, notice_id)
            worker.execute("update notices set body_html=%s where id=%s", (OLD_BODY, notice_id))
            changed = worker.execute(
                "select content_revision,content_updated_at from notices where id=%s", (notice_id,)
            ).fetchone()
            assert changed["content_updated_at"] == version["content_updated_at"]
            assert changed["content_revision"] == version["content_revision"] + 1
            with pytest.raises(SummaryExecutionSuperseded):
                save_notice_summary(worker, _completed(
                    notice_id, NEW_BODY, "2026-11-30", execution_token=token,
                ))

    def test_unchanged_source_collection_and_visibility_keep_version_and_summary(self, live_db):
        notice_id = _notice(live_db)
        save_notice_summary(live_db, _completed(notice_id, OLD_BODY, "2026-10-20"))
        before = _row(live_db, notice_id)
        revision = live_db.execute(
            "select content_revision from notices where id=%s", (notice_id,)
        ).fetchone()["content_revision"]
        live_db.execute(
            "update notices set title=title,body_html=body_html,is_visible=false,"
            "updated_at=clock_timestamp() where id=%s", (notice_id,),
        )
        assert _row(live_db, notice_id) == before
        assert live_db.execute(
            "select content_revision from notices where id=%s", (notice_id,)
        ).fetchone()["content_revision"] == revision

    @pytest.mark.parametrize("role", [None, "service_role"])
    def test_file_insert_noop_update_changed_update_and_delete_invalidate_parent(
        self, live_db, role,
    ):
        notice_id = _notice(live_db)
        if role:
            live_db.execute("set role " + role)

        def version():
            return live_db.execute(
                "select content_revision from notices where id=%s", (notice_id,)
            ).fetchone()["content_revision"]

        def publish():
            token = begin_summary_execution(live_db, notice_id)
            save_notice_summary(live_db, _completed(
                notice_id, OLD_BODY, "2026-10-20", execution_token=token,
            ))

        publish()
        initial = version()
        file_id = live_db.execute(
            "insert into notice_files(notice_id,kind,file_id,file_key,file_name,url) "
            "values (%s,'attachment','audit','id:audit','name','https://www.nowon.kr/a.pdf') "
            "returning id", (notice_id,),
        ).fetchone()["id"]
        assert version() == initial + 1 and _row(live_db, notice_id)["result"] is None
        publish()
        unchanged = version()
        live_db.execute("update notice_files set file_name=file_name where id=%s", (file_id,))
        assert version() == unchanged and _row(live_db, notice_id)["result"] is not None
        live_db.execute("update notice_files set file_name='changed' where id=%s", (file_id,))
        assert version() == unchanged + 1 and _row(live_db, notice_id)["result"] is None
        publish()
        live_db.execute("delete from notice_files where id=%s", (file_id,))
        assert version() == unchanged + 2 and _row(live_db, notice_id)["result"] is None

    @pytest.mark.parametrize("late_outcome", ["success", "failure", "pending"])
    def test_changed_source_without_new_execution_rejects_old_outcome(self, live_db, late_outcome):
        notice_id = _notice(live_db)
        token = begin_summary_execution(live_db, notice_id)
        with psycopg.connect(live_db.info.dsn, autocommit=True) as collector:
            collector.execute(
                "update notices set body_html=%s,content_updated_at=clock_timestamp() where id=%s",
                (NEW_BODY, notice_id),
            )
        with pytest.raises(SummaryExecutionSuperseded):
            if late_outcome == "success":
                save_notice_summary(
                    live_db, _completed(notice_id, OLD_BODY, "2026-10-20", execution_token=token)
                )
            elif late_outcome == "failure":
                record_summary_failure(
                    live_db, notice_id, _metadata(OLD_BODY), reason_code="api_timeout",
                    execution_token=token,
                )
            else:
                save_notice_summary(live_db, SummaryRecord(
                    notice_id, "pending", _metadata(OLD_BODY), execution_token=token,
                ))
        assert _row(live_db, notice_id) is None

    def test_rollback_removes_uncommitted_result_and_registry(self, live_db) -> None:
        notice_id = _notice(live_db)
        with psycopg.connect(live_db.info.dsn, row_factory=dict_row) as worker:
            token = begin_summary_execution(worker, notice_id)
            save_notice_summary(
                worker, _completed(notice_id, OLD_BODY, "2026-10-20", execution_token=token)
            )
            assert _row(live_db, notice_id) is None
            worker.rollback()
        assert _row(live_db, notice_id) is None
        assert live_db.execute(
            "select notice_id from notice_summary_executions where notice_id=%s", (notice_id,)
        ).fetchone() is None

    def test_deleted_notice_cascades_and_inflight_write_does_not_resurrect_it(self, live_db):
        notice_id = _notice(live_db)
        token = begin_summary_execution(live_db, notice_id)
        with psycopg.connect(live_db.info.dsn, autocommit=True) as collector:
            collector.execute("delete from notices where id=%s", (notice_id,))
        with pytest.raises(SummaryExecutionSuperseded):
            save_notice_summary(
                live_db, _completed(notice_id, OLD_BODY, "2026-10-20", execution_token=token)
            )
        assert _row(live_db, notice_id) is None

    def test_guard_rechecks_token_after_waiting_for_a_newer_registration(self, live_db):
        notice_id = _notice(live_db)
        old_token = begin_summary_execution(live_db, notice_id)
        ready = Event()
        backend: list[int] = []

        def old_writer() -> None:
            with psycopg.connect(live_db.info.dsn, row_factory=dict_row) as writer:
                backend.append(writer.info.backend_pid)
                ready.set()
                with pytest.raises(SummaryExecutionSuperseded):
                    save_notice_summary(writer, _completed(
                        notice_id, OLD_BODY, "2026-10-20", execution_token=old_token,
                    ))

        with psycopg.connect(live_db.info.dsn, row_factory=dict_row) as latest:
            newer_token = begin_summary_execution(latest, notice_id)
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(old_writer)
                assert ready.wait(5)
                waiting = False
                until = monotonic() + 5
                while monotonic() < until:
                    waiting = live_db.execute(
                        "select wait_event_type='Lock' as waiting "
                        "from pg_stat_activity where pid=%s",
                        (backend[0],),
                    ).fetchone()["waiting"]
                    if waiting:
                        break
                    Event().wait(0.02)
                assert waiting
                save_notice_summary(latest, _completed(
                    notice_id, OLD_BODY, "2026-10-20", execution_token=newer_token,
                ))
                latest.commit()
                future.result(timeout=10)
        assert _row(live_db, notice_id)["attempt_count"] == 1

    def test_constraint_fault_raises_safe_error_and_requires_transaction_rollback(self, live_db):
        notice_id = _notice(live_db)
        live_db.execute("delete from notices where id=%s", (notice_id,))
        with psycopg.connect(live_db.info.dsn, row_factory=dict_row) as worker:
            # A legacy unguarded caller hitting a missing parent must not report success.
            with pytest.raises(SummaryStorageError, match="^summary_storage_failed$"):
                save_notice_summary(worker, _completed(notice_id, OLD_BODY, "2026-10-20"))
            with pytest.raises(psycopg.errors.InFailedSqlTransaction):
                worker.execute("select 1")
            worker.rollback()
        assert _row(live_db, notice_id) is None

    def test_temp_relation_cannot_redirect_a_successful_guarded_write(self, live_db):
        notice_id = _notice(live_db)
        token = begin_summary_execution(live_db, notice_id)
        live_db.execute(
            "create temp table notice_summaries (like public.notice_summaries including all)"
        )
        try:
            save_notice_summary(live_db, _completed(
                notice_id, OLD_BODY, "2026-10-20", execution_token=token,
            ))
            assert live_db.execute(
                "select notice_id from public.notice_summaries where notice_id=%s", (notice_id,)
            ).fetchone() is not None, "Reported success was written to a temporary shadow table"
            temporary_row = live_db.execute(
                "select notice_id from pg_temp.notice_summaries"
            ).fetchone()
            assert temporary_row is None
        finally:
            live_db.execute("drop table pg_temp.notice_summaries")


class TestPassThreePublicJobReplay:
    @pytest.mark.parametrize("provide_revision", [True, False])
    def test_stale_prepared_input_is_blocked_with_captured_revision_or_missing_argument(
        self, live_db, monkeypatch, provide_revision,
    ):
        """The operational job cannot register old input by omitting its revision."""
        notice_id = _notice(live_db)
        revision = live_db.execute(
            "select content_revision from notices where id=%s", (notice_id,)
        ).fetchone()["content_revision"]
        notice = NoticeInput(
            title="지원 사업 신청", body_text=OLD_BODY,
            reference_datetime="2026-10-07T12:00:00+09:00",
        )
        prepared = PreparedInput(
            notice_id, notice, [{"type": "text", "text": render_notice_input(notice)}],
        )
        metadata = SummaryMetadata(
            source_hash=compute_source_hash(OLD_BODY), model="gemini-threepass-test",
            prompt_version=SUMMARY_PROMPT_VERSION, attachment_status="none",
        )
        live_db.execute("update notices set body_html=%s where id=%s", (NEW_BODY, notice_id))
        data = _summary("2026-10-20").model_dump(mode="json")
        data["status"] = "unknown"
        data["card_summaries"]["deadline"] = "2026-10-20까지 신청해 주세요."
        calls: list[object] = []

        def generate(**kwargs):
            calls.append(kwargs)
            return json.dumps(data)

        monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
        if provide_revision:
            outcome = summarize_and_save_prepared_notice(
                live_db, prepared, metadata, api_key="non-secret-unit-test-key",
                expected_source_revision=revision,
            )
            assert isinstance(outcome, StoredSummarySuperseded)
        else:
            with pytest.raises(TypeError, match="expected_source_revision"):
                summarize_and_save_prepared_notice(
                    live_db, prepared, metadata, api_key="non-secret-unit-test-key",
                )
        assert calls == [] and _row(live_db, notice_id) is None
        assert live_db.execute(
            "select notice_id from notice_summary_executions where notice_id=%s", (notice_id,),
        ).fetchone() is None
        assert live_db.execute(
            "select body_html from notices where id=%s", (notice_id,),
        ).fetchone()["body_html"] == NEW_BODY

    @pytest.mark.parametrize("source", ["nowon", "wolgye1"])
    def test_collector_partial_rate_limit_progress_matches_real_committed_rows(
        self, live_db, monkeypatch, source,
    ) -> None:
        prefix = "execution-audit-" + uuid4().hex
        post_sns = [prefix + "-" + str(index) for index in range(3)]
        seen: list[str] = []

        def detail(entry, *_settings):
            seen.append(entry.post_sn)
            if len(seen) == 2:
                error = NowonPageError if source == "nowon" else WolgyeSourceError
                raise error("429", rate_limited=True)
            return (entry.url, EMPTY_ATTACHMENTS) if source == "nowon" else "detail"

        database = DatabaseSettings(live_db.info.dsn)
        if source == "nowon":
            notices = tuple(_nowon(post_sn) for post_sn in post_sns)
            listing = NowonCollection(notices, (PageStatus(1, 3, 1, True),), 3, True, (), ())
            monkeypatch.setattr("pipeline.collect_nowon.collect_all", lambda *_: listing)
            monkeypatch.setattr("pipeline.collect_nowon.fetch_notice_page", detail)
            outcome = collect_and_save_nowon(NowonSettings("audit-only", 2.0, 7.0), database)
        else:
            entries = tuple(BoardEntry(sn, "audit", "월계1동", "2026-10-07", False)
                            for sn in post_sns)
            listing = WolgyeListing(3, entries, (), 0, (), False, True)
            monkeypatch.setattr("pipeline.collect_wolgye1.collect_wolgye_listing", lambda *_args,
                                **_kwargs: listing)
            monkeypatch.setattr("pipeline.collect_wolgye1._fetch_detail_with_retry", detail)
            monkeypatch.setattr("pipeline.collect_wolgye1.extract_dong_files", lambda *_: [])

            def raw(entry, _html):
                return RawNotice(
                    category="dong", source_board="1042", dong_group="wolgye1", is_pinned=False,
                    post_sn=entry.post_sn, title="audit", department="월계1동",
                    registered_on="2026-10-07", url=entry.url, body_html="<p>Body</p>",
                    license_type="KOGL-1",
                )

            monkeypatch.setattr("pipeline.collect_wolgye1.parse_detail_page", raw)
            outcome = collect_and_save_wolgye1(WolgyeSettings(1, 2), database)
        assert outcome.attempted_count == len(seen) == 2
        assert outcome.saved_count == 1 and not outcome.complete
        rows = live_db.execute(
            "select id,post_sn,body_html,content_updated_at from notices where post_sn=any(%s)",
            (post_sns,),
        ).fetchall()
        _OWNED_NOTICES[id(live_db)].extend(row["id"] for row in rows)
        assert len(rows) == 1 and rows[0]["post_sn"] == post_sns[0]
        assert rows[0]["body_html"] and rows[0]["content_updated_at"] is not None
        assert outcome.failures[-1].reason_code == "rate_limited_not_attempted"

    @pytest.mark.parametrize("kind", ["text", "pdf", "malformed", "timeout"])
    def test_actual_transform_job_storage_anon_and_view_contract(self, live_db, monkeypatch, kind):
        notice_id = _notice(live_db)
        revision = live_db.execute(
            "select content_revision from notices where id=%s", (notice_id,),
        ).fetchone()["content_revision"]
        notice = NoticeInput(
            title="지원 사업 신청", body_text="" if kind == "pdf" else OLD_BODY,
            reference_datetime="2026-10-07T12:00:00+09:00",
        )
        blocks = [{"type": "text", "text": render_notice_input(notice)}]
        if kind == "pdf":
            blocks.append(_media("document"))
        prepared = PreparedInput(notice_id, notice, blocks)
        metadata = SummaryMetadata(
            source_hash=compute_source_hash(notice.body_text), model="gemini-threepass-test",
            prompt_version=SUMMARY_PROMPT_VERSION,
            attachment_status="all_read" if kind == "pdf" else "none",
        )
        data = _summary("2026-10-20").model_dump(mode="json")
        # An end date alone cannot establish that intake has already opened.
        data["status"] = "unknown"
        data["card_summaries"]["deadline"] = "2026-10-20까지 신청해 주세요."
        if kind == "pdf":
            for evidence in data["evidence"]:
                evidence.update(source_type="document", source_id="media_1", page=1)
        calls: list[object] = []

        def generate(**kwargs):
            calls.append(kwargs["notice_text"])
            if kind == "timeout":
                raise GeminiRequestError("api_timeout")
            return "malformed private provider payload" if kind == "malformed" else json.dumps(data)

        monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
        outcome = summarize_and_save_prepared_notice(
            live_db, prepared, metadata, expected_source_revision=revision,
            api_key="non-secret-unit-test-key",
        )
        assert len(calls) == (2 if kind == "malformed" else 1)
        live_db.execute("set role anon")
        row = live_db.execute(
            "select status,result,card_summaries,deadline_on,attachment_status "
            "from notice_summaries where notice_id=%s", (notice_id,),
        ).fetchone()
        view = build_notice_summary_view(
            status=row["status"], result=row["result"],
            attachment_status=row["attachment_status"], notice=notice,
        )
        if kind in {"malformed", "timeout"}:
            assert isinstance(outcome, StoredSummaryFailure)
            assert row["status"] == "failed" and view.content is None
            assert row["result"] is None and row["card_summaries"] is None
        elif kind == "pdf":
            assert row["status"] == "needs_review" and row["deadline_on"] is None
            assert view.content is not None and view.message == "원문 확인 요함"
            assert view.text_highlights.cards.deadline.status == "file_only"
        else:
            assert row["status"] == "summarized" and view.message is None, (
                row["result"]["uncertainties"], row["result"]["evidence"]
            )
            assert row["deadline_on"] == date(2026, 10, 20)
            assert view.content is not None
            assert view.text_highlights.cards.deadline.status == "ready"
        assert row["card_summaries"] == (
            row["result"]["card_summaries"] if row["result"] is not None else None
        )
