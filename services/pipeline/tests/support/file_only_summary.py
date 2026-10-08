"""Helpers shared from test_file_only_summary.py."""

from typing import Any

from pipeline.transform.grounding import unknown_summary
from support.gemini_multimodal import _prepared

__all__ = [
    "_file_output",
]


def _file_output(kind: str = "image") -> dict[str, Any]:
    """Use valid shape with paraphrases that literal content grounding would drop."""
    data = unknown_summary(_prepared().notice, has_media=True).model_dump()
    data.update(
        category="mixed",
        category_code=26,
        summary="주민 문화 프로그램 안내",
        publisher="노원구청",
        applicable_area="월계1동",
        audience="지역 주민",
        audience_scope="general",
        action="온라인 예약 후 방문",
        action_requirement="required",
        location="월계문화센터",
        dates=[
            {
                "kind": "event",
                "label": "문화 프로그램",
                "text": "10월 10일 오전 10시부터 11시",
                "start_date": "2026-10-10",
                "end_date": "2026-10-10",
                "start_time": "10:00",
                "end_time": "11:00",
            }
        ],
        status="upcoming",
        status_detail="프로그램별 예약 안내 확인",
        notice_update="new",
        notes=["일부 프로그램은 사전 예약이 필요합니다", "참가비는 프로그램별로 다릅니다"],
        topics=[
            {"title": "공예 교실", "category": "event", "summary": "주민이 함께하는 공예 체험"},
            {"title": "음악 교실", "category": "event", "summary": "가족과 함께하는 음악 체험"},
        ],
        uncertainties=[],
        card_summaries={
            "audience": "지역 주민이 대상이에요.",
            "deadline": "문화 프로그램은 2026년 10월 10일 10:00부터 11:00까지예요.",
            "action": "온라인으로 예약한 뒤 월계문화센터를 방문해 주세요.",
            "notes": "일부 프로그램은 사전 예약이 필요해요. 참가비는 프로그램별로 달라요.",
        },
        evidence=[
            {
                "field": field,
                "excerpt": excerpt,
                "source_type": kind,
                "source_id": "media_1",
                "page": 1 if kind == "document" else None,
            }
            for field, excerpt in (
                ("summary", "문화교실 일정표"),
                ("publisher", "노원구청"),
                ("applicable_area", "월계1동"),
                ("audience", "주민 누구나"),
                ("action", "온라인 사전 신청"),
                ("action_requirement", "예약 필수"),
                ("location", "월계문화센터"),
                ("dates", "10.10 10:00-11:00"),
                ("notes", "예약제 / 프로그램별 비용"),
                ("topics", "공예 / 음악 프로그램"),
                ("category_code", "문화교실 일정표"),
            )
        ],
    )
    return data
