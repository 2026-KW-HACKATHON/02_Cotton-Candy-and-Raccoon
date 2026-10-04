"""Check media reference provenance without claiming to inspect file contents."""

from typing import Literal

import pytest
from pydantic import ValidationError

from pipeline.transform.grounding import REVIEW_NOTE, ground_summary, unknown_summary
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_schema import (
    DateEntry,
    Evidence,
    MediaSource,
    NoticeSummary,
    SummaryValidationError,
    validate_evidence,
)

MediaKind = Literal["document", "image"]


def _notice(body: str = "", *, attachments: list[dict[str, str]] | None = None) -> NoticeInput:
    return NoticeInput.model_validate(
        {
            "title": "주민행사 개최",
            "body_text": body,
            "attachments": attachments or [],
            "reference_datetime": "2026-09-26T12:00:00+09:00",
        }
    )


def _quote(
    field: str,
    excerpt: str,
    *,
    source_type: str = "text",
    source_id: str | None = None,
    page: int | None = None,
    verification: str | None = None,
) -> dict:
    return {
        "field": field,
        "excerpt": excerpt,
        "source_type": source_type,
        "source_id": source_id,
        "page": page,
        "verification": verification,
    }


def _file_quote(field: str, excerpt: str, kind: MediaKind, source_id: str = "media_1") -> dict:
    return _quote(
        field,
        excerpt,
        source_type=kind,
        source_id=source_id,
        page=1 if kind == "document" else None,
    )


def _summary(notice: NoticeInput, evidence: list[dict], **overrides: object) -> NoticeSummary:
    data = unknown_summary(notice).model_dump()
    data.update(category="event", summary="주민행사 개최", uncertainties=[], evidence=evidence)
    data.update(overrides)
    return NoticeSummary.model_validate(data)


def _date(**overrides: str | None) -> DateEntry:
    data: dict[str, str | None] = {
        "kind": "event",
        "label": None,
        "text": None,
        "start_date": None,
        "end_date": None,
        "start_time": None,
        "end_time": None,
    }
    data.update(overrides)
    return DateEntry.model_validate(data)


@pytest.mark.parametrize("kind", ["document", "image"])
def test_file_only_claims_preserve_quotes_with_reference_only_verification(kind: MediaKind) -> None:
    notice = _notice()
    summary = _summary(
        notice,
        [
            _file_quote("summary", "주민행사 개최", kind),
            _file_quote("audience", "대상: 월계1동 주민", kind),
            _file_quote("action", "현장 참여 가능", kind),
            _file_quote("location", "장소: 월계공원", kind),
            _file_quote("notes", "참가비 무료", kind),
        ],
        audience="월계1동 주민",
        audience_scope="general",
        action="현장 참여",
        action_requirement="optional",
        location="월계공원",
        notes=["참가비 무료"],
    )
    sources = (MediaSource(source_id="media_1", source_type=kind),)

    result = ground_summary(summary, notice, media_sources=sources)

    assert result.category == "event"
    assert result.summary == "주민행사 개최"
    assert result.audience == "월계1동 주민"
    assert result.action == "현장 참여"
    assert result.action_requirement == "optional"
    assert result.location == "월계공원"
    assert result.notes == ["참가비 무료"]
    assert len(result.evidence) == 5
    assert {item.verification for item in result.evidence} == {"file_reference_only"}
    assert all(item.source_id == "media_1" for item in result.evidence)
    assert all(item.page == (1 if kind == "document" else None) for item in result.evidence)
    validate_evidence(result, body_text="", attachment_texts=[], media_sources=sources)


def test_mixed_body_hwp_pdf_and_image_keep_distinct_verification_scopes() -> None:
    notice = _notice(
        "주민행사 개최",
        attachments=[{"name": "안내.hwp", "text": "대상: 월계1동 주민"}],
    )
    summary = _summary(
        notice,
        [
            _quote("summary", "주민행사 개최"),
            _quote("audience", "대상: 월계1동 주민"),
            _file_quote("action", "현장 참여 가능", "document", "media_1"),
            _file_quote("notes", "참가비 무료", "image", "media_2"),
        ],
        audience="월계1동 주민",
        audience_scope="general",
        action="현장 참여",
        action_requirement="optional",
        notes=["참가비 무료"],
    )
    sources = (
        MediaSource(source_id="media_1", source_type="document"),
        MediaSource(source_id="media_2", source_type="image"),
    )

    result = ground_summary(summary, notice, media_sources=sources)

    assert result.summary == "주민행사 개최"
    assert result.audience == "월계1동 주민"
    assert result.action == "현장 참여"
    assert result.notes == ["참가비 무료"]
    assert {item.field: item.verification for item in result.evidence} == {
        "summary": "text_matched",
        "audience": "text_matched",
        "action": "file_reference_only",
        "notes": "file_reference_only",
    }


