"""Explicit file provenance binds actual request bytes without exposing private data."""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from support.gemini_multimodal import _media, _prepared
from support.prepared_summary_storage import _response

from pipeline import summary_job
from pipeline.storage.summary_metadata import (
    SummaryAttachmentText,
    build_summary_metadata_from_manifest,
    compute_source_hash,
)
from pipeline.storage.summary_record import SummaryRecordError
from pipeline.transform import summarize as summarize_module
from pipeline.transform.notice_input import render_notice_input
from pipeline.transform.prepared_summary import SummaryPreparationError, prepare_gemini_request
from pipeline.transform.summary_files import (
    PreparedMediaBinding,
    PreparedSourceFile,
    PrivateSummaryFileManifest,
    manifest_snapshot,
)

DIGEST = hashlib.sha256(b"original attachment").hexdigest()
TEXT_DIGEST = hashlib.sha256("행사 안내".encode()).hexdigest()
ORIGINAL_URL = "https://example.org/notices/17"


def source_file(file_id=1, **updates):
    return PreparedSourceFile.model_validate({
        "notice_file_id": file_id, "file_key": "id:source-private",
        "kind": "attachment", "url": f"https://example.org/files/{file_id}",
        "outcome": "media", "source_id": "media_1", "content_sha256": DIGEST,
    } | updates)


def binding(**updates):
    return PreparedMediaBinding.model_validate({
        "source_id": "media_1", "source_type": "document", "input_block_index": 1,
        "content_sha256": DIGEST,
    } | updates)


def manifest(*, files=None, media=None, **updates):
    return PrivateSummaryFileManifest(
        **({"notice_id": 17, "source_revision": 4, "original_url": ORIGINAL_URL,
            "files": (source_file(),) if files is None else files,
            "media": (binding(),) if media is None else media} | updates),
    )


def prepared_with_manifest(value=None):
    prepared = _prepared("", _media("document"))
    prepared.file_manifest = manifest() if value is None else value
    return prepared


def test_three_original_rows_sharing_two_media_are_all_read_without_order_guesses():
    prepared = _prepared("", _media("document"), _media("image", b"poster"))
    poster_digest = hashlib.sha256(b"poster").hexdigest()
    value = manifest(files=(
        source_file(3, file_key="id:poster", source_id="media_2", kind="inline_image",
                    content_sha256=poster_digest),
        source_file(1), source_file(2, kind="inline_image"),
    ), media=(binding(source_id="media_2", source_type="image", input_block_index=2,
                     content_sha256=poster_digest), binding()))
    prepared.file_manifest = value
    _, media = prepare_gemini_request(prepared)
    metadata = build_summary_metadata_from_manifest(
        notice=prepared.notice, file_manifest=value, model="test-model",
    )
    assert value.total_file_count == value.read_file_count == 3
    assert metadata.attachment_status == "all_read"
    assert len(media) == 2
    refs = {item.source_id: item for item in value.public_references()}
    assert [item.notice_file_id for item in refs["media_1"].files] == [1, 2]
    public_json = json.dumps([item.model_dump(mode="json") for item in refs.values()])
    assert "source-private" not in public_json
    assert "content_sha256" not in public_json and "outcome" not in public_json


def test_unregistered_body_image_links_to_original_notice_without_invented_file():
    prepared = _prepared("", _media("image"))
    value = manifest(files=(), media=(binding(source_type="image"),))
    prepared.file_manifest = value
    prepare_gemini_request(prepared)
    ref, = value.public_references()
    assert ref.files == ()
    assert ref.guidance == "원문에서 확인"
    assert ref.original_notice_url == ORIGINAL_URL
    assert value.attachment_status == "none"


@pytest.mark.parametrize("changes", [
    {"content_sha256": "0" * 64}, {"source_type": "image"},
    {"input_block_index": 0}, {"source_id": "media_2"},
])
def test_misbound_media_is_rejected_before_provider(monkeypatch, changes):
    altered = binding(**changes)
    files = () if changes.get("source_id") else (
        source_file(content_sha256=altered.content_sha256),
    )
    prepared = prepared_with_manifest(manifest(files=files, media=(altered,)))
    provider = MagicMock(side_effect=AssertionError("must not call Gemini"))
    monkeypatch.setattr(summarize_module, "generate_summary_json", provider)
    with pytest.raises(SummaryPreparationError, match="^invalid_prepared_input$"):
        summarize_module.summarize_prepared_notice(prepared, api_key="test-key")
    provider.assert_not_called()


def test_omitted_media_binding_is_rejected():
    prepared = prepared_with_manifest(manifest(files=(), media=()))
    with pytest.raises(SummaryPreparationError, match="^invalid_prepared_input$"):
        prepare_gemini_request(prepared)


