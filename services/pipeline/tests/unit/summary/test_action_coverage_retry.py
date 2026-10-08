"""Preserve actual program signup details within the shared correction budget."""

import json
from copy import deepcopy

import pytest

from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.transform import summarize as summarize_module
from pipeline.transform.action_coverage import find_missing_action_conditions
from pipeline.transform.gemini_client import GeminiRequestError
from pipeline.transform.grounding import unknown_summary
from pipeline.transform.notice_input import NoticeInput

# Official Nowon notice posted 2026-10-06:
# https://www.nowon.kr/www/user/bbs/BD_selectBbs.do?q_bbsCode=1001
# &q_estnColumn1=11&q_bbscttSn=20261006135926543
BODY = (
    "2026년 마.들.장(마을에서 만나는 나들이 장터)\n"
    "‘노원 모두의정원 가을 소풍’\n"
    "◼일시 2026년 10월 24일(토) 11:00 ~ 14:00\n"
    "◼장소 노원에코센터 (숲놀이터, 모두의정원)\n"
    "◼내용 정원.환경 제험부스, 농부장터, 공연, 에코 이벤트외\n"
    "◼마들장 행사 및 체험부스 문의 : 02-3392-4911(노원에코센터)\n"
    "◼가을 상상데이 사전 접수 문의 : 02-931-1104 (마들상상놀이터)\n"
    "*모든 체험은 무료입니다\n"
    "*우천시에도 정상 진행, 행사장내 주차불가"
)
SIGNUP = "가을 상상데이 사전 접수 문의"
ACTION_CARD = "가을 상상데이 사전 접수는 02-931-1104로 문의해 주세요."


def _notice():
    return NoticeInput.model_validate({
        "title": "노원 모두의정원 가을 소풍", "body_text": BODY,
        "reference_datetime": "2026-10-07T12:00:00+09:00",
    })


def _response(*, missing_card=False):
    data = unknown_summary(_notice()).model_dump(mode="json")
    data.update(
        category="event", category_code=26, summary="노원 모두의정원 가을 소풍",
        action=None, action_requirement="unknown", location="노원에코센터",
        status="upcoming", notice_update="new", uncertainties=[],
        dates=[{
            "kind": "event", "label": "행사일시", "text": None,
            "start_date": "2026-10-24", "end_date": "2026-10-24",
            "start_time": "11:00", "end_time": "14:00",
        }],
        notes=["모든 체험은 무료입니다", "우천시에도 정상 진행, 행사장내 주차불가"],
        evidence=[
            {"field": field, "excerpt": quote}
            for field, quote in (
                ("summary", "노원 모두의정원 가을 소풍"),
                ("category_code", "노원 모두의정원 가을 소풍"),
                ("location", "노원에코센터"),
                ("dates", "2026년 10월 24일(토) 11:00 ~ 14:00"),
                ("notes", "모든 체험은 무료입니다"),
                ("notes", "우천시에도 정상 진행, 행사장내 주차불가"),
            )
        ],
        card_summaries={
            "audience": None,
            "deadline": "행사는 2026년 10월 24일 11:00부터 14:00까지예요.",
            "action": None if missing_card else "장소는 노원에코센터예요.",
            "notes": "체험은 무료예요. 비가 와도 진행하며 행사장에 주차할 수 없어요.",
        },
    )
    return data


def _corrected(first):
    retry = deepcopy(first)
    retry.update(action=SIGNUP, action_requirement="unknown")
    retry["card_summaries"]["action"] = ACTION_CARD
    retry["evidence"].append({"field": "action", "excerpt": SIGNUP})
    return retry


def _provider(monkeypatch, responses):
    calls = []
    iterator = iter(responses)

    def generate(**kwargs):
        calls.append(kwargs)
        result = next(iterator)
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    return calls


@pytest.mark.parametrize("missing_card", [False, True])
def test_actual_event_preregistration_and_card_shape_share_one_correction(
    monkeypatch, missing_card,
):
    first = _response(missing_card=missing_card)
    retry = _corrected(first)
    # An unrelated change on the second response must not replace first facts.
    retry["location"] = "다른 장소"
    calls = _provider(monkeypatch, [first, retry])
    summary = summarize_module.summarize_notice(_notice(), api_key="offline-test")
    assert len(calls) == 2
    assert SIGNUP in calls[1]["notice_text"]
    assert "행사 전체의 필수 신청" in calls[1]["notice_text"]
    assert summary.action == SIGNUP
    assert summary.action_requirement == "unknown"
    assert summary.location == "노원에코센터"
    assert summary.dates[0].end_date == "2026-10-24"
    assert summary.card_summaries.action == ACTION_CARD
    assert not find_missing_action_conditions(summary, _notice())


@pytest.mark.parametrize("failure", [
    "invalid-json", "repeat-omission", "timeout",
])
def test_signup_correction_failure_keeps_first_received_fields_and_cards(monkeypatch, failure):
    first = _response()
    second = {
        "invalid-json": "{broken",
        "repeat-omission": first,
        "timeout": GeminiRequestError("mock timeout", reason_code="gemini_timeout"),
    }[failure]
    calls = _provider(monkeypatch, [first, second])
    summary = summarize_module.summarize_notice(_notice(), api_key="offline-test")
    assert len(calls) == 2
    assert summary.location == first["location"]
    assert summary.action is None
    assert summary.card_summaries.model_dump() == first["card_summaries"]
    assert summary.uncertainties
    assert summary._correction_failure_code is not None
    view = build_notice_summary_view(
        status="needs_review", result=summary, attachment_status="none"
    )
    assert view.content.headline.text == first["summary"]
    assert view.content.cards.action.text == first["card_summaries"]["action"]


def test_repaired_existing_action_remains_intact_when_extra_condition_is_kept_in_notes(monkeypatch):
    notice = _notice().model_copy(update={"body_text": BODY + "\n텀블러를 지참해 주세요"})
    first = _response()
    first.update(action="텀블러를 지참해 주세요", action_requirement="recommended")
    first["evidence"].append({"field": "action", "excerpt": "텀블러를 지참해 주세요"})
    first["card_summaries"]["action"] = "텀블러를 지참해 주세요."
    retry = deepcopy(first)
    retry["notes"].append(SIGNUP)
    retry["evidence"].append({"field": "notes", "excerpt": SIGNUP})
    retry["card_summaries"]["notes"] += " 가을 상상데이 사전 접수는 문의해 주세요."
    calls = _provider(monkeypatch, [first, retry])
    summary = summarize_module.summarize_notice(notice, api_key="offline-test")
    assert len(calls) == 2
    assert summary.action == first["action"]
    assert SIGNUP in summary.notes
    assert not find_missing_action_conditions(summary, notice)


def test_contact_only_notice_does_not_infer_a_signup_or_force_an_extra_call(monkeypatch):
    notice = _notice().model_copy(update={"body_text": BODY.replace("사전 접수 ", "")})
    first = _response()
    calls = _provider(monkeypatch, [first])
    summary = summarize_module.summarize_notice(notice, api_key="offline-test")
    assert len(calls) == 1
    assert summary.action is None
    assert summary.action_requirement == "unknown"