@pytest.mark.parametrize(
    ("source_type", "source_id", "page", "actual_kind"),
    [
        ("document", "missing", 1, "document"),
        ("document", None, 1, "document"),
        ("document", "media_1", None, "document"),
        ("image", "media_1", None, "document"),
        ("document", "media_1", 1, "image"),
        ("image", "media_1", 1, "image"),
        ("text", "media_1", None, "document"),
        ("text", None, 1, "document"),
    ],
)
def test_invalid_file_or_text_reference_cannot_preserve_claims(
    source_type: str, source_id: str | None, page: int | None, actual_kind: MediaKind
) -> None:
    notice = _notice("주민행사 개최")
    summary = _summary(
        notice,
        [
            _quote("summary", "주민행사 개최"),
            _quote(
                "action",
                "현장 참여 가능",
                source_type=source_type,
                source_id=source_id,
                page=page,
            ),
        ],
        action="현장 참여",
        action_requirement="optional",
    )
    sources = (MediaSource(source_id="media_1", source_type=actual_kind),)

    with pytest.raises(SummaryValidationError):
        validate_evidence(
            summary, body_text=notice.body_text, attachment_texts=[], media_sources=sources
        )

    result = ground_summary(summary, notice, media_sources=sources)

    assert result.action is None
    assert result.action_requirement == "unknown"
    assert all(item.field != "action" for item in result.evidence)
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("page", [0, -1, True, "1"])
def test_pdf_page_must_be_a_positive_integer(page: object) -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(
            _quote(
                "summary", "주민행사 개최", source_type="document", source_id="media_1", page=page
            )
        )


def test_claimed_file_reference_requires_the_actual_input_context() -> None:
    notice = _notice("주민행사 개최")
    summary = _summary(
        notice,
        [
            _quote("summary", "주민행사 개최"),
            _file_quote("action", "현장 참여 가능", "document"),
        ],
        action="현장 참여",
        action_requirement="optional",
    )

    result = ground_summary(summary, notice)

    assert result.action is None
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("claimed", [None, "text_matched", "file_reference_only"])
def test_model_cannot_claim_text_matching_for_a_file_quote(claimed: str | None) -> None:
    notice = _notice()
    evidence = _file_quote("summary", "주민행사 개최", "document")
    evidence["verification"] = claimed
    summary = _summary(notice, [evidence])

    result = ground_summary(
        summary,
        notice,
        media_sources=(MediaSource(source_id="media_1", source_type="document"),),
    )

    assert result.evidence[0].verification == "file_reference_only"
    assert summary.evidence[0].verification == claimed


def test_legacy_text_quote_gets_text_matched_even_if_model_claims_file_reference() -> None:
    notice = _notice("주민행사 개최")
    summary = _summary(
        notice,
        [_quote("summary", "주민행사 개최", verification="file_reference_only")],
    )

    result = ground_summary(summary, notice)

    assert result.evidence[0].verification == "text_matched"
    assert result.evidence[0].source_type == "text"
    assert result.evidence[0].source_id is None
    assert result.evidence[0].page is None


def test_available_media_does_not_turn_an_invented_text_quote_into_valid_evidence() -> None:
    notice = _notice("주민행사 개최")
    summary = _summary(
        notice,
        [
            _quote("summary", "주민행사 개최"),
            _quote("action", "온라인 신청 가능", verification="text_matched"),
        ],
        action="온라인 신청",
        action_requirement="optional",
    )

    result = ground_summary(
        summary,
        notice,
        media_sources=(MediaSource(source_id="media_1", source_type="document"),),
    )

    assert result.action is None
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("kind", ["document", "image"])
def test_file_quote_still_rejects_negated_action_and_cost(kind: MediaKind) -> None:
    notice = _notice()
    summary = _summary(
        notice,
        [
            _file_quote("summary", "주민행사 개최", kind),
            _file_quote("action", "현장 참여 불가", kind),
            _file_quote("notes", "참가비 무료 아님", kind),
        ],
        action="현장 참여",
        action_requirement="optional",
        notes=["참가비 무료"],
    )

    result = ground_summary(
        summary, notice, media_sources=(MediaSource(source_id="media_1", source_type=kind),)
    )

    assert result.summary == "주민행사 개최"
    assert result.action is None
    assert result.notes == []
    assert result.uncertainties == [REVIEW_NOTE]


