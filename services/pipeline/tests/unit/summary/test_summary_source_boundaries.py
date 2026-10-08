"""Separate inferred no-action policy and file metadata from actual source evidence."""

import json
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError
from support.gemini_multimodal import _media, _prepared
from support.summary_grounding_retry import FACT, NO_APPLICATION, _notice, _response

from pipeline.storage.summary_metadata import build_summary_metadata
from pipeline.storage.summary_record import build_summary_record, summary_requires_review
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.transform import summarize as summarize_module
from pipeline.transform.file_only_summary import file_reference_problems, preserve_file_only_summary
from pipeline.transform.gemini_client import GeminiRequestError
from pipeline.transform.grounding import REVIEW_NOTE, unknown_summary
from pipeline.transform.notice_input import NoticeInput, render_notice_input
from pipeline.transform.prepared_summary import PreparedSummaryResult
from pipeline.transform.summary_schema import (
    GeminiNoticeSummary,
    MediaSource,
    NoticeSummary,
    SummaryValidationError,
)


def _provider(
    monkeypatch: pytest.MonkeyPatch, *responses: dict[str, Any] | str | Exception,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def generate(**kwargs: Any) -> str:
        index = len(calls)
        calls.append(deepcopy(kwargs))
        assert index < len(responses), "source corrections must share the two-call budget"
        response = responses[index]
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    return calls


def _policy_default_news() -> tuple[NoticeInput, dict[str, Any]]:
    notice = NoticeInput.model_validate(_notice().model_dump() | {
        "body_text": _notice().body_text.replace("\n" + NO_APPLICATION, ""),
    })
    raw = _response(FACT)
    raw["evidence"] = [item for item in raw["evidence"] if item["field"] != "action_requirement"]
    raw["card_summaries"]["action"] = None
    return notice, raw


def test_news_default_none_does_not_require_a_fabricated_no_action_card(monkeypatch):
    notice, raw = _policy_default_news()
    GeminiNoticeSummary.model_validate(raw)
    calls = _provider(monkeypatch, raw)

    summary = summarize_module.summarize_notice(notice, api_key="offline-source-test")

    assert len(calls) == 1
    assert summary.action_requirement == "none"
    assert summary.action is summary.location is summary.card_summaries.action is None
    assert summary.audience is None and summary.dates == []
    assert summary.notes == raw["notes"]
    assert summary.uncertainties == []
    assert summary._correction_failure_code is None
    assert not summary_requires_review(summary, attachment_status="none")
    metadata = build_summary_metadata(
        body_text=notice.body_text, total_file_count=0, read_file_count=0,
        model="offline-source-test", prompt_version="source-boundary-test",
    )
    record = build_summary_record(
        PreparedSummaryResult(notice_id=8101, summary=summary, warnings=()), metadata,
        deadline_on=None, generated_at=datetime(2026, 10, 7, tzinfo=UTC),
    )
    assert record.status == "summarized"
    view = build_notice_summary_view(status=record.status, result=record.result,
                                     attachment_status="none")
    assert view.content.headline.text == FACT
    assert view.content.cards.action.text is None


def test_explicit_no_action_statement_still_requires_one_card_correction(monkeypatch):
    first = _response(FACT)
    first["card_summaries"]["action"] = None
    with pytest.raises(ValidationError):
        GeminiNoticeSummary.model_validate(first)
    corrected = _response(FACT)
    calls = _provider(monkeypatch, first, corrected)

    summary = summarize_module.summarize_notice(_notice(), api_key="offline-source-test")

    assert len(calls) == 2
    assert summary.card_summaries.action == corrected["card_summaries"]["action"]
    assert summary.action_requirement == "none" and summary.action is None
    assert summary.notes == first["notes"]
    assert summary.uncertainties == []
    assert not summary_requires_review(summary, attachment_status="none")


@pytest.mark.parametrize("problem", ["malformed", "timeout"])
def test_explicit_none_nullable_candidate_survives_failed_correction(monkeypatch, problem):
    first = _response(FACT)
    first["card_summaries"]["action"] = None
    NoticeSummary.model_validate(first)
    second = "{broken-json" if problem == "malformed" else GeminiRequestError(
        "mock correction timeout", reason_code="api_timeout",
    )
    calls = _provider(monkeypatch, first, second)

    summary = summarize_module.summarize_notice(_notice(), api_key="offline-source-test")

    assert len(calls) == 2
    assert summary.card_summaries.action is None
    assert summary.notes == first["notes"]
    assert summary.action_requirement == "none"
    assert REVIEW_NOTE in summary.uncertainties
    assert summary._correction_failure_code == (
        "response_validation_failed" if problem == "malformed" else "api_timeout"
    )
    assert "correction_failure_code" not in summary.model_dump_json()
    assert summary_requires_review(summary, attachment_status="none")


@pytest.mark.parametrize("missing_card", [False, True])
def test_unmatched_none_evidence_is_reviewed_rather_than_trusted(monkeypatch, missing_card):
    notice, first = _policy_default_news()
    first["evidence"].append({
        "field": "action_requirement", "excerpt": "원문에 없는 신청 불필요 안내",
    })
    if not missing_card:
        first["card_summaries"]["action"] = "별도의 신청은 필요 없어요."
    calls = _provider(monkeypatch, first, first)

    summary = summarize_module.summarize_notice(notice, api_key="offline-source-test")

    assert len(calls) <= 2
    assert summary.card_summaries.action == first["card_summaries"]["action"]
    assert summary.notes == first["notes"]
    assert summary_requires_review(summary, attachment_status="none")
    reference = next(item for item in summary.evidence if item.field == "action_requirement")
    assert reference.verification is None


def test_invalid_first_schema_never_becomes_a_nullable_no_action_fallback(monkeypatch):
    _, first = _policy_default_news()
    first["card_summaries"]["notes"] = "지원품은 식료품 꾸러미입니다."
    calls = _provider(monkeypatch, first, "{broken-json")
    with pytest.raises(SummaryValidationError):
        summarize_module.summarize_notice(_notice(), api_key="offline-source-test")
    assert len(calls) == 2


RICH_TITLE = "월계1동 65세 이상 주민 10월 10일 월계문화센터 신청"
STRUCTURED_QUOTES = {
    "audience": "월계1동 65세 이상 주민",
    "action": "월계문화센터 신청",
    "location": "월계문화센터",
    "dates": "10월 10일",
}


def _file_title_case(kind: str, *, fake_text: bool):
    prepared = _prepared("", _media(kind))
    prepared.notice = NoticeInput.model_validate(prepared.notice.model_dump() | {
        "title": RICH_TITLE, "publisher": "노원구청", "department": "문화예술과",
    })
    prepared.blocks[0] = {"type": "text", "text": render_notice_input(prepared.notice)}
    raw = unknown_summary(prepared.notice, has_media=True).model_dump(mode="json")
    raw.update(
        category="application", category_code=26, summary=RICH_TITLE,
        publisher="다른 기관", audience=STRUCTURED_QUOTES["audience"], audience_scope="specific",
        action=STRUCTURED_QUOTES["action"], action_requirement="optional", location="월계문화센터",
        dates=[{
            "kind": "event", "label": None, "text": "10월 10일",
            "start_date": "2026-10-10", "end_date": "2026-10-10",
            "start_time": None, "end_time": None,
        }], status="upcoming", notice_update="new", uncertainties=[],
        card_summaries={
            "audience": "월계1동 65세 이상 주민이 대상이에요.",
            "deadline": "일정은 2026년 10월 10일이에요.",
            "action": "월계문화센터에 신청해 주세요.", "notes": None,
        },
    )
    raw["evidence"] = [
        {"field": field, "excerpt": RICH_TITLE}
        for field in ("summary", "category", "category_code")
    ]
    raw["evidence"].extend(
        {
            "field": field, "excerpt": quote, "source_type": "text" if fake_text else kind,
            "source_id": None if fake_text else "media_1",
            "page": 1 if kind == "document" and not fake_text else None,
        }
        for field, quote in STRUCTURED_QUOTES.items()
    )
    return prepared, raw


@pytest.mark.parametrize("kind", ["document", "image"])
def test_rich_file_title_cannot_verify_audience_action_place_or_schedule(monkeypatch, kind):
    prepared, raw = _file_title_case(kind, fake_text=True)
    original_blocks = deepcopy(prepared.blocks)
    calls = _provider(monkeypatch, raw, raw)

    result = summarize_module.summarize_prepared_notice(prepared, api_key="offline-source-test")

    assert len(calls) == 2
    assert prepared.blocks == original_blocks
    assert calls[1]["notice_text"][:len(original_blocks)] == original_blocks
    for field, quote in STRUCTURED_QUOTES.items():
        reference = next(item for item in result.summary.evidence if item.field == field)
        assert reference.excerpt == quote and reference.verification is None
    for item in result.summary.evidence:
        if item.field in {"summary", "category", "category_code"}:
            assert item.verification == "text_matched"
    assert result.summary.audience == raw["audience"]
    assert result.summary.action == raw["action"]
    assert result.summary.location == raw["location"]
    assert result.summary.dates[0].end_date == raw["dates"][0]["end_date"]
    assert result.summary.card_summaries.model_dump() == raw["card_summaries"]
    assert result.summary.publisher == "노원구청"
    assert REVIEW_NOTE in result.summary.uncertainties
    metadata = build_summary_metadata(
        body_text="", total_file_count=1, read_file_count=1,
        model="offline-source-test", prompt_version="source-boundary-test",
    )
    record = build_summary_record(result, metadata, deadline_on=date(2026, 10, 10),
                                  generated_at=datetime(2026, 10, 7, tzinfo=UTC))
    assert record.status == "needs_review" and record.deadline_on is None
    assert record.result.audience == raw["audience"]


@pytest.mark.parametrize("kind", ["document", "image"])
def test_file_title_can_support_headline_and_classification_with_real_file_fact_references(
    monkeypatch, kind,
):
    prepared, raw = _file_title_case(kind, fake_text=False)
    calls = _provider(monkeypatch, raw)

    result = summarize_module.summarize_prepared_notice(prepared, api_key="offline-source-test")

    assert len(calls) == 1
    assert result.summary.uncertainties == []
    assert result.summary.publisher == "노원구청"
    for item in result.summary.evidence:
        assert item.verification == (
            "text_matched" if item.field in {"summary", "category", "category_code"}
            else "file_reference_only"
        )
    assert summary_requires_review(result.summary, attachment_status="all_read")


@pytest.mark.parametrize("metadata_field", ["publisher", "department"])
def test_publisher_and_department_metadata_never_validate_a_location_quote(metadata_field):
    prepared, raw = _file_title_case("image", fake_text=False)
    value = getattr(prepared.notice, metadata_field)
    raw["location"] = value
    raw["evidence"] = [item for item in raw["evidence"] if item["field"] != "location"]
    raw["evidence"].append({"field": "location", "excerpt": value})
    summary = NoticeSummary.model_validate(raw)
    sources = (MediaSource("media_1", "image"),)

    assert file_reference_problems(summary, prepared.notice, sources)
    kept = preserve_file_only_summary(summary, prepared.notice, sources)

    assert kept.location == value
    assert kept.publisher == prepared.notice.publisher
    assert REVIEW_NOTE in kept.uncertainties
    reference = next(item for item in kept.evidence if item.field == "location")
    assert reference.verification is None
