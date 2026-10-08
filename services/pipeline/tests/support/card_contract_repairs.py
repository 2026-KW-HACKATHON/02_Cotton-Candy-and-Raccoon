"""Helpers shared from test_card_contract_repairs.py."""

from typing import Any

from pipeline.transform.notice_input import NoticeInput
from support.gemini_card_generation import _notice, _response

__all__ = [
    "_drop_references",
    "_known_case",
]


def _drop_references(data: dict[str, Any], *fields: str) -> None:
    data["evidence"] = [item for item in data["evidence"] if item["field"] not in fields]


def _known_case(case: str) -> tuple[NoticeInput, dict[str, Any], str, str]:
    """Every added fact has a literal source; none is inferred from a missing slot."""
    notice = _notice()
    data = _response()
    body = notice.body_text
    if case in {"location", "explicit-none"}:
        data.update(action=None, action_requirement="unknown", location=None)
        _drop_references(data, "action", "action_requirement", "location")
        body = body.replace("\n월계1동 주민센터 방문 신청", "")
        slot = "action"
        if case == "location":
            fact, quote = "월계1동 주민센터", "장소: 월계1동 주민센터"
            data["location"] = fact
            data["evidence"].append({"field": "location", "excerpt": quote})
            prose = "장소는 월계1동 주민센터예요."
        else:
            quote = "별도의 신청은 필요 없습니다."
            data["action_requirement"] = "none"
            data["evidence"].append({"field": "action_requirement", "excerpt": quote})
            prose = "별도의 신청은 필요 없어요."
    else:
        data["notes"] = []
        _drop_references(data, "notes")
        body = body.replace("\n신분증 지참", "")
        slot = "notes"
        if case == "changed-details":
            quote = "행사 장소가 월계1동 주민센터로 변경"
            data.update(notice_update="modified", changed_details=quote)
            data["evidence"].extend([
                {"field": "changed_details", "excerpt": quote},
                {"field": "notice_update", "excerpt": quote},
            ])
            prose = "행사 장소가 월계1동 주민센터로 변경됐어요."
        elif case == "status-detail":
            quote = "주차권은 발급되지 않습니다."
            data["status_detail"] = quote
            data["evidence"].append({"field": "status_detail", "excerpt": quote})
            prose = "주차권은 발급되지 않아요."
        elif case == "cancelled":
            quote = "행사가 취소되었습니다."
            data["status"] = "cancelled"
            data["evidence"].append({"field": "status", "excerpt": quote})
            prose = "행사가 취소됐어요."
        elif case == "update-only":
            quote = "행사 일정이 변경되었습니다."
            data["notice_update"] = "modified"
            data["evidence"].append({"field": "notice_update", "excerpt": quote})
            prose = "행사 일정이 변경됐어요."
        else:
            raise AssertionError(f"unsupported test case: {case}")
    notice = NoticeInput.model_validate(notice.model_dump() | {"body_text": body + "\n" + quote})
    data["card_summaries"][slot] = None
    return notice, data, slot, prose