def test_file_audience_quote_does_not_drop_its_conditions() -> None:
    notice = _notice()
    summary = _summary(
        notice,
        [
            _file_quote("summary", "주민행사 개최", "document"),
            _file_quote("audience", "대상: 월계1동 주민 중 65세 이상", "document"),
        ],
        audience="월계1동 주민",
        audience_scope="general",
    )

    result = ground_summary(
        summary,
        notice,
        media_sources=(MediaSource(source_id="media_1", source_type="document"),),
    )

    assert result.audience is None
    assert result.audience_scope == "unknown"
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("kind", ["document", "image"])
def test_file_schedule_retains_korean_clock_and_date_roles(kind: MediaKind) -> None:
    notice = _notice()
    entry = _date(
        start_date="2026-09-26",
        end_date="2026-09-26",
        start_time="09:00",
        end_time="18:00",
    )
    summary = _summary(
        notice,
        [
            _file_quote("summary", "주민행사 개최", kind),
            _file_quote("dates", "행사: 2026-09-26 오전 9시~오후 6시", kind),
        ],
        dates=[entry.model_dump()],
        status="ongoing",
    )

    result = ground_summary(
        summary, notice, media_sources=(MediaSource(source_id="media_1", source_type=kind),)
    )

    assert result.dates == [entry]
    assert result.status == "ongoing"
    assert all(item.verification == "file_reference_only" for item in result.evidence)


def test_file_period_start_cannot_be_claimed_as_end_and_marked_ended() -> None:
    notice = _notice()
    summary = _summary(
        notice,
        [
            _file_quote("summary", "주민행사 개최", "document"),
            _file_quote("dates", "행사: 2026-09-25~2026-09-30", "document"),
        ],
        dates=[_date(end_date="2026-09-25").model_dump()],
        status="ended",
    )

    result = ground_summary(
        summary,
        notice,
        media_sources=(MediaSource(source_id="media_1", source_type="document"),),
    )

    assert result.dates == []
    assert result.status == "unknown"
    assert all(item.field != "dates" for item in result.evidence)
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("fragment_kind", ["range", "clock"])
def test_schedule_cannot_borrow_another_files_period_endpoint_or_clock(fragment_kind: str) -> None:
    notice = _notice()
    if fragment_kind == "range":
        first = "행사 시작: 2026-09-25"
        second = "행사 종료: 2026-09-30"
        entry = _date(start_date="2026-09-25", end_date="2026-09-30")
    else:
        first = "행사: 2026-09-26 오전 9시"
        second = "행사: 2026-09-26 오후 6시 종료"
        entry = _date(
            start_date="2026-09-26",
            end_date="2026-09-26",
            start_time="09:00",
            end_time="18:00",
        )
    summary = _summary(
        notice,
        [
            _file_quote("summary", "주민행사 개최", "document", "media_1"),
            _file_quote("dates", first, "document", "media_1"),
            _file_quote("dates", second, "image", "media_2"),
        ],
        dates=[entry.model_dump()],
        status="ongoing",
    )

    result = ground_summary(
        summary,
        notice,
        media_sources=(
            MediaSource(source_id="media_1", source_type="document"),
            MediaSource(source_id="media_2", source_type="image"),
        ),
    )

    assert result.dates == []
    assert result.status == "unknown"
    assert result.uncertainties == [REVIEW_NOTE]


def test_file_recurring_schedule_preserves_unresolved_original_expression() -> None:
    notice = _notice()
    entry = _date(text="매주 토요일")
    summary = _summary(
        notice,
        [
            _file_quote("summary", "주민행사 개최", "image"),
            _file_quote("dates", "행사: 매주 토요일", "image"),
        ],
        dates=[entry.model_dump()],
        status="check_required",
    )

    result = ground_summary(
        summary,
        notice,
        media_sources=(MediaSource(source_id="media_1", source_type="image"),),
    )

    assert result.dates == [entry]
    assert result.status == "check_required"


def test_media_with_no_supported_summary_remains_review_required() -> None:
    notice = _notice()
    summary = _summary(
        notice,
        [_file_quote("summary", "행사 참여 불가", "image")],
    )

    result = ground_summary(
        summary,
        notice,
        media_sources=(MediaSource(source_id="media_1", source_type="image"),),
    )

    assert result.category == "unknown"
    assert result.summary == REVIEW_NOTE
    assert result.evidence == []
    assert result.uncertainties == [REVIEW_NOTE]
