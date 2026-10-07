"""Keep publishable news usable without widening the evidence rules."""

from typing import Any

import pytest

from pipeline.storage.summary_record import summary_requires_review
from pipeline.transform.grounding import REVIEW_NOTE, ground_summary, preserve_uncertain_summary
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_schema import (
    Evidence,
    MediaSource,
    NoticeSummary,
    SummaryValidationError,
    evidence_reference_valid,
    validate_evidence,
)

TITLE = "월계1동, 식료품 꾸러미 전달"
BODY = "월계1동은 지역 내 홀몸어르신 20명에게 식료품 꾸러미를 전달했다."


def _notice(body: str = BODY, *, title: str = TITLE) -> NoticeInput:
    return NoticeInput.model_validate(
        {"title": title, "body_text": body, "reference_datetime": "2026-10-06T12:00:00+09:00"}
    )


def _news(**changes: Any) -> NoticeSummary:
    return NoticeSummary.model_validate(
        {
            "category": "news",
            "category_code": 27,
            "summary": TITLE,
            "publisher": None,
            "applicable_area": None,
            "audience": None,
            "audience_scope": "unknown",
            "action": None,
            "action_requirement": "none",
            "location": None,
            "dates": [],
            "status": "not_applicable",
            "status_detail": None,
            "notice_update": "new",
            "changed_details": None,
            "notes": [],
            "topics": [],
            "uncertainties": [],
            "evidence": [
                {"field": "category", "excerpt": BODY},
                {"field": "category_code", "excerpt": TITLE},
                {"field": "summary", "excerpt": TITLE},
            ],
        }
        | changes
    )


def _verify(summary: NoticeSummary, notice: NoticeInput) -> NoticeSummary:
    return preserve_uncertain_summary(summary, ground_summary(summary, notice), notice)


def test_title_exclusive_news_preserves_no_action_contract_and_can_be_published() -> None:
    notice = _notice()
    assert TITLE not in notice.body_text
    result = _verify(_news(), notice)
    assert result.summary == TITLE
    assert result.category == "news"
    assert result.category_code == 27
    assert result.action is None
    assert result.action_requirement == "none"
    assert result.status == "not_applicable"
    assert result.uncertainties == []
    assert all(item.verification == "text_matched" for item in result.evidence)
    assert not summary_requires_review(result, attachment_status="none")
    validate_evidence(result, body_text=BODY, attachment_texts=[], title=TITLE)
    # Existing callers that supply no title retain their original contract.
    with pytest.raises(SummaryValidationError):
        validate_evidence(result, body_text=BODY, attachment_texts=[])


def test_body_matched_news_preserves_explicit_no_action_code() -> None:
    notice = _notice(f"{TITLE}\n{BODY}")
    result = _verify(_news(), notice)
    assert result.action_requirement == "none"
    assert result.uncertainties == []


def test_unsupported_headline_still_requires_review_and_rejects_no_action_code() -> None:
    summary = _news(summary="월계1동, 홀몸어르신께 식료품 꾸러미 전달")
    checked = ground_summary(summary, _notice())
    assert checked.summary == REVIEW_NOTE
    assert checked.category == "unknown"
    assert checked.action_requirement == "unknown"
    result = preserve_uncertain_summary(summary, checked, _notice())
    assert result.uncertainties == [REVIEW_NOTE]
    assert summary_requires_review(result, attachment_status="none")


def test_rejected_nonnull_action_cannot_become_verified_no_action() -> None:
    summary = _news(
        action="온라인 신청",
        action_requirement="none",
        evidence=[*_news().evidence, {"field": "action", "excerpt": "온라인 신청"}],
    )
    checked = ground_summary(summary, _notice())
    assert checked.category == "news"
    assert checked.action is None
    assert checked.action_requirement == "unknown"
    assert _verify(summary, _notice()).uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("category", ["unknown", "application", "event", "living"])
def test_other_categories_do_not_gain_verified_no_action(category: str) -> None:
    checked = ground_summary(_news(category=category), _notice())
    assert checked.action_requirement == "unknown"


