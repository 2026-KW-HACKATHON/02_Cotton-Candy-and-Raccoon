"""Public file links come from stored provenance and actual field evidence."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest

from pipeline.storage.summary_view import (
    NoticeSummaryFileView,
    NoticeSummaryTextFileView,
    NoticeSummaryTextView,
    build_notice_summary_view,
)
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_cards import SummaryCardError, build_summary_cards
from pipeline.transform.summary_files import (
    PreparedSourceFile,
    PublicFileReference,
    PublicSourceFile,
)
from pipeline.transform.summary_schema import Evidence, NoticeSummary

PRIVATE_MARKER = "PRIVATE_INTERNAL_FILE_METADATA"
NOTICE_URL = "https://www.nowon.kr/notices/notice-1"
PDF_URL = "https://www.nowon.kr/files/notice.pdf"
IMAGE_URL = "https://www.nowon.kr/files/poster.png"


def _evidence(field: str = "action", **changes: Any) -> Evidence:
    return Evidence.model_validate({
        "field": field, "excerpt": "신청서를 제출하세요", "source_type": "document",
        "source_id": "media_1", "page": 1, "verification": "file_reference_only",
    } | changes)


def _summary(*references: Evidence) -> NoticeSummary:
    return NoticeSummary(
        category="application", category_code=27, summary="주민 지원사업 신청 안내",
        publisher=None, applicable_area=None, audience="주민", audience_scope="general",
        action="신청서 제출", action_requirement="optional", location=None, dates=[],
        status="open", status_detail=None, notice_update="new", changed_details=None,
        notes=["신분증 지참"], topics=[], uncertainties=[], evidence=list(references),
    )


def _reference(**changes: Any) -> PublicFileReference:
    return PublicFileReference(
        source_id="media_1", source_type="document",
        files=(PublicSourceFile(notice_file_id=71, kind="attachment", url=PDF_URL),),
        original_notice_url=None, guidance=None,
    ).model_copy(update=changes)


def _view(summary: NoticeSummary | dict, references: Any, **changes: Any) -> NoticeSummaryFileView:
    return build_notice_summary_view(**{
        "status": "needs_review", "result": summary, "attachment_status": "all_read",
        "file_references": references,
    } | changes)


def _notice() -> NoticeInput:
    return NoticeInput(
        title="공지", body_text="신청서를 제출하세요. 신분증을 지참하세요.",
        reference_datetime=datetime(2026, 10, 7, tzinfo=UTC),
    )


def test_omitted_mapping_preserves_legacy_shape_and_never_invents_a_file_link() -> None:
    summary = _summary(_evidence())
    legacy = build_notice_summary_view(
        status="needs_review", result=summary, attachment_status="all_read",
    )
    text = build_notice_summary_view(
        status="needs_review", result=summary, attachment_status="all_read", notice=_notice(),
    )
    assert set(legacy.model_dump()) == {"status", "message", "content"}
    assert isinstance(text, NoticeSummaryTextView)
    assert set(text.model_dump()) == {"status", "message", "content", "text_highlights"}
    assert legacy.content == build_summary_cards(summary)


def test_db_json_aliases_keep_all_original_file_ids_without_media_order_inference() -> None:
    first = _reference(files=(
        PublicSourceFile(notice_file_id=93, kind="attachment", url=PDF_URL),
        PublicSourceFile(notice_file_id=71, kind="inline_image", url=PDF_URL),
    ))
    second = _reference(
        source_id="media_2", source_type="image", files=(
            PublicSourceFile(notice_file_id=84, kind="inline_image", url=IMAGE_URL),
        ),
    )
    db_json = [second.model_dump(mode="json"), first.model_dump(mode="json")]
    before = deepcopy(db_json)
    summary = _summary(_evidence(), _evidence(
        "notes", source_id="media_2", source_type="image", page=None,
    ))
    view = _view(summary.model_dump(mode="json"), db_json)
    assert [item.source_id for item in view.file_references] == ["media_2", "media_1"]
    assert [item.notice_file_id for item in view.file_references[1].files] == [93, 71]
    assert len(view.file_references) == 2
    assert sum(len(item.files) for item in view.file_references) == 3
    action_reference = view.content.cards.action.items[0].evidence[0].evidence
    assert (action_reference.source_id, action_reference.source_type) == (
        view.file_references[1].source_id, view.file_references[1].source_type,
    )
    assert set(view.model_dump(mode="json")) == {
        "status", "message", "content", "file_references",
    }
    assert set(view.file_references[1].model_dump()) == {
        "source_id", "source_type", "files", "original_notice_url", "guidance",
    }
    assert set(view.file_references[1].files[0].model_dump()) == {
        "notice_file_id", "kind", "url",
    }
    assert db_json == before
    db_json[1]["files"][0]["url"] = IMAGE_URL
    assert view.file_references[1].files[0].url == PDF_URL
    assert view.content == build_summary_cards(summary)


@pytest.mark.parametrize("evidence_changes", [
    {"source_id": "media_9"},
    {"source_type": "image", "page": None},
    {"source_type": "text", "source_id": None, "page": None},
    {"source_type": "text", "source_id": "media_1", "page": None},
])
def test_link_requires_both_actual_evidence_source_id_and_type(evidence_changes: dict) -> None:
    summary = _summary(_evidence(**evidence_changes))
    view = _view(summary, (_reference(),))
    assert view.file_references == ()
    assert view.content == build_summary_cards(summary)
    assert PDF_URL not in view.model_dump_json()


def test_unused_source_mapping_is_not_exposed_and_repeated_evidence_has_one_link() -> None:
    used = _reference()
    unused = _reference(source_id="media_2")
    summary = _summary(_evidence(), _evidence("notes"), _evidence("summary"))
    view = _view(summary, (unused, used))
    assert view.file_references == (used,)
    assert len(view.content.metadata.evidence) == 3


def test_body_image_without_db_alias_keeps_summary_and_links_only_original_notice() -> None:
    reference = PublicFileReference(
        source_id="media_1", source_type="image", files=(),
        original_notice_url=NOTICE_URL, guidance="원문에서 확인",
    )
    summary = _summary(_evidence(source_type="image", page=None))
    view = _view(summary, (reference,))
    assert view.file_references == (reference,)
    assert view.file_references[0].files == ()
    assert view.file_references[0].original_notice_url == NOTICE_URL
    assert view.file_references[0].guidance == "원문에서 확인"
    assert view.content == build_summary_cards(summary)
    assert view.content.headline.text == summary.summary
    assert view.content.cards.action.text is None
    assert IMAGE_URL not in view.model_dump_json()


@pytest.mark.parametrize("status", ["pending", "failed", "needs_review"])
@pytest.mark.parametrize("with_notice", [False, True])
def test_no_result_hides_links_without_inspecting_unavailable_private_input(
    status: str, with_notice: bool,
) -> None:
    kwargs = {"notice": _notice()} if with_notice else {}
    malformed = [{"source_id": PRIVATE_MARKER, "file_key": PRIVATE_MARKER}]
    result = {"summary": PRIVATE_MARKER} if status in {"pending", "failed"} else None
    view = _view(result, malformed, status=status, **kwargs)
    assert view.content is None
    assert view.file_references == ()
    assert view.model_dump(mode="json")["file_references"] == []
    if with_notice:
        assert isinstance(view, NoticeSummaryTextFileView)
        assert isinstance(view, NoticeSummaryTextView)
        assert view.text_highlights is None
    assert PRIVATE_MARKER not in view.model_dump_json()
    assert NOTICE_URL not in view.model_dump_json()


def test_file_links_and_text_highlights_coexist_without_highlighting_file_quote() -> None:
    summary = _summary(
        _evidence(), _evidence("notes", excerpt="신분증을 지참하세요", source_type="text",
                               source_id=None, page=None, verification="text_matched"),
    )
    before_summary = summary.model_dump(mode="json")
    before_notice = _notice()
    view = _view(summary, (_reference(),), notice=before_notice)
    assert isinstance(view, NoticeSummaryTextFileView)
    assert isinstance(view, NoticeSummaryFileView)
    assert isinstance(view, NoticeSummaryTextView)
    assert set(view.model_dump()) == {
        "status", "message", "content", "text_highlights", "file_references",
    }
    assert view.text_highlights.cards.action.status == "file_only"
    assert view.text_highlights.cards.action.ranges == []
    assert view.text_highlights.cards.notes.ranges[0].text == "신분증을 지참하세요"
    assert view.file_references == (_reference(),)
    assert summary.model_dump(mode="json") == before_summary
    assert before_notice == _notice()


@pytest.mark.parametrize("private_key", ["file_key", "content_sha256", "outcome", "file_name"])
@pytest.mark.parametrize("at_alias", [False, True])
def test_restricted_fields_are_rejected_even_on_unused_references(
    private_key: str, at_alias: bool,
) -> None:
    data = _reference(source_id="media_9").model_dump(mode="json")
    target = data["files"][0] if at_alias else data
    target[private_key] = PRIVATE_MARKER
    with pytest.raises(SummaryCardError) as caught:
        _view(_summary(_evidence()), [data])
    assert caught.value.reason_code == "invalid_file_references"
    assert str(caught.value) == "invalid_file_references"
    assert PRIVATE_MARKER not in str(caught.value)


@pytest.mark.parametrize("invalid_id", [True, "71", 71.0, 0, -1, 2**63])
def test_strict_json_validation_does_not_coerce_file_identifiers(invalid_id: Any) -> None:
    data = _reference().model_dump(mode="json")
    data["files"][0]["notice_file_id"] = invalid_id
    with pytest.raises(SummaryCardError, match="^invalid_file_references$"):
        _view(_summary(_evidence()), [data])


@pytest.mark.parametrize("change", [
    {"url": f"javascript:{PRIVATE_MARKER}"},
    {"url": f"https://user:{PRIVATE_MARKER}@www.nowon.kr/file.pdf"},
    {"kind": PRIVATE_MARKER},
])
def test_malformed_public_alias_exposes_only_safe_error(change: dict) -> None:
    data = _reference().model_dump(mode="json")
    data["files"][0].update(change)
    with pytest.raises(SummaryCardError) as caught:
        _view(_summary(_evidence()), [data])
    assert str(caught.value) == "invalid_file_references"
    assert PRIVATE_MARKER not in str(caught.value)


@pytest.mark.parametrize("invalid", [
    PRIVATE_MARKER, {}, [PRIVATE_MARKER], [None], [True],
])
def test_invalid_reference_container_is_safely_rejected(invalid: Any) -> None:
    with pytest.raises(SummaryCardError, match="^invalid_file_references$"):
        _view(_summary(_evidence()), invalid)


def test_duplicate_source_id_is_rejected_even_if_type_differs() -> None:
    references = (_reference(), _reference(source_type="image"))
    with pytest.raises(SummaryCardError, match="^invalid_file_references$"):
        _view(_summary(_evidence()), references)


def test_unchecked_nested_model_is_revalidated_and_private_subclass_is_rejected() -> None:
    corrupted = _reference(files=(PublicSourceFile(
        notice_file_id=71, kind="attachment", url=PDF_URL,
    ).model_copy(update={"url": PRIVATE_MARKER}),))
    restricted = PreparedSourceFile(
        notice_file_id=71, kind="attachment", url=PDF_URL,
        file_key=f"id:{PRIVATE_MARKER}", outcome="unread",
    )
    subclass = _reference(files=(restricted,))
    for invalid in (corrupted, subclass):
        with pytest.raises(SummaryCardError) as caught:
            _view(_summary(_evidence()), (invalid,))
        assert str(caught.value) == "invalid_file_references"
        assert PRIVATE_MARKER not in str(caught.value)


def test_explicit_empty_mapping_has_no_fabricated_fallback() -> None:
    summary = _summary(_evidence())
    view = _view(summary, [])
    assert isinstance(view, NoticeSummaryFileView)
    assert view.file_references == ()
    assert view.content == build_summary_cards(summary)
    assert NOTICE_URL not in view.model_dump_json()


@pytest.mark.parametrize("change", [
    {"files": [], "original_notice_url": None, "guidance": None},
    {"original_notice_url": NOTICE_URL, "guidance": "원문에서 확인"},
    {"files": [], "original_notice_url": NOTICE_URL, "guidance": PRIVATE_MARKER},
])
def test_missing_or_conflicting_original_notice_fallback_is_not_guessed(change: dict) -> None:
    data = _reference().model_dump(mode="json") | change
    with pytest.raises(SummaryCardError, match="^invalid_file_references$"):
        _view(_summary(_evidence()), [data])


def test_excessive_file_reference_collection_is_rejected_before_projection() -> None:
    with pytest.raises(SummaryCardError, match="^invalid_file_references$"):
        _view(_summary(_evidence()), [_reference()] * 1025)
