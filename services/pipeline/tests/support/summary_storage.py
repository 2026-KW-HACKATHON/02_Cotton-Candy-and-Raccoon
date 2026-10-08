"""Helpers shared from test_summary_storage.py."""

from datetime import date, datetime, timedelta, timezone
from typing import Any

from pipeline.storage.summary_record import SummaryMetadata, SummaryRecord
from pipeline.transform.summary_schema import NoticeSummary

__all__ = [
    "GENERATED_AT",
    "_metadata",
    "_record",
    "_summary",
]


GENERATED_AT = datetime(2026, 10, 3, 14, 30, tzinfo=timezone(timedelta(hours=9)))


def _metadata(**changes: Any) -> SummaryMetadata:
    values = {
        "source_hash": "ab" * 32,
        "model": "gemini-3.5-flash-lite",
        "prompt_version": "notice-summary-v3",
        "attachment_status": "all_read",
    }
    return SummaryMetadata(**(values | changes))


def _summary(**changes: Any) -> NoticeSummary:
    values = {
        "category": "application",
        "category_code": 27,
        "summary": "지원 사업 신청",
        "publisher": "노원구청",
        "applicable_area": "월계1동",
        "audience": "월계1동 주민",
        "audience_scope": "specific",
        "action": "주민센터 방문 신청",
        "action_requirement": "optional",
        "location": "월계1동 주민센터",
        "dates": [
            {
                "kind": "application",
                "label": "신청 기간",
                "text": "2026-10-03~2026-10-20",
                "start_date": "2026-10-03",
                "end_date": "2026-10-20",
                "start_time": "09:00",
                "end_time": "18:00",
            }
        ],
        "status": "open",
        "status_detail": "신청 접수 중",
        "notice_update": "new",
        "changed_details": None,
        "notes": ["신분증 지참"],
        "topics": [{"title": "지원 사업", "category": "application", "summary": "지원 사업 신청"}],
        "uncertainties": [],
        "evidence": [
            {
                "field": "summary",
                "excerpt": "지원 사업 신청",
                "source_type": "text",
                "source_id": None,
                "page": None,
                "verification": "text_matched",
            },
            {
                "field": "dates",
                "excerpt": "신청 기간: 2026-10-03~2026-10-20 오전 9시~오후 6시",
                "source_type": "text",
                "source_id": None,
                "page": None,
                "verification": "text_matched",
            },
            {
                "field": "notes",
                "excerpt": "신분증 지참",
                "source_type": "text",
                "source_id": None,
                "page": None,
                "verification": "text_matched",
            },
        ],
    }
    values["evidence"].extend(
        {
            "field": field,
            "excerpt": excerpt,
            "source_type": "text",
            "source_id": None,
            "page": None,
            "verification": "text_matched",
        }
        for field, excerpt in (
            ("category_code", "지원 사업 신청"),
            ("applicable_area", "월계1동"),
            ("audience", "월계1동 주민"),
            ("action", "주민센터 방문 신청"),
            ("location", "월계1동 주민센터"),
            ("topics", "지원 사업 신청"),
        )
    )
    return NoticeSummary.model_validate(values | changes)


def _record(status: str = "summarized", **changes: Any) -> SummaryRecord:
    values: dict[str, Any] = {"notice_id": 42, "status": status, "metadata": _metadata()}
    if status in ("summarized", "needs_review"):
        values["generated_at"] = GENERATED_AT
    if status == "summarized":
        values["result"] = _summary()
        values["deadline_on"] = date(2026, 10, 20)
    if status == "failed":
        values["last_error_code"] = "api_timeout"
    return SummaryRecord(**(values | changes))
