"""Adversarial plain-text location tests, independent of AI claim verification."""

import hashlib
from datetime import UTC, datetime

import pytest

from pipeline.storage.summary_view import NoticeSummaryTextView, build_notice_summary_view
from pipeline.transform.notice_input import AttachmentText, NoticeInput
from pipeline.transform.summary_highlights import build_summary_text_highlights
from pipeline.transform.summary_schema import CardSummaries, Evidence, NoticeSummary


def _notice(body: str, *attachment_texts: str, title: str = "공지") -> NoticeInput:
    return NoticeInput(
        title=title, body_text=body, reference_datetime=datetime(2026, 10, 7, tzinfo=UTC),
        attachments=[
            AttachmentText(name=f"첨부 {index}", text=text)
            for index, text in enumerate(attachment_texts)
        ],
    )


def _summary(*references: Evidence) -> NoticeSummary:
    return NoticeSummary(
        category="news", category_code=30, summary="공지 안내", publisher=None,
        applicable_area=None, audience=None, audience_scope="unknown", action=None,
        action_requirement="none", location=None, dates=[], status="not_applicable",
        status_detail=None, notice_update="new", changed_details=None, notes=[], topics=[],
        uncertainties=[], evidence=list(references),
    )


def _evidence(field: str, quote: str, **kwargs: object) -> Evidence:
    return Evidence.model_validate({"field": field, "excerpt": quote, **kwargs})


def _utf16_slice(text: str, start: int, end: int) -> str:
    return text.encode("utf-16-le")[start * 2:end * 2].decode("utf-16-le")


def test_exact_quotes_are_associated_with_named_cards_and_preserve_source() -> None:
    notice = _notice("주민 대상. 10월 7일까지. 신청서 제출. 비용은 2만원. 연장 안내.")
    summary = _summary(
        _evidence("audience", "주민 대상"), _evidence("dates", "10월 7일까지"),
        _evidence("action", "신청서 제출"), _evidence("notes", "비용은 2만원"),
        _evidence("changed_details", "연장 안내"), _evidence("summary", "주민 대상"),
    )
    before_notice, before_summary = notice.model_dump(), summary.model_dump()
    result = build_summary_text_highlights(summary, notice)

    assert result.offset_unit == "utf16"
    assert result.sources[0].text == notice.body_text
    assert result.sources[0].sha256 == hashlib.sha256(notice.body_text.encode()).hexdigest()
    assert [span.text for span in result.cards.audience.ranges] == ["주민 대상"]
    assert [span.text for span in result.cards.deadline.ranges] == ["10월 7일까지", "연장 안내"]
    assert [span.text for span in result.cards.action.ranges] == ["신청서 제출"]
    assert [span.text for span in result.cards.notes.ranges] == ["비용은 2만원", "연장 안내"]
    assert all(card.status == "ready" for card in (
        result.cards.audience, result.cards.deadline, result.cards.action, result.cards.notes,
    ))
    assert result.cards.audience.references[0].reference.source_path == "evidence[0]"
    assert result.cards.audience.references[0].reference.scope == "field"
    assert notice.model_dump() == before_notice
    assert summary.model_dump() == before_summary


def test_whitespace_matching_returns_original_range_and_never_promotes_verification() -> None:
    source = "앞\r\n신청서\u00a0  제출\t필수\r\n뒤"
    notice = _notice(source)
    summary = _summary(_evidence("action", "신청서 제출 필수"))
    result = build_summary_text_highlights(summary, notice)
    card = result.cards.action
    assert card.status == "ready"
    assert card.references[0].match_kind == "whitespace_normalized"
    assert card.references[0].reference.evidence.verification is None
    assert summary.evidence[0].verification is None
    assert len(card.ranges) == 1
    span = card.ranges[0]
    assert span.text == "신청서\u00a0  제출\t필수"
    assert _utf16_slice(source, span.start, span.end) == span.text


def test_emoji_offsets_are_javascript_utf16_and_end_is_exclusive() -> None:
    source = "😀 앞 🧑‍💻 신청서 제출 🐾 뒤"
    result = build_summary_text_highlights(
        _summary(_evidence("action", "신청서 제출")), _notice(source),
    )
    span = result.cards.action.ranges[0]
    assert span.start > source.index("신청서 제출")
    assert _utf16_slice(source, span.start, span.end) == "신청서 제출"
    assert span.end - span.start == len("신청서 제출")


@pytest.mark.parametrize("body,attachments", [
    ("제출 제출", ()), ("제출", ("제출",)), ("", ("제출", "제출")), ("aaa", ()),
])
def test_duplicate_and_overlapping_quotes_never_choose_first_location(
    body: str, attachments: tuple[str, ...],
) -> None:
    quote = "aa" if body == "aaa" else "제출"
    result = build_summary_text_highlights(_summary(_evidence("action", quote)),
                                           _notice(body, *attachments))
    assert result.cards.action.status == "ambiguous"
    assert result.cards.action.ranges == []
    assert len(result.cards.action.references[0].candidates) == 2


def test_exact_match_wins_over_looser_whitespace_match() -> None:
    result = build_summary_text_highlights(
        _summary(_evidence("action", "신청서 제출")), _notice("신청서 제출", "신청서\n제출"),
    )
    card = result.cards.action
    assert card.status == "ready"
    assert card.ranges[0].source_key == "body_text"
    assert card.references[0].match_kind == "exact"


