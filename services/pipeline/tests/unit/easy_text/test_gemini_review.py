"""Semantic review, bounded correction and unavailable-review fallbacks."""
import json

import pytest
from support.easy_rewrite import rewrite, rewrite_response, sentence

from pipeline.glossary.easy_language import EasyLanguageResult, simplify_notice
from pipeline.glossary.source import NoticeGlossaryInput

BODY = "다른 가족이 돌봄을 수행하기 어려운 경우 예외 서류를 내세요. 비용은 10,000원입니다."
KEY = "test-review-secret"


def payload(text):
    return rewrite(BODY, sections=[{
        "heading": "무엇을 하나요?", "style": "paragraph",
        "sentences": [sentence(text, BODY)],
    }])


def run(review_request, text="비용은 1만 원이에요."):
    return simplify_notice(
        NoticeGlossaryInput(text=BODY), api_key=KEY,
        request=lambda **kwargs: rewrite_response(BODY, rewrite_payload=payload(text)),
        review_request=review_request,
    )


def test_equivalent_amount_pass_keeps_generation_and_roundtrips():
    calls = []
    def review(**kwargs):
        calls.append(kwargs)
        return '{"issues": [], "corrected_rewrite": null}'
    result = run(review)
    assert len(calls) == 1 and calls[0]["review"] is True
    sent = json.loads(calls[0]["notice_text"])
    assert sent["source"] == BODY
    assert "1만 원" in result.easy_text
    assert result.easy_result.review.status == "passed"
    assert EasyLanguageResult.model_validate_json(result.model_dump_json()) == result


def test_semantic_problem_is_corrected_once():
    calls = []
    def review(**kwargs):
        calls.append(kwargs)
        return json.dumps({
            "issues": ["돌봄 수행 불가가 방해로 바뀜"],
            "corrected_rewrite": payload("다른 가족이 돌볼 수 없으면 예외 서류를 내세요."),
        })
    result = run(review, "다른 가족이 돌봄을 어렵게 하면 서류를 내세요.")
    assert len(calls) == 1
    assert "돌볼 수 없으면" in result.easy_text
    assert result.easy_result.review.status == "corrected"
    assert result.attempt_count == 1


@pytest.mark.parametrize("output", [
    "", "{", "{}", '{"issues": ["오류"], "corrected_rewrite": null}',
    json.dumps({"issues": [], "corrected_rewrite": payload("바뀐 문장")}),
    json.dumps({"issues": ["오류"], "corrected_rewrite": payload("")}),
    json.dumps({"issues": [KEY], "corrected_rewrite": payload(KEY)}),
])
def test_unusable_review_preserves_generation_with_incomplete_state(output):
    result = run(lambda **kwargs: output)
    assert result.easy_result.review.status == "incomplete"
    assert "1만 원" in result.easy_text and KEY not in result.model_dump_json()


def test_review_api_failure_preserves_result_without_private_error():
    def fail(**kwargs):
        raise RuntimeError(KEY)
    result = run(fail)
    assert result.easy_result.review.status == "incomplete"
    assert KEY not in result.model_dump_json()


def test_review_timeout_preserves_completed_generation(monkeypatch):
    from pipeline import gemini_execution
    now = [0.0]
    monkeypatch.setattr(gemini_execution.time, "monotonic", lambda: now[0])
    def timeout(**kwargs):
        now[0] = 121.0
        gemini_execution.current_execution().check()
    result = run(timeout)
    assert result.easy_result.review.status == "incomplete"
    assert "1만 원" in result.easy_text
