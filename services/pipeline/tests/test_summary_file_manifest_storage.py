"""Persist trusted file aliases atomically without exposing private provenance."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from threading import Event
from time import monotonic
from unittest.mock import MagicMock

import psycopg
import pytest
from psycopg.rows import dict_row
from test_summary_execution_storage import OLD_BODY, _completed, _metadata, _notice, _row, _summary

from pipeline.storage import summaries as storage
from pipeline.storage.summary_record import SummaryRecord, SummaryRecordError, build_summary_record
from pipeline.transform.prepared_summary import PreparedSummaryResult
from pipeline.transform.summary_files import (
    PreparedMediaBinding,
    PreparedSourceFile,
    PrivateSummaryFileManifest,
)
from pipeline.transform.summary_schema import MediaSource


@pytest.fixture
def source_case():
    dsn = os.environ.get("AUDIT_TEST_DATABASE_URL") or os.environ.get("PIPELINE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("A disposable pipeline_schema_test_* database is required")
    conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=5)
    if conn.info.host != "127.0.0.1" or not conn.info.dbname.startswith("pipeline_schema_test_"):
        conn.close()
        pytest.fail("Use a disposable loopback pipeline_schema_test_* database")
    notice_id = _notice(conn)
    try:
        original_files = [
            ("id:shared", "attachment", "https://www.nowon.kr/audit/image.png", "media_1"),
            ("id:shared", "inline_image", "https://www.nowon.kr/audit/image.png", "media_1"),
            ("id:pdf", "attachment", "https://www.nowon.kr/audit/file.pdf", "media_2"),
        ]
        files = []
        media = (
            PreparedMediaBinding(
                source_id="media_1", source_type="image", input_block_index=1,
                content_sha256=sha256(b"image").hexdigest(),
            ),
            PreparedMediaBinding(
                source_id="media_2", source_type="document", input_block_index=2,
                content_sha256=sha256(b"pdf").hexdigest(),
            ),
        )
        for key, kind, url, source_id in original_files:
            file_id = conn.execute(
                "insert into notice_files(notice_id,file_id,file_key,kind,url) "
                "values (%s,%s,%s,%s,%s) returning id", (notice_id, key[3:], key, kind, url),
            ).fetchone()["id"]
            files.append(PreparedSourceFile(
                notice_file_id=file_id, file_key=key, kind=kind, url=url, outcome="media",
                source_id=source_id, content_sha256=media[int(source_id[-1]) - 1].content_sha256,
            ))
        source = conn.execute(
            "select content_revision,url from notices where id=%s", (notice_id,),
        ).fetchone()
        manifest = PrivateSummaryFileManifest(
            notice_id=notice_id, source_revision=source["content_revision"],
            original_url=source["url"], files=tuple(files), media=media,
        )
        yield conn, manifest
    finally:
        conn.execute("reset role")
        conn.execute("delete from notices where id=%s", (notice_id,))
        conn.close()


def _public(manifest):
    return [item.model_dump(mode="json") for item in manifest.public_references()]


def _result(manifest, *, correction=None):
    return PreparedSummaryResult(
        notice_id=manifest.notice_id, summary=_summary("2026-10-20"), warnings=(),
        media_sources=tuple(MediaSource(source_id=item.source_id, source_type=item.source_type)
                            for item in manifest.media),
        correction_failure_code=correction, file_manifest=manifest,
    )


def _save(conn, manifest, *, correction=None, token=None):
    if token is None:
        token = storage.begin_summary_execution(
            conn, manifest.notice_id, expected_source_revision=manifest.source_revision,
        )
    return storage.save_prepared_summary(
        conn, _result(manifest, correction=correction),
        replace(_metadata(OLD_BODY), attachment_status=manifest.attachment_status),
        deadline_on=None, generated_at=datetime.now(UTC), execution_token=token,
    )


def test_private_manifest_snapshot_and_id_based_alias_counts_are_stored(source_case):
    conn, manifest = source_case
    assert manifest.total_file_count == manifest.read_file_count == 3
    assert len(manifest.media) == 2 and manifest.attachment_status == "all_read"
    stored = _save(conn, manifest)
    row = _row(conn, manifest.notice_id)
    assert stored.status == "summarized" and row["attachment_status"] == "all_read"
    assert PrivateSummaryFileManifest.model_validate_json(
        json.dumps(row["file_manifest"]),
    ) == manifest
    assert row["file_references"] == _public(manifest)
    assert len(row["file_references"][0]["files"]) == 2
    assert "file_manifest" not in row["result"] and "file_references" not in row["result"]
    assert stored.result.file_manifest == manifest


def test_file_identity_validation_uses_ids_not_input_order(source_case):
    conn, manifest = source_case
    shuffled = manifest.model_copy(update={"files": tuple(reversed(manifest.files))})
    _save(conn, shuffled)
    assert _row(conn, manifest.notice_id)["file_references"] == _public(shuffled)


@pytest.mark.parametrize(("unread_indices", "expected_status"), [
    ((0,), "partial"), ((0, 1, 2), "unread"),
])
def test_original_file_outcomes_determine_review_and_never_expose_unread_aliases(
    source_case, unread_indices, expected_status,
):
    conn, manifest = source_case
    files = tuple(
        item.model_copy(update={"outcome": "unread", "source_id": None, "content_sha256": None})
        if index in unread_indices else item
        for index, item in enumerate(manifest.files)
    )
    candidate = manifest.model_copy(update={"files": files})
    assert candidate.attachment_status == expected_status
    _save(conn, candidate)
    row = _row(conn, manifest.notice_id)
    assert row["status"] == "needs_review" and row["attachment_status"] == expected_status
    assert row["file_references"] == _public(candidate)
    readable_ids = {item.notice_file_id for item in files if item.outcome != "unread"}
    assert {
        item["notice_file_id"]
        for reference in row["file_references"] for item in reference["files"]
    } == readable_ids


def test_a_real_file_id_from_another_notice_cannot_be_used_as_an_alias(source_case):
    conn, manifest = source_case
    other = _notice(conn)
    try:
        borrowed_id = conn.execute(
            "insert into notice_files(notice_id,file_id,file_key,kind,url) "
            "values (%s,'shared','id:shared','attachment',%s) returning id",
            (other, manifest.files[0].url),
        ).fetchone()["id"]
        borrowed = manifest.files[0].model_copy(update={"notice_file_id": borrowed_id})
        candidate = manifest.model_copy(update={"files": (borrowed, *manifest.files[1:])})
        with pytest.raises(SummaryRecordError, match="^file_manifest_source_mismatch$"):
            _save(conn, candidate)
        assert _row(conn, manifest.notice_id) is None
    finally:
        conn.execute("delete from notices where id=%s", (other,))


@pytest.mark.parametrize("mutation", ["missing", "extra", "id", "key", "kind", "url", "origin"])
def test_wrong_or_incomplete_db_file_identities_are_rejected_without_publication(
    source_case, mutation,
):
    conn, manifest = source_case
    files = list(manifest.files)
    updates = {}
    if mutation == "missing":
        files.pop()
    elif mutation == "extra":
        files.append(files[0].model_copy(update={"notice_file_id": 2**63 - 1}))
    elif mutation == "id":
        files[0] = files[0].model_copy(update={"notice_file_id": 2**63 - 1})
    elif mutation == "key":
        files[0] = files[0].model_copy(update={"file_key": "id:private-wrong-key"})
    elif mutation == "kind":
        files[0] = files[0].model_copy(update={"kind": "inline_image"})
    elif mutation == "url":
        files[0] = files[0].model_copy(update={"url": "https://www.nowon.kr/wrong.pdf"})
    else:
        updates["original_url"] = "https://www.nowon.kr/wrong-notice"
    candidate = manifest.model_copy(update={"files": tuple(files), **updates})
    with pytest.raises(SummaryRecordError, match="^file_manifest_source_mismatch$"):
        _save(conn, candidate)
    assert _row(conn, manifest.notice_id) is None


def test_source_revision_changed_after_preparation_is_superseded(source_case):
    conn, manifest = source_case
    token = storage.begin_summary_execution(
        conn, manifest.notice_id, expected_source_revision=manifest.source_revision,
    )
    conn.execute("update notices set title='Changed source' where id=%s", (manifest.notice_id,))
    with pytest.raises(storage.SummaryExecutionSuperseded):
        _save(conn, manifest, token=token)
    assert _row(conn, manifest.notice_id) is None


def test_newer_execution_cannot_be_replaced_by_old_file_manifest(source_case):
    conn, manifest = source_case
    old_token = storage.begin_summary_execution(conn, manifest.notice_id)
    _save(conn, manifest)
    before = _row(conn, manifest.notice_id)
    with pytest.raises(storage.SummaryExecutionSuperseded):
        _save(conn, manifest, token=old_token)
    assert _row(conn, manifest.notice_id) == before


@pytest.mark.parametrize("operation", ["failed", "pending", "correction"])
def test_preserved_result_keeps_its_private_manifest_and_public_links(source_case, operation):
    conn, manifest = source_case
    _save(conn, manifest)
    before = _row(conn, manifest.notice_id)
    token = storage.begin_summary_execution(conn, manifest.notice_id)
    if operation == "failed":
        storage.record_summary_failure(
            conn, manifest.notice_id,
            replace(_metadata("partial retry"), attachment_status="unread"),
            reason_code="api_timeout", execution_token=token,
        )
    elif operation == "pending":
        storage.save_notice_summary(conn, SummaryRecord(
            notice_id=manifest.notice_id, status="pending", metadata=_metadata(OLD_BODY),
            execution_token=token,
        ))
    else:
        candidate = manifest.model_copy(update={"media": tuple(reversed(manifest.media))})
        _save(conn, candidate, correction="api_timeout", token=token)
    after = _row(conn, manifest.notice_id)
    for column in ("status", "result", "file_manifest", "file_references", "attachment_status"):
        assert after[column] == before[column]
    assert after["attempt_count"] == before["attempt_count"] + 1


def test_first_usable_correction_fallback_keeps_candidate_links(source_case):
    conn, manifest = source_case
    stored = _save(conn, manifest, correction="api_timeout")
    row = _row(conn, manifest.notice_id)
    assert stored.status == row["status"] == "needs_review"
    assert row["deadline_on"] is None and row["last_error_code"] == "api_timeout"
    assert row["file_references"] == _public(manifest)


def test_legacy_new_result_does_not_inherit_previous_file_links(source_case):
    conn, manifest = source_case
    _save(conn, manifest)
    storage.save_notice_summary(conn, _completed(manifest.notice_id, OLD_BODY, "2026-10-20"))
    row = _row(conn, manifest.notice_id)
    assert row["result"] is not None
    assert row["file_manifest"] is row["file_references"] is None


@pytest.mark.parametrize("source_edit", ["notice", "file"])
def test_source_invalidation_hides_public_file_links_with_result(source_case, source_edit):
    conn, manifest = source_case
    _save(conn, manifest)
    if source_edit == "notice":
        conn.execute("update notices set title='Changed source' where id=%s", (manifest.notice_id,))
    else:
        conn.execute("update notice_files set url=%s where id=%s", (
            "https://www.nowon.kr/updated.pdf", manifest.files[-1].notice_file_id,
        ))
    row = _row(conn, manifest.notice_id)
    assert row["status"] == "needs_review" and row["result"] is row["file_references"] is None
    assert row["file_manifest"] == manifest.model_dump(mode="json")


def test_unregistered_body_image_gets_original_notice_guidance_without_guessed_file(source_case):
    conn, manifest = source_case
    candidate = manifest.model_copy(update={"media": (*manifest.media, PreparedMediaBinding(
        source_id="media_3", source_type="image", input_block_index=3,
        content_sha256=sha256(b"body-image").hexdigest(),
    ))})
    _save(conn, candidate)
    assert _row(conn, manifest.notice_id)["file_references"][-1] == {
        "source_id": "media_3", "source_type": "image", "files": [],
        "original_notice_url": manifest.original_url, "guidance": "원문에서 확인",
    }


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_apps_read_only_whitelisted_links_and_cannot_read_manifest(source_case, role):
    conn, manifest = source_case
    _save(conn, manifest)
    conn.execute("set role " + role)
    public = conn.execute(
        "select file_references from notice_summaries where notice_id=%s", (manifest.notice_id,),
    ).fetchone()["file_references"]
    assert public == _public(manifest)
    serialized = json.dumps(public)
    for key in ("file_key", "content_sha256", "outcome", "input_block_index", "attachment_index"):
        assert key not in serialized
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select file_manifest from notice_summaries")


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_hidden_notice_file_references_are_not_visible_to_apps(source_case, role):
    conn, manifest = source_case
    _save(conn, manifest)
    conn.execute("update notices set is_visible=false where id=%s", (manifest.notice_id,))
    conn.execute("set role " + role)
    assert conn.execute(
        "select file_references from notice_summaries where notice_id=%s", (manifest.notice_id,),
    ).fetchone() is None


def test_file_references_are_stored_generated_and_cannot_be_written(source_case):
    conn, manifest = source_case
    _save(conn, manifest)
    generated = conn.execute(
        "select attgenerated from pg_attribute where attrelid='notice_summaries'::regclass "
        "and attname='file_references'",
    ).fetchone()["attgenerated"]
    assert generated == "s"
    with pytest.raises(psycopg.errors.GeneratedAlways):
        conn.execute("update notice_summaries set file_references='[]' where notice_id=%s", (
            manifest.notice_id,
        ))


def test_service_role_can_validate_and_store_generated_file_links(source_case):
    conn, manifest = source_case
    conn.execute("set role service_role")
    _save(conn, manifest)
    assert _row(conn, manifest.notice_id)["file_references"] == _public(manifest)


@pytest.mark.parametrize("correction", [None, "api_timeout"])
def test_non_autocommit_storage_does_not_commit_the_callers_transaction(source_case, correction):
    conn, manifest = source_case
    with psycopg.connect(conn.info.dsn, row_factory=dict_row) as worker:
        _save(worker, manifest, correction=correction)
        assert _row(conn, manifest.notice_id) is None
        worker.commit()
    assert _row(conn, manifest.notice_id)["file_references"] == _public(manifest)


@pytest.mark.parametrize("correction", [None, "api_timeout"])
def test_autocommit_validation_and_write_share_parent_lock_without_child_deadlock(
    source_case, monkeypatch, correction,
):
    conn, manifest = source_case
    validated = Event()
    release = Event()
    writer_started = Event()
    writer_pids = []
    original = storage._validate_manifest_source

    def pause(cursor, record):
        original(cursor, record)
        validated.set()
        assert release.wait(5), "The validation/write barrier was not released"

    monkeypatch.setattr(storage, "_validate_manifest_source", pause)

    def save():
        with psycopg.connect(conn.info.dsn, autocommit=True, row_factory=dict_row) as worker:
            return _save(worker, manifest, correction=correction)

    def edit_file():
        with psycopg.connect(conn.info.dsn, autocommit=True) as writer:
            writer_pids.append(writer.info.backend_pid)
            writer_started.set()
            writer.execute("update notice_files set url=%s where id=%s", (
                "https://www.nowon.kr/after-validation.pdf", manifest.files[-1].notice_file_id,
            ))

    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = pool.submit(save)
        try:
            assert validated.wait(5)
            edited = pool.submit(edit_file)
            assert writer_started.wait(5)
            wait_until = monotonic() + 4
            blocked = False
            while monotonic() < wait_until and not blocked:
                blocked = bool(conn.execute(
                    "select cardinality(pg_blocking_pids(%s)) > 0 as blocked", (writer_pids[0],),
                ).fetchone()["blocked"])
                if not blocked:
                    Event().wait(0.02)
            assert blocked, "A file edit committed between validation and summary storage"
        finally:
            release.set()
        saved.result(timeout=5)
        edited.result(timeout=5)
    row = _row(conn, manifest.notice_id)
    assert row["result"] is row["file_references"] is None
    assert row["file_manifest"] == manifest.model_dump(mode="json")


def _memory_manifest():
    digest = sha256(b"pdf").hexdigest()
    return PrivateSummaryFileManifest(
        notice_id=17, source_revision=4, original_url="https://www.nowon.kr/audit",
        files=(PreparedSourceFile(
            notice_file_id=5, file_key="id:pdf", kind="attachment",
            url="https://www.nowon.kr/file.pdf", outcome="media", source_id="media_1",
            content_sha256=digest,
        ),),
        media=(PreparedMediaBinding(
            source_id="media_1", source_type="document", input_block_index=1,
            content_sha256=digest,
        ),),
    )


@pytest.mark.parametrize(("mutation", "reason"), [
    ("notice_id", "file_manifest_notice_id_mismatch"),
    ("attachment_status", "file_manifest_attachment_status_mismatch"),
    ("dict", "invalid_file_manifest"),
    ("invalid_nested_url", "invalid_file_manifest"),
])
def test_storage_record_rejects_invalid_manifest_without_opening_db(mutation, reason):
    manifest = _memory_manifest()
    metadata = replace(_metadata(OLD_BODY), attachment_status="all_read")
    if mutation == "notice_id":
        manifest = manifest.model_copy(update={"notice_id": 18})
    elif mutation == "attachment_status":
        metadata = replace(metadata, attachment_status="none")
    elif mutation == "dict":
        manifest = manifest.model_dump(mode="json")
    else:
        object.__setattr__(manifest.files[0], "url", "javascript:invalid")
    with pytest.raises(SummaryRecordError, match="^" + reason + "$"):
        SummaryRecord(
            notice_id=17, status="summarized", metadata=metadata, result=_summary("2026-10-20"),
            generated_at=datetime.now(UTC), file_manifest=manifest,
        )


def test_result_manifest_is_snapshotted_and_storage_revalidates_nested_data():
    manifest = _memory_manifest()
    metadata = replace(_metadata(OLD_BODY), attachment_status="all_read")
    record = build_summary_record(
        _result(manifest), metadata, deadline_on=None, generated_at=datetime.now(UTC),
    )
    expected_url = record.file_manifest.files[0].url
    object.__setattr__(manifest.files[0], "url", "https://www.nowon.kr/other.pdf")
    assert record.file_manifest.files[0].url == expected_url
    object.__setattr__(record.file_manifest.files[0], "url", "javascript:invalid")
    conn = MagicMock()
    with pytest.raises(SummaryRecordError, match="^invalid_file_manifest$"):
        storage.save_notice_summary(conn, record)
    conn.cursor.assert_not_called()
