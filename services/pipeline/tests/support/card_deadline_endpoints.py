"""Helpers shared from test_card_deadline_endpoints.py."""

from pipeline.transform.grounding import unknown_summary
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_schema import DateEntry

__all__ = [
    "_dates",
    "_input_response",
]


def _dates():
    return [
        DateEntry(
            kind="application", label="신청기간", text="2026-10-03~2026-10-20",
            start_date="2026-10-03", end_date="2026-10-20", start_time=None, end_time=None,
        ),
        DateEntry(
            kind="event", label="행사기간", text="2026-11-01~2026-11-03",
            start_date="2026-11-01", end_date="2026-11-03", start_time=None, end_time=None,
        ),
    ]


def _input_response():
    title = "행사 참가 신청 안내"
    audience = "노원구민"
    action = "온라인으로 신청"
    note = "참가비 무료"
    dates = _dates()
    quotes = ["신청기간: " + dates[0].text, "행사기간: " + dates[1].text]
    notice = NoticeInput.model_validate({
        "title": title,
        "body_text": "\n".join([title, audience, action, note, *quotes]),
        "reference_datetime": "2026-10-07T12:00:00+09:00",
    })
    response = unknown_summary(notice).model_dump(mode="json")
    response.update(
        category="mixed", category_code=26, summary=title, audience=audience,
        audience_scope="specific", action=action, action_requirement="optional",
        status="open", notice_update="new", notes=[note], uncertainties=[],
        dates=[entry.model_dump(mode="json") for entry in dates],
        evidence=[
            {"field": field, "excerpt": excerpt}
            for field, excerpt in [
                ("summary", title), ("category", title), ("category_code", title),
                ("audience", audience), ("action", action), ("notes", note),
                *(("dates", quote) for quote in quotes),
            ]
        ],
        card_summaries={
            "audience": "노원구민이 대상이에요.",
            "deadline": "신청은 10월 20일까지이고 행사는 11월 1일에 시작해요.",
            "action": "희망하시면 온라인으로 신청해 주세요.",
            "notes": "참가비는 무료예요.",
        },
    )
    return notice, response