def test_extracted_attachment_text_has_separate_identity_without_media_id() -> None:
    result = build_summary_text_highlights(
        _summary(_evidence("action", "서류 제출")), _notice("본문", "서류 제출"),
    )
    assert result.cards.action.ranges[0].source_key == "attachments[0].text"
    assert result.sources[1].label == "첨부 텍스트 1"
    assert result.cards.action.references[0].reference.evidence.source_id is None


def test_public_highlights_do_not_disclose_restricted_attachment_names() -> None:
    notice = _notice("본문", "서류 제출", "비용 안내")
    notice.attachments[0].name = "PRIVATE_마스킹대상_신청자명_명단.txt"
    notice.attachments[1].name = "PRIVATE_내부파일명.txt"
    view = build_notice_summary_view(
        status="needs_review", result=_summary(_evidence("action", "서류 제출")),
        attachment_status="all_read", notice=notice,
    )
    assert [source.label for source in view.text_highlights.sources] == [
        "본문", "첨부 텍스트 1", "첨부 텍스트 2",
    ]
    assert "PRIVATE_" not in view.model_dump_json()
    assert notice.attachments[0].name == "PRIVATE_마스킹대상_신청자명_명단.txt"


@pytest.mark.parametrize("quote", ["2만원", "10월8일", "신청서제출", "Cafe\u0301"])
def test_changed_numbers_removed_spaces_and_unicode_near_quotes_do_not_match(quote: str) -> None:
    result = build_summary_text_highlights(
        _summary(_evidence("action", quote)), _notice("3만원 10월7일 신청서 제출 Café"),
    )
    assert result.cards.action.status == "not_found"
    assert result.cards.action.ranges == []


@pytest.mark.parametrize("source_type", ["document", "image"])
def test_file_quote_never_becomes_body_highlight_even_when_words_match(source_type: str) -> None:
    summary = _summary(_evidence("action", "신청서 제출", source_type=source_type,
                                 source_id="media_1", page=1,
                                 verification="file_reference_only"))
    result = build_summary_text_highlights(summary, _notice("신청서 제출"))
    assert result.cards.action.status == "file_only"
    assert result.cards.action.ranges == []
    assert result.cards.action.references[0].status == "file_reference"
    assert result.cards.action.references[0].reference.evidence == summary.evidence[0]


@pytest.mark.parametrize("invalid", [{"source_id": "media_1"}, {"page": 1}])
def test_invalid_text_reference_identifiers_are_not_silently_ignored(invalid: dict) -> None:
    result = build_summary_text_highlights(
        _summary(_evidence("action", "제출", **invalid)), _notice("제출"),
    )
    assert result.cards.action.references[0].status == "invalid_reference"
    assert result.cards.action.ranges == []


def test_partial_ranges_keep_missing_and_ambiguous_references_and_deduplicate_spans() -> None:
    result = build_summary_text_highlights(_summary(
        _evidence("action", "신청서 제출"), _evidence("action", "신청서 제출"),
        _evidence("location", "주민센터"), _evidence("action_requirement", "필수"),
    ), _notice("신청서 제출. 주민센터 주민센터"))
    card = result.cards.action
    assert card.status == "partial"
    assert len(card.ranges) == 1
    assert [item.status for item in card.references] == [
        "matched", "matched", "ambiguous", "not_found",
    ]
    assert card.guidance is not None


def test_title_only_evidence_does_not_invent_a_body_source() -> None:
    result = build_summary_text_highlights(
        _summary(_evidence("audience", "월계1동 주민")),
        _notice("", title="월계1동 주민"),
    )
    assert result.sources == []
    assert result.cards.audience.status == "not_found"
    assert result.cards.notes.status == "no_evidence"


def test_view_optional_highlights_preserve_review_prose_and_old_shape() -> None:
    summary = _summary(_evidence("action", "서류 제출", verification="text_matched"))
    summary.action = "서류 제출"
    summary.card_summaries = CardSummaries.model_validate({
        "audience": None, "deadline": None, "action": "서류를 제출해 주세요", "notes": None,
    })
    notice = _notice("😀 서류 제출")
    kwargs = {"status": "needs_review", "result": summary, "attachment_status": "none"}
    before = build_notice_summary_view(**kwargs)
    after = build_notice_summary_view(**kwargs, notice=notice)
    assert set(before.model_dump()) == {"status", "message", "content"}
    assert isinstance(after, NoticeSummaryTextView)
    assert after.model_dump(exclude={"text_highlights"}) == before.model_dump()
    assert after.text_highlights.cards.action.ranges[0].text == "서류 제출"
    assert after.status == "needs_review"
    assert after.content.cards.action.text == "서류를 제출해 주세요"
    assert after.content.headline.text == summary.summary


@pytest.mark.parametrize("status", ["pending", "failed", "needs_review"])
def test_no_result_does_not_expose_source_text_or_highlights(status: str) -> None:
    view = build_notice_summary_view(status=status, result=None, attachment_status="none",
                                     notice=_notice("민감한 원문"))
    assert isinstance(view, NoticeSummaryTextView)
    assert view.content is None
    assert view.text_highlights is None


def test_highlight_ranges_are_plain_text_positions_and_do_not_strip_html() -> None:
    raw = "<p>신청서</p><p>제출</p>"
    result = build_summary_text_highlights(
        _summary(_evidence("action", "신청서 제출")), _notice(raw),
    )
    assert result.cards.action.status == "not_found"
    assert result.sources[0].text == raw
