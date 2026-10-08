"""Captured source bodies, files and revisions must survive preparation intact."""

from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import pytest
from support.gemini_multimodal import PreparedInput
from support.summary_execution_storage import NEW_BODY, OLD_BODY, _row
from support.threepass_database_audit import _notice
from support.threepass_database_audit import live_db as live_db

from pipeline.storage.summary_metadata import build_summary_metadata
from pipeline.storage.summary_source import load_summary_source
from pipeline.summary_job import StoredSummarySuperseded, summarize_and_save_prepared_notice
from pipeline.transform.notice_input import NoticeInput, render_notice_input


def test_snapshot_has_the_original_file_identities_and_revision_with_dict_rows(live_db):
    notice_id = _notice(live_db)
    for key, kind in (("id:b", "attachment"), ("id:a", "inline_image")):
        live_db.execute(
            "insert into notice_files (notice_id,kind,file_key,file_id,url) "
            "values (%s,%s,%s,%s,'https://www.nowon.kr/file')",
            (notice_id, kind, key, key[3:]),
        )
    source = load_summary_source(live_db, notice_id)
    assert source.body_html == OLD_BODY
    assert [(item.file_key, item.kind) for item in source.files] == [
        ("id:a", "inline_image"), ("id:b", "attachment"),
    ]
    assert source.content_revision == live_db.execute(
        "select content_revision from notices where id=%s", (notice_id,),
    ).fetchone()["content_revision"]
    assert all(item.id > 0 and item.url == "https://www.nowon.kr/file" for item in source.files)
    with pytest.raises(FrozenInstanceError):
        source.content_revision += 1


def test_snapshot_cannot_prepare_a_hidden_or_missing_notice(live_db):
    notice_id = _notice(live_db)
    live_db.execute("update notices set is_visible=false where id=%s", (notice_id,))
    assert load_summary_source(live_db, notice_id) is None
    live_db.execute("delete from notices where id=%s", (notice_id,))
    assert load_summary_source(live_db, notice_id) is None


@pytest.mark.parametrize("notice_id", [None, True, False, 0, -1, 2**63, "1", 1.0])
def test_invalid_snapshot_identifier_does_not_touch_a_database(notice_id):
    conn = Mock()
    with pytest.raises(ValueError, match="^invalid_notice_id$"):
        load_summary_source(conn, notice_id)
    conn.cursor.assert_not_called()


@pytest.mark.parametrize("change", ["body", "file"])
def test_preparation_snapshot_is_rejected_after_a_source_or_file_change(
    live_db, monkeypatch, change,
):
    notice_id = _notice(live_db)
    source = load_summary_source(live_db, notice_id)
    notice = NoticeInput(title=source.title, body_text=source.body_html,
                         reference_datetime="2026-10-07T12:00:00+09:00")
    prepared = PreparedInput(
        source.notice_id, notice, [{"type": "text", "text": render_notice_input(notice)}],
    )
    metadata = build_summary_metadata(
        body_text=notice.body_text, total_file_count=0, read_file_count=0, model="offline-snapshot",
    )
    if change == "body":
        live_db.execute("update notices set body_html=%s where id=%s", (NEW_BODY, notice_id))
    else:
        live_db.execute(
            "insert into notice_files(notice_id,kind,file_key,file_id,url) "
            "values (%s,'attachment','id:new-file','new-file','https://www.nowon.kr/new')",
            (notice_id,),
        )
    provider = Mock(side_effect=AssertionError("stale input must not invoke Gemini"))
    monkeypatch.setattr("pipeline.summary_job.summarize_prepared_notice", provider)
    outcome = summarize_and_save_prepared_notice(
        live_db, prepared, metadata, expected_source_revision=source.content_revision,
    )
    assert isinstance(outcome, StoredSummarySuperseded)
    provider.assert_not_called()
    assert source.body_html == OLD_BODY and source.files == ()
    assert _row(live_db, notice_id) is None
    assert live_db.execute(
        "select notice_id from notice_summary_executions where notice_id=%s", (notice_id,),
    ).fetchone() is None