@pytest.mark.parametrize("field", ["summary", "category", "category_code"])
def test_metadata_title_reference_is_scoped_to_headline_and_classification(field: str) -> None:
    evidence = Evidence(field=field, excerpt=TITLE)
    assert not evidence_reference_valid(evidence, sources=[BODY])
    assert evidence_reference_valid(evidence, sources=[BODY], title=TITLE)


@pytest.mark.parametrize("field", ["audience", "action", "dates", "notes", "location"])
def test_metadata_title_cannot_validate_resident_claims(field: str) -> None:
    evidence = Evidence(field=field, excerpt=TITLE)
    assert not evidence_reference_valid(evidence, sources=[BODY], title=TITLE)


def test_title_does_not_rescue_a_quote_negated_in_the_body() -> None:
    title = "행사 개최"
    body = "행사 개최 취소 안내"
    notice = _notice(body, title=title)
    summary = _news(
        category="event",
        category_code=26,
        summary=title,
        action_requirement="unknown",
        status="unknown",
        evidence=[
            {"field": "summary", "excerpt": title},
            {"field": "category_code", "excerpt": title},
        ],
    )
    checked = ground_summary(summary, notice)
    assert checked.summary == REVIEW_NOTE
    assert checked.uncertainties == [REVIEW_NOTE]
    assert summary_requires_review(_verify(summary, notice), attachment_status="none")


def test_title_exclusive_quote_cannot_mask_a_negated_body_headline() -> None:
    title = "행사 개최 안내"
    body = "행사 개최 취소 안내"
    notice = _notice(body, title=title)
    summary = _news(
        category="event",
        category_code=26,
        summary="행사 개최",
        action_requirement="unknown",
        status="unknown",
        evidence=[
            {"field": "summary", "excerpt": title},
            {"field": "category_code", "excerpt": title},
        ],
    )
    assert title not in body
    checked = ground_summary(summary, notice)
    assert checked.summary == REVIEW_NOTE
    assert checked.uncertainties == [REVIEW_NOTE]
    assert summary_requires_review(_verify(summary, notice), attachment_status="none")


def test_title_cannot_supply_action_or_dates_to_a_news_summary() -> None:
    title = "온라인 신청 2026.10.10."
    notice = _notice(title=title)
    summary = _news(
        action="온라인 신청",
        action_requirement="optional",
        dates=[
            {
                "kind": "application",
                "label": "신청",
                "text": "2026.10.10.",
                "start_date": None,
                "end_date": "2026-10-10",
                "start_time": None,
                "end_time": None,
            }
        ],
        evidence=[
            {"field": "summary", "excerpt": BODY},
            {"field": "category_code", "excerpt": BODY},
            {"field": "action", "excerpt": "온라인 신청"},
            {"field": "dates", "excerpt": title},
        ],
        summary="월계1동은 식료품 꾸러미를 전달했다",
    )
    checked = ground_summary(summary, notice)
    assert checked.action is None
    assert checked.dates == []
    result = preserve_uncertain_summary(summary, checked, notice)
    assert result.uncertainties == [REVIEW_NOTE]
    assert all(
        item.verification is None for item in result.evidence if item.field in {"action", "dates"}
    )


def test_title_does_not_turn_empty_content_into_readable_source() -> None:
    with pytest.raises(SummaryValidationError):
        ground_summary(_news(), _notice(""))


def test_partial_attachment_and_file_only_publication_gates_still_require_review() -> None:
    text_summary = _verify(_news(), _notice())
    assert summary_requires_review(text_summary, attachment_status="partial")
    media = (MediaSource(source_id="media_1", source_type="document"),)
    file_summary = _news(
        evidence=[
            {
                "field": field,
                "excerpt": TITLE,
                "source_type": "document",
                "source_id": "media_1",
                "page": 1,
            }
            for field in ("summary", "category_code")
        ]
    )
    result = ground_summary(file_summary, _notice(""), media_sources=media)
    assert all(item.verification == "file_reference_only" for item in result.evidence)
    assert summary_requires_review(result, attachment_status="all_read")
