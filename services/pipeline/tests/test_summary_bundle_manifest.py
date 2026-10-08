"""The actual attachment preparer must satisfy the current summary/storage contract."""

import json
from dataclasses import replace
from hashlib import sha256
from unittest.mock import Mock

import httpx
import pytest
from test_hwp_text import _hwp, _text
from test_prepared_summary_storage import _response
from test_summary_bundle import NOW, PNG, URL, file, source
from test_summary_execution_storage import _row
from test_threepass_database_audit import _notice
from test_threepass_database_audit import live_db as live_db

from pipeline.attachments.summary_bundle import prepare_summary_source
from pipeline.storage.summary_metadata import (
    SummaryAttachmentText,
    build_summary_metadata_from_manifest,
    compute_source_hash,
)
from pipeline.storage.summary_source import load_summary_source
from pipeline.summary_job import (
    StoredSummarySuperseded,
    summarize_and_save_prepared_notice,
)
from pipeline.transform.gemini_client import DEFAULT_MODEL
from pipeline.transform.prepared_summary import prepare_gemini_request


def _metadata(prepared):
    return build_summary_metadata_from_manifest(
        notice=prepared.notice, file_manifest=prepared.file_manifest, model=DEFAULT_MODEL,
    )


def test_duplicate_urls_and_bytes_keep_every_original_file_identity():
    calls = []

    def download(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=PNG)

    originals = (
        file(1, "image.png"),
        replace(file(1, None, "inline_image"), id=2),
        file(3, "copy.png"),
    )
    with httpx.Client(transport=httpx.MockTransport(download)) as client:
        prepared = prepare_summary_source(
            source(*originals, html=f'<p>행사 안내</p><img src="{URL}1">'),
            reference_datetime=NOW, client=client,
        )
    blocks, media = prepare_gemini_request(prepared)
    manifest = prepared.file_manifest
    assert len(calls) == 2 and len(media) == 1
    assert manifest.total_file_count == manifest.read_file_count == 3
    assert manifest.source_revision == 4
    assert [(item.notice_file_id, item.url) for item in manifest.files] == [
        (item.id, item.url) for item in originals
    ]
    assert {item.source_id for item in manifest.files} == {"media_1"}
    assert len(manifest.public_references()[0].files) == 3
    assert _metadata(prepared).attachment_status == "all_read"
    assert "file_key" not in json.dumps(blocks)


def test_hwp_aliases_hash_extracted_text_under_each_original_key():
    text = "행사 안내"
    hwp = _hwp([_text(text)]).data
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=hwp)),
    ) as client:
        prepared = prepare_summary_source(
            source(file(1, "a.hwp"), file(2, "b.hwp")),
            reference_datetime=NOW, client=client,
        )
    prepare_gemini_request(prepared)
    assert len(prepared.notice.attachments) == 1
    assert {item.attachment_index for item in prepared.file_manifest.files} == {0}
    assert {item.content_sha256 for item in prepared.file_manifest.files} == {
        sha256(text.encode()).hexdigest(),
    }
    assert _metadata(prepared).source_hash == compute_source_hash(
        prepared.notice.body_text,
        (SummaryAttachmentText("id:1", text), SummaryAttachmentText("id:2", text)),
    )


def test_mixed_inputs_bind_media_by_transmitted_order_and_keep_body_image_fallback():
    data = {"1": b"%PDF-source", "2": _hwp([_text("행사 안내")]).data, "3": PNG}
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=data[request.url.params["q_fileId"]]),
    )) as client:
        prepared = prepare_summary_source(
            source(file(1, "a.pdf"), file(2, "b.hwp"), html=f'<img src="{URL}3">'),
            reference_datetime=NOW, client=client,
        )
    prepare_gemini_request(prepared)
    manifest = prepared.file_manifest
    assert [
        (item.source_id, item.source_type, item.input_block_index) for item in manifest.media
    ] == [
        ("media_1", "image", 1), ("media_2", "document", 2),
    ]
    assert manifest.files[0].source_id == "media_2"
    assert manifest.files[1].attachment_index == 0
    fallback, pdf = manifest.public_references()
    assert fallback.files == () and fallback.original_notice_url == source().url
    assert fallback.guidance == "원문에서 확인"
    assert pdf.files[0].notice_file_id == 1


def test_failure_keeps_unread_row_and_allows_only_the_read_content():
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=b"%PDF-source"),
    )) as client:
        prepared = prepare_summary_source(
            source(file(1, "a.xlsx"), file(2, "b.pdf")),
            reference_datetime=NOW, client=client,
        )
    assert [item.outcome for item in prepared.file_manifest.files] == ["unread", "media"]
    assert _metadata(prepared).attachment_status == "partial"
    blocks, _ = prepare_gemini_request(prepared)
    assert "입력 처리 범위" in blocks[2]["text"]
    assert prepared.file_manifest.omissions[0].notice_file_id == 1