@pytest.mark.parametrize("kind", ["missing", "unread", "wrong-text-hash", "wrong-index"])
def test_every_extracted_text_requires_an_explicit_matching_source(kind):
    prepared = _prepared("", attachment_text=True)
    file = source_file(outcome="text", source_id=None, attachment_index=0,
                       content_sha256=TEXT_DIGEST)
    if kind == "missing":
        files = ()
    elif kind == "unread":
        files = (source_file(outcome="unread", source_id=None, content_sha256=None),)
    else:
        files = (file.model_copy(update={
            "content_sha256": "0" * 64,
        } if kind == "wrong-text-hash" else {"attachment_index": 1}),)
    value = manifest(files=files, media=())
    prepared.file_manifest = value
    with pytest.raises(SummaryPreparationError, match="^invalid_prepared_input$"):
        prepare_gemini_request(prepared)
    with pytest.raises(SummaryRecordError, match="^invalid_prepared_input$"):
        build_summary_metadata_from_manifest(
            notice=prepared.notice, file_manifest=value, model="test-model",
        )


def test_duplicate_original_text_rows_count_as_read_but_hash_once_per_key():
    prepared = _prepared("본문", attachment_text=True)
    value = manifest(files=tuple(source_file(
        file_id, outcome="text", source_id=None, attachment_index=0,
        content_sha256=TEXT_DIGEST,
    ) for file_id in (1, 2)), media=())
    prepared.file_manifest = value
    prepare_gemini_request(prepared)
    metadata = build_summary_metadata_from_manifest(
        notice=prepared.notice, file_manifest=value, model="test-model",
    )
    assert value.total_file_count == value.read_file_count == 2
    assert metadata.attachment_status == "all_read"
    assert metadata.source_hash == compute_source_hash(
        "본문", (SummaryAttachmentText("id:source-private", "행사 안내"),),
    )


@pytest.mark.parametrize("mutation", ["attachment", "body", "extra-text", "missing-text"])
def test_manifest_binds_the_actual_transmitted_text_snapshot_before_provider(monkeypatch, mutation):
    prepared = _prepared("본문", attachment_text=True)
    prepared.file_manifest = manifest(files=(source_file(
        outcome="text", source_id=None, attachment_index=0, content_sha256=TEXT_DIGEST,
    ),), media=())
    if mutation in {"attachment", "body"}:
        changed = prepared.notice.model_copy(deep=True)
        if mutation == "attachment":
            changed.attachments[0].text = "전혀 다른 파일 내용"
        else:
            changed.body_text = "전혀 다른 본문"
        prepared.blocks[0]["text"] = render_notice_input(changed)
    elif mutation == "extra-text":
        prepared.blocks.append({"type": "text", "text": "추가로 끼운 다른 파일 내용"})
    else:
        prepared.blocks = [_media("document")]
    provider = MagicMock(side_effect=AssertionError("must not call Gemini"))
    monkeypatch.setattr(summarize_module, "generate_summary_json", provider)
    with pytest.raises(SummaryPreparationError, match="^invalid_prepared_input$"):
        summarize_module.summarize_prepared_notice(prepared, api_key="test-key")
    provider.assert_not_called()


def test_unread_original_row_counts_as_partial_without_fake_media_identity():
    value = manifest(files=(source_file(), source_file(
        2, file_key="id:unread", outcome="unread", source_id=None, content_sha256=None,
    )))
    assert value.total_file_count == 2 and value.read_file_count == 1
    assert value.attachment_status == "partial"
    assert len(value.public_references()[0].files) == 1


@pytest.mark.parametrize("attack", ["nested-boolean", "extra-private", "wrong-type"])
def test_unchecked_model_construction_cannot_bypass_manifest_snapshot(attack):
    value = manifest()
    if attack == "nested-boolean":
        value = value.model_copy(update={"files": (source_file().model_copy(
            update={"notice_file_id": True},
        ),)})
    elif attack == "extra-private":
        value = value.model_copy(update={"secret": "PRIVATE_MARKER"})
        # Strict models discard model_copy extras when serialized; subclass fields
        # are different and are visible to the checked snapshot.
        class Forged(PrivateSummaryFileManifest):
            secret: str
        value = Forged(**manifest().model_dump(), secret="PRIVATE_MARKER")
    else:
        value = value.model_dump()
    with pytest.raises((ValueError, TypeError)):
        manifest_snapshot(value)


def forged_integer_manifest(location, value):
    if location == "attachment_index":
        file = source_file(outcome="text", source_id=None, attachment_index=0,
                           content_sha256=TEXT_DIGEST)
        return manifest(files=(file,), media=()).model_copy(update={"files": (
            file.model_copy(update={location: value}),
        )})
    original = manifest()
    if location in {"notice_id", "source_revision"}:
        return original.model_copy(update={location: value})
    if location == "notice_file_id":
        return original.model_copy(update={"files": (
            original.files[0].model_copy(update={location: value}),
        )})
    return original.model_copy(update={"media": (
        original.media[0].model_copy(update={location: value}),
    )})


