"""Preserve previously captured real notices without modifying historical JSON."""

import hashlib
import json

import pytest
from support.paths import FIXTURES_DIR

from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.transform.grounding import ground_summary
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_cards import build_summary_cards
from pipeline.transform.summary_schema import MediaSource, NoticeSummary

FIXTURE_PATH = FIXTURES_DIR / "real_notice_grounding.json"


@pytest.mark.parametrize("case_name", ["camp_text", "gifted_text", "camp_pdf"])
def test_real_capture_keeps_all_schedules_notes_and_evidence_with_review_guidance(
    case_name: str,
) -> None:
    case = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"][case_name]
    capture = case["responses"][-1]
    original = capture["raw"]
    assert hashlib.sha256(original.encode("utf-8")).hexdigest() == capture["sha256"]
    values = json.loads(original)
    # Old captures predate category_code. The adapter supplies no inferred field.
    values["category_code"] = None
    summary = NoticeSummary.model_validate(values)
    before = summary.model_dump(mode="json")
    cards = build_summary_cards(summary)

    assert cards.headline.text == summary.summary
    assert summary.card_summaries is None
    for key in ("audience", "deadline", "action", "notes"):
        assert getattr(cards.cards, key).text is None
    assert [item.value.model_dump(mode="json") for item in cards.cards.deadline.items] == values[
        "dates"
    ]
    assert [item.source_path for item in cards.cards.deadline.items] == [
        f"dates[{index}]" for index in range(len(values["dates"]))
    ]
    assert [
        item.value for item in cards.cards.notes.items if item.source_path.startswith("notes[")
    ] == values["notes"]
    assert [link.evidence.model_dump(mode="json") for link in cards.metadata.evidence] == before[
        "evidence"
    ]
    assert summary.model_dump(mode="json") == before
    if case_name == "camp_text":
        assert {entry.kind for entry in summary.dates} == {"application", "event", "result"}
        assert "30,000원" in " ".join(summary.notes)
    elif case_name == "gifted_text":
        assert len(cards.cards.deadline.items) == 5
        assert [entry.label for entry in summary.dates] == [
            "원서접수",
            "1차전형",
            "1차 합격자 발표",
            "2차전형",
            "최종합격자 발표",
        ]

    # Historical outputs have no category code. The view preserves their source
    # facts and headline, returning review guidance separately in message.
    view = build_notice_summary_view(
        status="summarized", result=summary, attachment_status="all_read"
    )
    assert view.status == "needs_review"
    assert view.message == "원문 확인 요함"
    assert view.content is not None
    assert view.content.headline.value == summary.summary
    assert view.content.headline.text == summary.summary
    assert view.content.cards.model_dump() == cards.cards.model_dump()
    assert view.content.metadata.model_dump() == cards.metadata.model_dump()
    assert summary.model_dump(mode="json") == before
    assert hashlib.sha256(original.encode("utf-8")).hexdigest() == capture["sha256"]


def test_added_ai_card_text_preserves_historical_capture_items_and_review_view() -> None:
    case = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]["camp_text"]
    capture = case["responses"][-1]
    original = capture["raw"]
    values = json.loads(original) | {"category_code": None}
    legacy_cards = build_summary_cards(NoticeSummary.model_validate(values))
    # Supply new display strings alongside the capture; never rewrite its raw JSON.
    card_text = {
        "audience": "노원구에 거주하는 2026학년도 초등학교 4~6학년 학생이 대상이에요.",
        "deadline": (
            "신청 기간은 2026년 9월 14일 09:00부터 10월 2일 18:00까지예요. 이미 마감됐어요. "
            "결과 발표는 10월 14일 15:00예요. 캠프는 10월 29~31일이에요."
        ),
        "action": (
            "노원구청 홈페이지에서 인터넷으로 접수해 주세요. "
            "캠프 장소는 강원도 고성군 일원이에요."
        ),
        "notes": "참가비는 30,000원이에요. 사회적배려대상 본인부담금은 전액 노원구가 지원해요.",
    }
    summary = NoticeSummary.model_validate(values | {"card_summaries": card_text})
    before = summary.model_dump(mode="json")
    view = build_notice_summary_view(
        status="needs_review", result=summary, attachment_status="all_read"
    )

    assert view.status == "needs_review"
    assert view.message == "원문 확인 요함"
    assert view.content is not None
    assert view.content.headline.value == summary.summary
    assert view.content.headline.text == summary.summary
    for key, text in card_text.items():
        card = getattr(view.content.cards, key)
        assert card.text == text
        assert card.model_dump(exclude={"text"}) == getattr(legacy_cards.cards, key).model_dump(
            exclude={"text"}
        )
    assert view.content.metadata.model_dump() == legacy_cards.metadata.model_dump()
    assert summary.model_dump(mode="json") == before
    assert hashlib.sha256(original.encode("utf-8")).hexdigest() == capture["sha256"]


def test_actual_pdf_file_references_remain_visible_with_review_guidance_after_grounding() -> None:
    case = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]["camp_pdf"]
    values = json.loads(case["responses"][-1]["raw"])
    values["category_code"] = None
    summary = ground_summary(
        NoticeSummary.model_validate(values),
        NoticeInput.model_validate(case["notice"]),
        media_sources=tuple(MediaSource(**source) for source in case["media_sources"]),
    )
    internal = build_summary_cards(summary)
    dates = internal.cards.deadline.items
    assert dates and dates[0].value.start_date == "2026-10-29"
    assert dates[0].evidence[0].scope == "field"
    assert dates[0].evidence[0].evidence.verification == "file_reference_only"
    public = build_notice_summary_view(
        status="needs_review", result=summary, attachment_status="all_read"
    )
    assert public.status == "needs_review"
    assert public.message == "원문 확인 요함"
    assert public.content is not None
    assert public.content.headline.value == summary.summary
    assert public.content.headline.text == summary.summary
    assert public.content.cards.model_dump() == internal.cards.model_dump()
    assert public.content.metadata.model_dump() == internal.metadata.model_dump()
