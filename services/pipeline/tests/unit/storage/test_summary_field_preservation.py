"""Issue #40: a later incomplete candidate must not erase a committed public bundle."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, date, datetime
from threading import Barrier

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row
from support.card_deadline_endpoints import _input_response
from support.gemini_multimodal import _mock_sdk
from support.threepass_database_audit import _notice
from support.threepass_database_audit import live_db as live_db

from pipeline.attachments.summary_bundle import prepare_summary_source
from pipeline.storage.summaries import (
    SummaryExecutionSuperseded,
    begin_summary_execution,
    save_prepared_summary,
)
from pipeline.storage.summary_metadata import build_summary_metadata_from_manifest
from pipeline.storage.summary_source import load_summary_source
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.summary_job import summarize_and_save_prepared_notice
from pipeline.transform.gemini_client import DEFAULT_MODEL
from pipeline.transform.summarize import summarize_prepared_notice

_PUBLIC_COLUMNS = (
    "status,result,card_summaries,category,category_code,deadline_on,"
    "attachment_status,file_references,preparation_omissions"
)


def _public_snapshot(dsn, notice_id):
    # A fresh role-restricted connection sees only the committed public result.
    with psycopg.connect(dsn, row_factory=dict_row) as reader:
        reader.execute("set local role anon")
        row = reader.execute(
            f"select {_PUBLIC_COLUMNS} from notice_summaries where notice_id=%s",
            (notice_id,),
        ).fetchone()
    assert row is not None
    view = build_notice_summary_view(
        status=row["status"], result=row["result"],
        attachment_status=row["attachment_status"],
        file_references=row["file_references"],
        preparation_omissions=row["preparation_omissions"],
    )
    return row, view.model_dump(mode="json")


def _missing_candidate(baseline, missing, warning):
    candidate = deepcopy(baseline)
    missing_fields = set()
    if missing in {"audience", "both"}:
        candidate.update(audience=None, audience_scope="unknown")
        candidate["card_summaries"]["audience"] = None
        missing_fields.update({"audience", "audience_scope"})
    if missing in {"deadline", "both"}:
        candidate["dates"] = []
        candidate["card_summaries"]["deadline"] = None
        missing_fields.add("dates")
    candidate["evidence"] = [
        item for item in candidate["evidence"] if item["field"] not in missing_fields
    ]
    candidate["uncertainties"] = ["원문 확인 필요"] if warning else []
    return candidate


def _setup_source(conn, *, pdf=False):
    notice, baseline = _input_response()
    # Explicit labels avoid manufacturing a review baseline through ambiguous prose.
    body = notice.body_text.replace("\n노원구민\n", "\n대상: 노원구민\n")
    notice_id = _notice(conn)
    conn.execute(
        "update notices set title=%s,body_html=%s,registered_on='2026-10-07' where id=%s",
        (notice.title, body.replace("\n", "<br>"), notice_id),
    )
    if pdf:
        conn.execute(
            "insert into notice_files(notice_id,file_id,file_key,kind,file_name,url) "
            "values (%s,'guide','id:guide','attachment','guide.pdf',%s)",
            (notice_id, "https://www.nowon.kr/component/file/ND_fileDownload.do"
             "?q_fileSn=1&q_fileId=guide"),
        )
        # Only this evidence uses the PDF, making the stored public link nonempty.
        for item in baseline["evidence"]:
            if item["field"] == "notes":
                item.update(source_type="document", source_id="media_1", page=1)
    source = load_summary_source(conn, notice_id)
    assert source is not None

    def download(request):
        assert pdf and request.url.params["q_fileId"] == "guide"
        return httpx.Response(
            200, content=b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n",
            headers={"content-type": "application/pdf"},
        )

    with httpx.Client(transport=httpx.MockTransport(download)) as client:
        prepared = prepare_summary_source(
            source, reference_datetime=notice.reference_datetime, client=client,
        )
    assert prepared.complete and prepared.warnings == ()
    metadata = build_summary_metadata_from_manifest(
        notice=prepared.notice, file_manifest=prepared.file_manifest, model=DEFAULT_MODEL,
    )
    return prepared, metadata, baseline


def _mock_responses(monkeypatch, current_response):
    requests = []

    def gemini_http(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "status": "completed",
            "steps": [{"type": "model_output", "content": [{
                "type": "text", "text": json.dumps(current_response[0], ensure_ascii=False),
            }]}],
        })

    _mock_sdk(monkeypatch, gemini_http)
    return requests


def _execute_and_commit(conn, prepared, metadata):
    with psycopg.connect(conn.info.dsn) as writer:
        outcome = summarize_and_save_prepared_notice(
            writer, prepared, metadata,
            expected_source_revision=prepared.file_manifest.source_revision,
            api_key="test-only-key",
        )
        writer.commit()
    return outcome


def _private_snapshot(conn, notice_id):
    return conn.execute(
        "select * from notice_summaries where notice_id=%s", (notice_id,),
    ).fetchone()


def _assert_preserved(conn, notice_id, before, public_before, outcome):
    after = _private_snapshot(conn, notice_id)
    assert outcome.information_loss_prevented is True
    assert outcome.status == before["status"]
    assert outcome.deadline_on == before["deadline_on"]
    assert outcome.generated_at == before["generated_at"]
    assert after["last_error_code"] == "summary_information_loss"
    assert after["attempt_count"] == before["attempt_count"] + 1
    mutable = {"last_error_code", "attempt_count", "updated_at"}
    assert {k: v for k, v in after.items() if k not in mutable} == {
        k: v for k, v in before.items() if k not in mutable
    }
    assert _public_snapshot(conn.info.dsn, notice_id) == public_before


@pytest.mark.parametrize("warning", [False, True], ids=["no-warning", "warning"])
@pytest.mark.parametrize("missing", ["audience", "deadline", "both"])
def test_same_source_omission_preserves_committed_public_bundle(
    live_db, monkeypatch, missing, warning,
):
    prepared, metadata, baseline = _setup_source(live_db)
    notice_id = prepared.notice_id
    response = [baseline]
    requests = _mock_responses(monkeypatch, response)
    first = _execute_and_commit(live_db, prepared, metadata)
    public_before = _public_snapshot(live_db.info.dsn, notice_id)
    before = _private_snapshot(live_db, notice_id)
    assert first.status == before["status"] == "summarized"
    assert first.information_loss_prevented is False
    assert len(requests) == 1
    assert before["result"]["audience"] == "노원구민"
    assert before["card_summaries"]["audience"] is not None
    assert before["card_summaries"]["deadline"] is not None
    assert before["deadline_on"] == date(2026, 10, 20)

    response[0] = _missing_candidate(baseline, missing, warning)
    outcome = _execute_and_commit(live_db, prepared, metadata)
    assert len(requests) >= 2, "The second execution must actually reach Gemini"
    _assert_preserved(live_db, notice_id, before, public_before, outcome)


def test_partial_media_retry_preserves_nonempty_public_file_links(live_db, monkeypatch):
    prepared, metadata, baseline = _setup_source(live_db, pdf=True)
    response = [baseline]
    _mock_responses(monkeypatch, response)
    _execute_and_commit(live_db, prepared, metadata)
    before = _private_snapshot(live_db, prepared.notice_id)
    public_before = _public_snapshot(live_db.info.dsn, prepared.notice_id)
    assert before["status"] == "needs_review"
    assert len(before["file_references"]) == 1
    assert before["file_references"][0]["files"][0]["url"].endswith("q_fileId=guide")
    assert public_before[1]["file_references"] == before["file_references"]

    response[0] = _missing_candidate(baseline, "both", True)
    outcome = _execute_and_commit(live_db, prepared, metadata)
    _assert_preserved(live_db, prepared.notice_id, before, public_before, outcome)


def test_first_partial_can_be_published_then_completed_and_corrected(live_db, monkeypatch):
    prepared, metadata, baseline = _setup_source(live_db)
    response = [_missing_candidate(baseline, "both", True)]
    _mock_responses(monkeypatch, response)
    first = _execute_and_commit(live_db, prepared, metadata)
    partial, view = _public_snapshot(live_db.info.dsn, prepared.notice_id)
    assert first.information_loss_prevented is False
    assert partial["status"] == "needs_review" and view["content"] is not None
    assert partial["result"]["audience"] is None
    assert partial["card_summaries"]["deadline"] is None
    assert partial["result"]["action"] == baseline["action"]

    response[0] = baseline
    completed = _execute_and_commit(live_db, prepared, metadata)
    complete, _ = _public_snapshot(live_db.info.dsn, prepared.notice_id)
    assert completed.information_loss_prevented is False
    assert complete["status"] == "summarized"
    assert complete["deadline_on"] == date(2026, 10, 20)
    assert complete["result"]["audience"] == baseline["audience"]

    response[0] = deepcopy(baseline)
    response[0]["card_summaries"]["notes"] = "참가비가 무료예요."
    corrected = _execute_and_commit(live_db, prepared, metadata)
    after = _private_snapshot(live_db, prepared.notice_id)
    assert corrected.information_loss_prevented is False
    assert after["card_summaries"]["notes"] == "참가비가 무료예요."
    assert after["last_error_code"] is None and after["attempt_count"] == 3


def test_concurrent_direct_saves_preserve_bundle_and_reject_obsolete_token(live_db, monkeypatch):
    prepared, metadata, baseline = _setup_source(live_db)
    response = [baseline]
    _mock_responses(monkeypatch, response)
    first = _execute_and_commit(live_db, prepared, metadata)
    before = _private_snapshot(live_db, prepared.notice_id)
    public_before = _public_snapshot(live_db.info.dsn, prepared.notice_id)
    response[0] = _missing_candidate(baseline, "both", False)
    candidate = summarize_prepared_notice(prepared, api_key="test-only-key")
    older_token = begin_summary_execution(
        live_db, prepared.notice_id,
        expected_source_revision=prepared.file_manifest.source_revision,
    )
    active_token = begin_summary_execution(
        live_db, prepared.notice_id,
        expected_source_revision=prepared.file_manifest.source_revision,
    )
    barrier = Barrier(2)

    def store(result, token, deadline):
        with psycopg.connect(live_db.info.dsn) as writer:
            writer.execute("set local lock_timeout='3s'")
            barrier.wait(timeout=5)
            try:
                return save_prepared_summary(
                    writer, result, metadata, deadline_on=deadline,
                    generated_at=datetime.now(UTC), execution_token=token,
                )
            except SummaryExecutionSuperseded:
                return "superseded"

    with ThreadPoolExecutor(max_workers=2) as executor:
        old = executor.submit(store, first.result, older_token, date(2026, 10, 20))
        active = executor.submit(store, candidate, active_token, None)
        assert old.result(timeout=10) == "superseded"
        outcome = active.result(timeout=10)
    _assert_preserved(live_db, prepared.notice_id, before, public_before, outcome)