@pytest.mark.parametrize("mutation", ["revision", "notice_id", "duplicate_file_id"])
def test_invalid_snapshot_is_rejected_before_any_download(mutation):
    original = source(file(1, "a.pdf"))
    if mutation == "revision":
        original = replace(original, content_revision=0)
    elif mutation == "notice_id":
        original = replace(original, notice_id=True)
    else:
        original = replace(original, files=(original.files[0], original.files[0]))
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: pytest.fail("invalid snapshot must not download"),
    )) as client, pytest.raises(ValueError):
        prepare_summary_source(original, reference_datetime=NOW, client=client)


def test_legacy_decorative_file_is_retained_as_unread_with_a_warning():
    decorative = replace(
        file(1, None, "inline_image"),
        url="https://culture.seoul.go.kr/_ui/images/main/cnl-common/nLc-logo-culture.png",
    )
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: pytest.fail("decorative image must not download"),
    )) as client:
        prepared = prepare_summary_source(
            source(decorative), reference_datetime=NOW, client=client,
        )
    prepare_gemini_request(prepared)
    assert prepared.complete and prepared.file_manifest.files[0].outcome == "unread"
    assert prepared.warnings[0].reason_code == "decorative_image_ignored"
    assert _metadata(prepared).attachment_status == "unread"


def _database_source(conn):
    notice_id = _notice(conn)
    conn.execute(
        "update notices set title='행사 안내',body_html='<p>행사 안내</p>' where id=%s",
        (notice_id,),
    )
    for kind in ("attachment", "inline_image"):
        conn.execute(
            "insert into notice_files(notice_id,kind,file_key,file_id,file_name,url) "
            "values (%s,%s,'id:1','1','poster.png',%s)", (notice_id, kind, URL + "1"),
        )
    return load_summary_source(conn, notice_id)


def _prepare(original, *, failure=False):
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(503) if failure else httpx.Response(200, content=PNG),
    )) as client:
        return prepare_summary_source(original, reference_datetime=NOW, client=client)


def test_real_database_preparer_and_summary_job_store_aliases_and_preserve_them_on_failure(
    live_db, monkeypatch,
):
    original = _database_source(live_db)
    prepared = _prepare(original)
    provider = Mock(return_value=json.dumps(_response("image"), ensure_ascii=False))
    monkeypatch.setattr("pipeline.transform.summarize.generate_summary_json", provider)
    result = summarize_and_save_prepared_notice(
        live_db, prepared, _metadata(prepared),
        expected_source_revision=original.content_revision, api_key="test-key",
    )
    assert result.status == "needs_review"
    assert provider.call_count == 1
    saved = _row(live_db, original.notice_id)
    assert saved["attachment_status"] == "all_read"
    assert len(saved["file_manifest"]["files"]) == 2
    assert len(saved["file_references"][0]["files"]) == 2
    assert saved["file_references"] == [
        item.model_dump(mode="json") for item in prepared.file_manifest.public_references()
    ]
    failed = _prepare(original, failure=True)
    provider.return_value = json.dumps(_response("text"), ensure_ascii=False)
    outcome = summarize_and_save_prepared_notice(
        live_db, failed, _metadata(failed),
        expected_source_revision=original.content_revision, api_key="test-key",
    )
    assert outcome.status == saved["status"]
    assert provider.call_count == 2
    after = _row(live_db, original.notice_id)
    for key in ("result", "file_manifest", "file_references", "status"):
        assert after[key] == saved[key]


@pytest.mark.parametrize("change", ["body", "file"])
def test_actual_prepared_snapshot_is_rejected_after_source_changes(live_db, monkeypatch, change):
    original = _database_source(live_db)
    prepared = _prepare(original)
    if change == "body":
        live_db.execute(
            "update notices set body_html='내용 변경' where id=%s", (original.notice_id,),
        )
    else:
        live_db.execute(
            "update notice_files set url=%s where notice_id=%s", (URL + "2", original.notice_id),
        )
    provider = Mock(side_effect=AssertionError("stale source must not invoke Gemini"))
    monkeypatch.setattr("pipeline.transform.summarize.generate_summary_json", provider)
    result = summarize_and_save_prepared_notice(
        live_db, prepared, _metadata(prepared),
        expected_source_revision=original.content_revision, api_key="test-key",
    )
    assert isinstance(result, StoredSummarySuperseded)
    provider.assert_not_called()
    assert _row(live_db, original.notice_id) is None
