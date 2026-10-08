"""Helpers shared from test_summary_grounding_retry.py."""

import json

import pytest

from pipeline.transform import summarize as summarize_module
from pipeline.transform.grounding import unknown_summary
from pipeline.transform.notice_input import NoticeInput

__all__ = [
    "FACT",
    "NO_APPLICATION",
    "_generate",
    "_notice",
    "_response",
]


FACT = "월계1동은 식료품 꾸러미를 홀몸어르신에게 전달했다"


NOTE = "지원품 : 식료품 꾸러미"


NO_APPLICATION = "별도의 신청은 필요 없습니다."


UNSUPPORTED = "월계1동, 어르신께 식료품 꾸러미 전달"


def _notice() -> NoticeInput:
    return NoticeInput.model_validate(
        {
            "title": "식료품 꾸러미 지원 소식",
            "body_text": FACT + ".\n" + NOTE + "\n" + NO_APPLICATION,
            "reference_datetime": "2026-10-06T12:00:00+09:00",
            "publisher": "월계1동",
        }
    )


def _response(headline: str = UNSUPPORTED) -> dict:
    data = unknown_summary(_notice()).model_dump(mode="json")
    data.update(
        category="news",
        category_code=27,
        summary=headline,
        action_requirement="none",
        status="not_applicable",
        notice_update="new",
        notes=[NOTE],
        uncertainties=[],
        card_summaries={
            "audience": None, "deadline": None, "action": "별도의 신청은 필요 없어요.",
            "notes": "지원품은 식료품 꾸러미예요.",
        },
        evidence=[
            {"field": "category", "excerpt": FACT},
            {"field": "category_code", "excerpt": FACT},
            {"field": "summary", "excerpt": FACT},
            {"field": "action_requirement", "excerpt": NO_APPLICATION},
            {"field": "notes", "excerpt": NOTE},
        ],
    )
    return data


def _generate(monkeypatch: pytest.MonkeyPatch, responses: list[dict | str]) -> list:
    calls = []
    iterator = iter(responses)

    def generate(**kwargs):
        calls.append(kwargs)
        response = next(iterator)
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    return calls
