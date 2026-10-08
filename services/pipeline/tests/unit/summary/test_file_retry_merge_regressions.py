"""File-reference retries preserve distinct schedules and missing valid values."""

import json
from copy import deepcopy
from typing import Any

import pytest
from support.gemini_multimodal import _media, _prepared

from pipeline.transform import summarize as summarize_module
from pipeline.transform.grounding import REVIEW_NOTE, unknown_summary
from pipeline.transform.summary_schema import NoticeSummary


def _date_entry(
    day: str = "2026-10-10", *, start_time: str = "10:00", end_time: str = "11:00"
) -> dict[str, Any]:
    return {
        "kind": "event",
        "label": "행사",
        "text": day,
        "start_date": day,
        "end_date": day,
        "start_time": start_time,
        "end_time": end_time,
    }


def _output(
    *, dates: list[dict[str, Any]] | None = None, topics: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    output = unknown_summary(_prepared().notice, has_media=True).model_dump()
    output.update(
        category="mixed" if topics else "event",
        summary="행사 안내",
        status="upcoming",
        notice_update="new",
        dates=dates or [],
        topics=topics or [],
        uncertainties=[],
        card_summaries={
            "audience": None,
            "deadline": (
                "일정은 " + "; ".join(
                    " ".join(str(value) for value in entry.values() if value) + "예요."
                    for entry in dates
                ) if dates else None
            ),
            "action": None,
            "notes": None,
        },
        evidence=[
            {
                "field": field,
                "excerpt": excerpt,
                "source_type": "document",
                "source_id": "media_1",
                "page": 1,
            }
            for field, excerpt in (
                ("summary", "행사 안내"),
                *([("dates", "행사 일정 안내")] if dates else []),
                *([("topics", "프로그램별 안내")] if topics else []),
            )
        ],
    )
    return output


def _run_retry(
    monkeypatch: pytest.MonkeyPatch, first: dict[str, Any], retry: dict[str, Any]
) -> NoticeSummary:
    first = deepcopy(first)
    for item in first["evidence"]:
        item["source_id"] = None
    responses = [first, deepcopy(retry)]
    requests = []

    def generate(**kwargs: Any) -> str:
        index = len(requests)
        requests.append(deepcopy(kwargs))
        assert index < 2, "reference correction has one shared retry"
        return json.dumps(responses[index], ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    result = summarize_module.summarize_prepared_notice(
        _prepared("", _media("document")), api_key="offline-test-key"
    ).summary

    assert len(requests) == 2
    assert all(
        any(block["type"] == "document" for block in request["notice_text"]) for request in requests
    )
    return result


def test_same_schedule_label_keeps_each_day_and_its_retry_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _output(dates=[_date_entry(), _date_entry("2026-10-11")])
    retry = _output(
        dates=[_date_entry("2026-10-11", end_time="13:00"), _date_entry(end_time="12:00")]
    )

    result = _run_retry(monkeypatch, first, retry)

    assert len(result.dates) == 2
    assert {entry.start_date: entry.end_time for entry in result.dates} == {
        "2026-10-10": "12:00",
        "2026-10-11": "13:00",
    }


def test_same_topic_title_keeps_distinct_categories_and_their_corrections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _output(
        topics=[
            {"title": "문화 프로그램", "category": "application", "summary": "청년 강사 모집"},
            {"title": "문화 프로그램", "category": "event", "summary": "주민 체험 행사"},
        ]
    )
    retry = _output(
        topics=[
            {"title": "문화 프로그램", "category": "event", "summary": "주민 공예 체험 행사"},
            {
                "title": "문화 프로그램",
                "category": "application",
                "summary": "청년 강사 지원서 접수",
            },
        ]
    )

    result = _run_retry(monkeypatch, first, retry)

    assert len(result.topics) == 2
    assert {topic.category: topic.summary for topic in result.topics} == {
        "application": "청년 강사 지원서 접수",
        "event": "주민 공예 체험 행사",
    }


def test_retry_null_start_values_preserve_the_first_schedule_with_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_date = _date_entry(start_time="22:00", end_time="01:00") | {
        "end_date": "2026-10-11",
        "text": "10월10일 22시~10월11일 1시",
    }
    first = _output(dates=[first_date])
    retry = _output(dates=[first_date | {"start_date": None, "start_time": None, "text": None}])

    result = _run_retry(monkeypatch, first, retry)

    assert len(result.dates) == 1
    assert result.dates[0].model_dump() == first_date
    assert REVIEW_NOTE in result.uncertainties


def test_retry_never_restores_an_end_already_cleared_for_reversed_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_date = _date_entry("2026-10-11", end_time="09:00") | {"end_date": "2026-10-10"}
    first = _output(dates=[first_date])
    retry = _output(dates=[first_date | {"end_date": None, "end_time": None}])
    retry["status"] = "unknown"

    result = _run_retry(monkeypatch, first, retry)

    assert len(result.dates) == 1
    assert result.dates[0].start_date == "2026-10-11"
    assert result.dates[0].start_time == "10:00"
    assert result.dates[0].end_date is None
    assert result.dates[0].end_time is None
    assert result.status == "unknown"


def test_same_day_sessions_match_by_start_time_when_retry_order_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _output(dates=[_date_entry(), _date_entry(start_time="14:00", end_time="15:00")])
    retry = _output(
        dates=[
            _date_entry(start_time="14:00", end_time="16:00"),
            _date_entry(end_time="12:00"),
        ]
    )

    result = _run_retry(monkeypatch, first, retry)

    assert len(result.dates) == 2
    assert {entry.start_time: entry.end_time for entry in result.dates} == {
        "10:00": "12:00",
        "14:00": "16:00",
    }


def test_ambiguous_new_session_keeps_both_previous_sessions_and_the_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _output(dates=[_date_entry(), _date_entry(start_time="14:00", end_time="15:00")])
    retry = _output(dates=[_date_entry(start_time="12:00", end_time="13:00")])

    result = _run_retry(monkeypatch, first, retry)

    assert len(result.dates) == 3
    assert {entry.start_time: entry.end_time for entry in result.dates} == {
        "10:00": "11:00",
        "12:00": "13:00",
        "14:00": "15:00",
    }
    assert REVIEW_NOTE in result.uncertainties
