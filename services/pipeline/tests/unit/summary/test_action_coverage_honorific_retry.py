"""Explicit honorific no-signup text must not downgrade a verified first response."""

import pytest
from support.summary_grounding_retry import FACT, NO_APPLICATION, _generate, _notice, _response

from pipeline.storage.summary_record import summary_requires_review
from pipeline.transform import summarize as summarize_module
from pipeline.transform.action_coverage import find_missing_action_conditions


@pytest.mark.parametrize(
    "source",
    [
        "사전 신청하실 필요가 없습니다.",
        "사전 접수하실 필요는 없습니다.",
        "사전 신청하셔도 되지 않습니다.",
        "현장 접수하실 수 없습니다.",
    ],
)
def test_verified_no_application_news_stays_single_call_without_review(monkeypatch, source):
    notice = _notice().model_copy(update={"body_text": _notice().body_text + "\n" + source})
    first = _response(FACT)
    assert first["action_requirement"] == "none"
    assert any(
        item["field"] == "action_requirement" and item["excerpt"] == NO_APPLICATION
        for item in first["evidence"]
    )
    calls = _generate(monkeypatch, [first])

    summary = summarize_module.summarize_notice(notice, api_key="offline-test")

    assert len(calls) == 1
    assert summary.action is None
    assert summary.action_requirement == "none"
    assert summary.card_summaries.action == "별도의 신청은 필요 없어요."
    assert summary.uncertainties == []
    assert summary._correction_failure_code is None
    assert not find_missing_action_conditions(summary, notice)
    assert not summary_requires_review(summary, attachment_status="none")