@pytest.mark.parametrize("location", [
    "notice_id", "source_revision", "notice_file_id", "input_block_index", "attachment_index",
])
@pytest.mark.parametrize("invalid", [True, False, 1.0, "PRIVATE_PRIMITIVE_INTEGER", None])
def test_unchecked_primitive_integer_never_becomes_a_valid_id_or_index(
    monkeypatch, location, invalid,
):
    value = forged_integer_manifest(location, invalid)
    with pytest.raises((ValueError, TypeError)):
        manifest_snapshot(value)
    prepared = (
        _prepared("", attachment_text=True) if location == "attachment_index"
        else _prepared("", _media("document"))
    )
    prepared.file_manifest = value
    provider = MagicMock(side_effect=AssertionError("must not call Gemini"))
    monkeypatch.setattr(summarize_module, "generate_summary_json", provider)
    with pytest.raises(SummaryPreparationError) as caught:
        summarize_module.summarize_prepared_notice(prepared, api_key="test-key")
    assert str(caught.value) == "invalid_prepared_input"
    assert "PRIVATE_PRIMITIVE_INTEGER" not in str(caught.value)
    provider.assert_not_called()


@pytest.mark.parametrize("location", ["notice_id", "source_revision", "notice_file_id"])
@pytest.mark.parametrize("invalid", [0, -1, 2**63])
def test_unchecked_database_identity_still_obeys_positive_bigint_bounds(location, invalid):
    with pytest.raises((ValueError, TypeError)):
        manifest_snapshot(forged_integer_manifest(location, invalid))


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "file:///tmp/a", "https://u:p@example.org/a",
    "https://example.org/a\nsecret", " https://example.org/a", "https://example.org:99999/a",
])
def test_private_manifest_rejects_unsafe_original_url(url):
    with pytest.raises(ValidationError):
        manifest(original_url=url)


def test_retries_keep_original_media_and_captured_manifest_out_of_prompt(monkeypatch):
    prepared = prepared_with_manifest()
    captured = prepared.file_manifest
    calls = []

    def generate(**kwargs):
        calls.append(deepcopy(kwargs))
        prepared.file_manifest = None
        return "bad JSON" if len(calls) == 1 else json.dumps(_response("pdf"))

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    result = summarize_module.summarize_prepared_notice(prepared, api_key="test-key")
    assert len(calls) == 2
    assert result.file_manifest == captured
    assert result.file_manifest is not captured
    assert all(call["notice_text"][1] == _media("document") for call in calls)
    assert "source-private" not in repr(calls)
    assert "content_sha256" not in repr(calls)


@pytest.mark.parametrize("attack", ["revision", "attachment-status", "notice-id"])
def test_job_rejects_mismatched_manifest_before_db_or_provider(monkeypatch, attack):
    prepared = prepared_with_manifest()
    metadata = build_summary_metadata_from_manifest(
        notice=prepared.notice, file_manifest=prepared.file_manifest, model="test-model",
    )
    if attack == "revision":
        revision = 5
    else:
        revision = 4
    if attack == "attachment-status":
        metadata = replace(metadata, attachment_status="partial")
    if attack == "notice-id":
        prepared.file_manifest = manifest(notice_id=18)
    conn = MagicMock()
    provider = MagicMock()
    monkeypatch.setattr(summary_job, "summarize_prepared_notice", provider)
    with pytest.raises(SummaryRecordError):
        summary_job.summarize_and_save_prepared_notice(
            conn, prepared, metadata, expected_source_revision=revision,
        )
    assert conn.mock_calls == []
    provider.assert_not_called()


def test_job_carries_captured_manifest_through_generation_to_storage(monkeypatch):
    prepared = prepared_with_manifest()
    metadata = build_summary_metadata_from_manifest(
        notice=prepared.notice, file_manifest=prepared.file_manifest, model="test-model",
    )
    conn = MagicMock()
    registration = MagicMock(return_value=77)
    save = MagicMock(return_value="saved")
    monkeypatch.setattr(summary_job, "begin_summary_execution", registration)
    monkeypatch.setattr(summary_job, "save_prepared_summary", save)
    monkeypatch.setattr(summarize_module, "generate_summary_json",
                        lambda **kwargs: json.dumps(_response("pdf")))
    assert summary_job.summarize_and_save_prepared_notice(
        conn, prepared, metadata, expected_source_revision=4, api_key="test-key",
    ) == "saved"
    assert save.call_args.args[1].file_manifest == prepared.file_manifest
    assert save.call_args.kwargs["execution_token"] == 77


def test_legacy_preparer_keeps_summary_without_fabricated_file_links(monkeypatch):
    prepared = _prepared("", _media("document"))
    monkeypatch.setattr(summarize_module, "generate_summary_json",
                        lambda **kwargs: json.dumps(_response("pdf")))
    result = summarize_module.summarize_prepared_notice(prepared, api_key="test-key")
    assert result.summary.summary == "행사 안내"
    assert result.file_manifest is None
