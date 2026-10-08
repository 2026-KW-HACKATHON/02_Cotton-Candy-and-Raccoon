"""Helpers shared from test_gemini_card_generation.py."""

from copy import deepcopy

from pipeline.transform.grounding import unknown_summary
from pipeline.transform.notice_input import NoticeInput

__all__ = [
    "CARD_TEXT",
    "_notice",
    "_response",
]


BODY = (
    "지원 사업 신청\n대상: 월계1동 주민\n"
    "신청 기간: 2026-10-03~2026-10-20\n"
    "월계1동 주민센터 방문 신청\n신분증 지참"
)


CARD_TEXT = {
    "audience": "월계1동 주민이 대상이에요.",
    "deadline": "신청 기간은 2026년 10월 3일부터 10월 20일까지예요.",
    "action": "월계1동 주민센터를 방문해 신청하세요.",
    "notes": "방문할 때 신분증을 준비하세요.",
}


def _notice() -> NoticeInput:
    return NoticeInput.model_validate({
        "title": "지원 사업 신청", "body_text": BODY,
        "reference_datetime": "2026-10-07T12:00:00+09:00",
    })


def _response() -> dict:
    data = unknown_summary(_notice()).model_dump(mode="json")
    data.update(
        category="application", category_code=27, summary="지원 사업 신청",
        audience="월계1동 주민", audience_scope="specific",
        action="월계1동 주민센터 방문 신청", action_requirement="optional",
        location="월계1동 주민센터", status="open", notice_update="new",
        dates=[{
            "kind": "application", "label": "신청 기간", "text": "2026-10-03~2026-10-20",
            "start_date": "2026-10-03", "end_date": "2026-10-20",
            "start_time": None, "end_time": None,
        }], notes=["신분증 지참"], uncertainties=[],
        evidence=[
            {"field": field, "excerpt": excerpt}
            for field, excerpt in (
                ("summary", "지원 사업 신청"), ("category", "지원 사업 신청"),
                ("category_code", "지원 사업 신청"), ("audience", "월계1동 주민"),
                ("action", "월계1동 주민센터 방문 신청"),
                ("location", "월계1동 주민센터 방문 신청"),
                ("dates", "신청 기간: 2026-10-03~2026-10-20"), ("notes", "신분증 지참"),
            )
        ], card_summaries=deepcopy(CARD_TEXT),
    )
    return data
