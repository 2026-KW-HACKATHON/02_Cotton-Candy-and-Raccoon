"""Correct a simple news headline without bypassing evidence or retry limits."""

from copy import deepcopy

import pytest
from support.summary_grounding_retry import FACT, NOTE, _generate, _notice, _response

from pipeline.storage.summary_record import summary_requires_review
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.transform import summarize as summarize_module


def test_source_backed_news_headline_correction_can_be_published(monkeypatch) -> None:
    calls = _generate(monkeypatch, [_response(), _response(FACT)])

    result = summarize_module.summarize_notice(_notice(), api_key="test")

    assert len(calls) == 2
    assert "summary/category/category_code:" in calls[1]["notice_text"]
    assert result.summary == FACT
    assert result.uncertainties == []
    assert not summary_requires_review(result, attachment_status="none")
    view = build_notice_summary_view(
        status="summarized", result=result, attachment_status="none"
    )
    assert view.status == "summarized"
    assert view.content is not None
    assert view.content.headline.value == FACT


def test_headline_retry_preserves_first_verified_facts_and_quotes(monkeypatch) -> None:
    first = _response()
    snapshot = deepcopy(first)
    retry = _response(FACT)
    retry.update(
        category_code=30,
        publisher="다른 기관",
        audience="모든 주민",
        audience_scope="general",
        action="방문 신청",
        action_requirement="required",
        status="open",
        notes=[],
        card_summaries={
            "audience": "모든 주민이 대상이에요.", "deadline": None,
            "action": "방문 신청해 주세요.", "notes": None,
        },
    )
    calls = _generate(monkeypatch, [first, retry])

    result = summarize_module.summarize_notice(_notice(), api_key="test")

    assert len(calls) == 2
    assert result.summary == FACT
    assert result.category == "news"
    assert result.category_code == 27
    assert result.publisher == "월계1동"
    assert result.audience is None
    assert result.action is None
    assert result.action_requirement == "none"
    assert result.status == "not_applicable"
    assert result.notes == [NOTE]
    assert any(item.field == "notes" and item.excerpt == NOTE for item in result.evidence)
    assert result.uncertainties == []
    assert first == snapshot


def test_unresolved_headline_stays_review_without_a_third_request(monkeypatch) -> None:
    calls = _generate(monkeypatch, [_response(), _response()])

    result = summarize_module.summarize_notice(_notice(), api_key="test")

    assert len(calls) == 2
    assert result.uncertainties == ["원문 확인 필요"]
    assert summary_requires_review(result, attachment_status="none")


@pytest.mark.parametrize("problem", ["explicit-warning", "unmatched-evidence"])
def test_other_uncertainties_do_not_trigger_headline_only_retry(monkeypatch, problem) -> None:
    first = _response()
    if problem == "explicit-warning":
        first["uncertainties"] = ["원문 확인 필요"]
    else:
        first["evidence"][-1]["excerpt"] = "원문에 없는 유의사항 근거"
    calls = _generate(monkeypatch, [first])

    result = summarize_module.summarize_notice(_notice(), api_key="test")

    assert len(calls) == 1
    assert summary_requires_review(result, attachment_status="none")


def test_schema_retry_and_headline_retry_share_two_request_budget(monkeypatch) -> None:
    first = _response()
    first["summary"] = "가" * 41
    calls = _generate(monkeypatch, [first, _response()])

    result = summarize_module.summarize_notice(_notice(), api_key="test")

    assert len(calls) == 2
    assert summary_requires_review(result, attachment_status="none")


def test_notes_retry_and_headline_retry_share_two_request_budget(monkeypatch) -> None:
    notice = _notice().model_copy(update={"body_text": _notice().body_text + "\n참가비 : 30,000원"})
    first = _response()
    retry = _response()
    retry["notes"].append("참가비 : 30,000원")
    retry["evidence"].append({"field": "notes", "excerpt": "참가비 : 30,000원"})
    calls = _generate(monkeypatch, [first, retry])

    result = summarize_module.summarize_notice(notice, api_key="test")

    assert len(calls) == 2
    assert "참가비 : 30,000원" in result.notes
    assert summary_requires_review(result, attachment_status="none")


def test_malformed_headline_retry_preserves_first_summary_for_review(monkeypatch) -> None:
    first = _response()
    calls = _generate(monkeypatch, [first, "{broken-json"])
    result = summarize_module.summarize_notice(_notice(), api_key="test")

    assert result.summary == first["summary"]
    assert result.notes == first["notes"]
    assert result.card_summaries.model_dump() == first["card_summaries"]
    assert result.uncertainties == ["원문 확인 필요"]
    assert summary_requires_review(result, attachment_status="none")
    assert len(calls) == 2


def test_explicit_warning_on_corrected_headline_is_not_cleared(monkeypatch) -> None:
    retry = _response(FACT)
    retry["uncertainties"] = ["원문 확인 필요"]
    calls = _generate(monkeypatch, [_response(), retry])

    result = summarize_module.summarize_notice(_notice(), api_key="test")

    assert len(calls) == 2
    assert result.summary == FACT
    assert summary_requires_review(result, attachment_status="none")


def test_unmatched_corrected_quote_remains_review(monkeypatch) -> None:
    retry = _response(FACT)
    retry["evidence"][2]["excerpt"] = "원문에 없는 문장"
    calls = _generate(monkeypatch, [_response(), retry])

    result = summarize_module.summarize_notice(_notice(), api_key="test")

    assert len(calls) == 2
    assert summary_requires_review(result, attachment_status="none")


def test_paraphrased_classification_quote_retries_and_can_be_published(monkeypatch) -> None:
    first = _response(FACT)
    first["evidence"][1]["excerpt"] = "월계1동, 홀몸어르신 식료품 지원"
    calls = _generate(monkeypatch, [first, _response(FACT)])

    result = summarize_module.summarize_notice(_notice(), api_key="test")

    assert len(calls) == 2
    assert result.category_code == 27
    assert result.summary == FACT
    assert result.notes == [NOTE]
    assert result.uncertainties == []
    assert not summary_requires_review(result, attachment_status="none")


def test_title_exclusive_quotes_can_be_corrected_without_using_body_only_checks(
    monkeypatch,
) -> None:
    first = _response()
    for item in first["evidence"]:
        if item["field"] in {"summary", "category_code"}:
            item["excerpt"] = _notice().title
    retry = deepcopy(first)
    retry["summary"] = _notice().title
    calls = _generate(monkeypatch, [first, retry])

    result = summarize_module.summarize_notice(_notice(), api_key="test")

    assert len(calls) == 2
    assert result.summary == _notice().title
    assert result.uncertainties == []
    assert not summary_requires_review(result, attachment_status="none")


def test_quote_correction_cannot_change_an_unverified_classification_value(monkeypatch) -> None:
    first = _response(FACT)
    first["evidence"][1]["excerpt"] = "원문에 없는 분야 근거"
    retry = _response(FACT)
    retry["category_code"] = 30
    calls = _generate(monkeypatch, [first, retry])

    result = summarize_module.summarize_notice(_notice(), api_key="test")

    assert len(calls) == 2
    assert result.category_code == 27
    assert summary_requires_review(result, attachment_status="none")
