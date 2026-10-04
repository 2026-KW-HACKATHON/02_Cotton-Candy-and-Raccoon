"""Replay captured public notices and exercise independent field grounding offline."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from pipeline.transform import summarize as summarize_module
from pipeline.transform.grounding import REVIEW_NOTE, ground_summary, unknown_summary
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_schema import (
    MediaSource,
    NoticeSummary,
    validate_evidence,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "real_notice_grounding.json"


@pytest.fixture(scope="module")
def captures() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _notice(body: str, title: str = "캠프 안내") -> NoticeInput:
    return NoticeInput.model_validate(
        {
            "title": title,
            "body_text": body,
            "reference_datetime": "2026-10-03T12:00:00+09:00",
        }
    )


def _date(**changes: Any) -> dict[str, Any]:
    return {
        "kind": "event",
        "label": None,
        "text": None,
        "start_date": "2026-10-29",
        "end_date": "2026-10-31",
        "start_time": None,
        "end_time": None,
    } | changes


def _summary(notice: NoticeInput, **changes: Any) -> NoticeSummary:
    values = unknown_summary(notice).model_dump(mode="json")
    values.update(category="event", summary="캠프 안내", uncertainties=[])
    return NoticeSummary.model_validate(values | changes)


def _case(
    captures: dict[str, Any], name: str
) -> tuple[NoticeInput, NoticeSummary, tuple[MediaSource, ...]]:
    case = captures["cases"][name]
    notice = NoticeInput.model_validate(case["notice"])
    raw = NoticeSummary.model_validate_json(case["responses"][-1]["raw"])
    media = tuple(MediaSource(**value) for value in case["media_sources"])
    return notice, raw, media


def _ground_case(captures: dict[str, Any], name: str) -> NoticeSummary:
    notice, raw, media = _case(captures, name)
    return ground_summary(raw, notice, media_sources=media)


def test_captured_response_fixture_preserves_original_bytes_and_source_references(
    captures: dict[str, Any],
) -> None:
    assert set(captures["cases"]) == {"camp_text", "gifted_text", "camp_pdf"}
    for case in captures["cases"].values():
        assert case["source_url"].startswith("https://www.nowon.kr/")
        for response in case["responses"]:
            assert hashlib.sha256(response["raw"].encode("utf-8")).hexdigest() == response["sha256"]
            assert isinstance(json.loads(response["raw"]), dict)
    pdf = captures["cases"]["camp_pdf"]
    assert pdf["notice"]["body_text"] == ""
    assert pdf["attachment_url"].startswith("https://www.nowon.kr/component/file/")
    assert "기  간" in pdf["review_extracted_text"]


def test_actual_pdf_response_keeps_spaced_period_and_place_even_when_summary_is_unsupported(
    captures: dict[str, Any],
) -> None:
    result = _ground_case(captures, "camp_pdf")
    assert result.summary == REVIEW_NOTE
    assert result.category == "event"
    assert result.location == "강원도 고성 일원"
    assert [(entry.kind, entry.start_date, entry.end_date) for entry in result.dates] == [
        ("event", "2026-10-29", "2026-10-31")
    ]
    assert result.status == "upcoming"
    assert result.uncertainties == [REVIEW_NOTE]
    original_quotes = {item.field: item.excerpt for item in _case(captures, "camp_pdf")[1].evidence}
    for field in ("location", "dates"):
        item = next(item for item in result.evidence if item.field == field)
        assert item.excerpt == original_quotes[field]
        assert item.source_id == "media_1"
        assert item.page == 1
        assert item.verification == "file_reference_only"


def test_actual_camp_response_keeps_application_and_an_unresolved_camp_period(
    captures: dict[str, Any],
) -> None:
    result = _ground_case(captures, "camp_text")
    assert result.category == "application"
    assert result.summary == "청소년 역사·평화·환경 캠프 참가자 모집"
    assert result.action == "노원구청 홈페이지 인터넷 접수"
    assert result.location == "강원도 고성군 일원"
    intervals = {(entry.kind, entry.start_date, entry.end_date) for entry in result.dates}
    assert ("application", "2026-09-14", "2026-10-02") in intervals
    assert ("other", "2026-10-29", "2026-10-31") in intervals
    assert ("event", "2026-10-29", "2026-10-31") not in intervals
    assert result.status == "closed"
    assert result.uncertainties == [REVIEW_NOTE]


def test_actual_gifted_response_retains_application_category_despite_unsupported_summary(
    captures: dict[str, Any],
) -> None:
    result = _ground_case(captures, "gifted_text")
    assert result.summary == REVIEW_NOTE
    assert result.category == "application"
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("case_name", ["camp_text", "gifted_text"])
def test_original_text_responses_replay_through_the_public_summarizer_without_api(
    monkeypatch: pytest.MonkeyPatch, captures: dict[str, Any], case_name: str
) -> None:
    case = captures["cases"][case_name]
    notice = NoticeInput.model_validate(case["notice"])
    responses = iter(response["raw"] for response in case["responses"])
    calls: list[dict[str, Any]] = []

    def generate(**kwargs: Any) -> str:
        calls.append(deepcopy(kwargs))
        return next(responses)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    result = summarize_module.summarize_notice(notice, api_key="offline-test-key")
    assert len(calls) == 2
    assert result.category == "application"
    if case_name == "camp_text":
        assert result.action == "노원구청 홈페이지 인터넷 접수"
        assert any(
            entry.kind == "other" and entry.start_date == "2026-10-29" for entry in result.dates
        )
    else:
        assert result.summary == REVIEW_NOTE


@pytest.mark.parametrize("spacing", [" ", "  ", "\t", "\u3000"])
def test_table_labels_allow_whitespace_without_changing_the_original_quotes(spacing: str) -> None:
    period = f"기{spacing}간 : 2026-10-29~2026-10-31"
    place = f"장{spacing}소 : 월계공원"
    notice = _notice(f"캠프 안내\n{period}\n{place}")
    summary = _summary(
        notice,
        dates=[_date()],
        location="월계공원",
        status="upcoming",
        evidence=[
            {"field": "summary", "excerpt": "캠프 안내"},
            {"field": "dates", "excerpt": period},
            {"field": "location", "excerpt": place},
        ],
    )
    result = ground_summary(summary, notice)
    assert [(entry.kind, entry.start_date, entry.end_date) for entry in result.dates] == [
        ("event", "2026-10-29", "2026-10-31")
    ]
    assert result.location == "월계공원"
    assert next(item.excerpt for item in result.evidence if item.field == "dates") == period
    assert next(item.excerpt for item in result.evidence if item.field == "location") == place


@pytest.mark.parametrize("kind", ["application", "payment", "operation"])
@pytest.mark.parametrize("returned_as_event", [False, True])
def test_specific_application_payment_or_operation_period_cannot_borrow_camp_event_kind(
    kind: str, returned_as_event: bool
) -> None:
    label = {"application": "신청기간", "payment": "납부기간", "operation": "운영기간"}[kind]
    period = f"{label}: 2026-10-29~2026-10-31"
    notice = _notice(f"캠프 안내\n{period}")
    entry = _date(kind="event" if returned_as_event else kind)
    summary = _summary(
        notice,
        dates=[entry],
        evidence=[
            {"field": "summary", "excerpt": "캠프 안내"},
            {"field": "dates", "excerpt": period},
        ],
    )
    result = ground_summary(summary, notice)
    if returned_as_event:
        assert result.dates == []
        assert result.status == "unknown"
    else:
        assert [(date.kind, date.start_date, date.end_date) for date in result.dates] == [
            (kind, "2026-10-29", "2026-10-31")
        ]


def test_a_bare_period_keeps_dates_as_other_without_establishing_event_kind() -> None:
    period = "기 간: 2026-10-29~2026-10-31"
    notice = _notice(f"자료 안내\n{period}", title="자료 안내")
    summary = _summary(
        notice,
        summary="자료 안내",
        category="unknown",
        dates=[_date()],
        evidence=[
            {"field": "summary", "excerpt": "자료 안내"},
            {"field": "dates", "excerpt": period},
        ],
    )
    result = ground_summary(summary, notice)
    assert [(entry.kind, entry.start_date, entry.end_date) for entry in result.dates] == [
        ("other", "2026-10-29", "2026-10-31")
    ]
    assert result.status == "unknown"
    assert result.uncertainties == [REVIEW_NOTE]


def test_generic_pdf_period_cannot_borrow_event_context_from_another_file() -> None:
    notice = _notice("")
    summary = _summary(
        notice,
        dates=[_date()],
        evidence=[
            {
                "field": "summary",
                "excerpt": "캠프 안내",
                "source_type": "document",
                "source_id": "media_2",
                "page": 1,
            },
            {
                "field": "dates",
                "excerpt": "기 간: 2026-10-29~2026-10-31",
                "source_type": "document",
                "source_id": "media_1",
                "page": 1,
            },
        ],
    )
    result = ground_summary(
        summary,
        notice,
        media_sources=(MediaSource("media_1", "document"), MediaSource("media_2", "document")),
    )
    assert result.category == "event"
    assert [(entry.kind, entry.start_date, entry.end_date) for entry in result.dates] == [
        ("other", "2026-10-29", "2026-10-31")
    ]
    assert result.status == "unknown"
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("end_date", ["2026-10-31", "2026-10-29"])
def test_unresolved_period_fallback_still_checks_the_role_of_a_partial_endpoint(
    end_date: str,
) -> None:
    period = "기 간: 2026-10-29~2026-10-31"
    notice = _notice(f"자료 안내\n{period}", title="자료 안내")
    summary = _summary(
        notice,
        summary="자료 안내",
        category="unknown",
        dates=[_date(start_date=None, end_date=end_date)],
        evidence=[
            {"field": "summary", "excerpt": "자료 안내"},
            {"field": "dates", "excerpt": period},
        ],
    )
    result = ground_summary(summary, notice)
    if end_date == "2026-10-31":
        assert [(entry.kind, entry.start_date, entry.end_date) for entry in result.dates] == [
            ("other", None, "2026-10-31")
        ]
    else:
        assert result.dates == []
    assert result.status == "unknown"
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize(
    "preceding_context",
    [
        "캠프 안내\n\n",
        "캠프 안내\n\n\n",
        "캠프 안내\n\n\n\n",
        "캠프 안내\n다른 자료\n",
        "캠프 안내\n신청기간: 2026-09-01~2026-09-03\n",
    ],
    ids=[
        "one-blank-line",
        "two-blank-lines",
        "three-blank-lines",
        "other-material",
        "other-period",
    ],
)
def test_generic_period_does_not_borrow_event_kind_from_disconnected_title(
    preceding_context: str,
) -> None:
    period = "기간: 2026-10-29~2026-10-31"
    notice = _notice(preceding_context + period)
    summary = _summary(
        notice,
        dates=[_date()],
        status="upcoming",
        evidence=[
            {"field": "summary", "excerpt": "캠프 안내"},
            {"field": "dates", "excerpt": period},
        ],
    )
    result = ground_summary(summary, notice)
    assert result.category == "event"
    assert [(entry.kind, entry.start_date, entry.end_date) for entry in result.dates] == [
        ("other", "2026-10-29", "2026-10-31")
    ]
    assert result.status == "unknown"
    assert result.uncertainties == [REVIEW_NOTE]


def test_same_pdf_page_preserves_both_quotes_for_generic_and_explicit_application_periods() -> None:
    notice = _notice("")
    period_a = "기 간: 2026-10-29~2026-10-31"
    period_b = "신청기간: 2026-10-10~2026-10-20"

    def file_quote(field: str, excerpt: str) -> dict[str, Any]:
        return {
            "field": field,
            "excerpt": excerpt,
            "source_type": "document",
            "source_id": "media_1",
            "page": 1,
        }

    summary = _summary(
        notice,
        dates=[
            _date(),
            _date(kind="application", start_date="2026-10-10", end_date="2026-10-20"),
        ],
        status="upcoming",
        evidence=[
            file_quote("summary", "캠프 안내"),
            file_quote("dates", period_a),
            file_quote("dates", period_b),
        ],
    )
    media = (MediaSource("media_1", "document"),)
    result = ground_summary(summary, notice, media_sources=media)
    assert [(entry.kind, entry.start_date, entry.end_date) for entry in result.dates] == [
        ("other", "2026-10-29", "2026-10-31"),
        ("application", "2026-10-10", "2026-10-20"),
    ]
    date_quotes = [item for item in result.evidence if item.field == "dates"]
    assert [item.excerpt for item in date_quotes] == [period_a, period_b]
    assert all(item.source_id == "media_1" for item in date_quotes)
    assert all(item.page == 1 for item in date_quotes)
    assert all(item.verification == "file_reference_only" for item in date_quotes)
    assert result.status == "unknown"
    assert result.uncertainties == [REVIEW_NOTE]
    validate_evidence(result, body_text="", attachment_texts=[], media_sources=media)


@pytest.mark.parametrize("category", ["obligation", "living", "news", "mixed"])
@pytest.mark.parametrize("valid_headline", [False, True], ids=["unsupported", "legacy-valid"])
def test_other_categories_cannot_turn_an_unsupported_headline_into_verified_category_evidence(
    category: str, valid_headline: bool
) -> None:
    headline = "캠프 안내"
    action_quote = "신청 방법: 현장 신청 가능"
    date_quote = "행사: 2026-10-29~2026-10-31"
    note_quote = "유의사항: 신분증 지참"
    notice = _notice(f"{headline}\n{action_quote}\n{date_quote}\n{note_quote}")
    summary = _summary(
        notice,
        category=category,
        summary=headline if valid_headline else "원문에 없는 잘못된 제목",
        action="현장 신청",
        action_requirement="optional",
        dates=[_date()],
        notes=["신분증 지참"],
        evidence=[
            {"field": "summary", "excerpt": headline},
            {"field": "action", "excerpt": action_quote},
            {"field": "dates", "excerpt": date_quote},
            {"field": "notes", "excerpt": note_quote},
        ],
    )
    result = ground_summary(summary, notice)
    assert result.category == (category if valid_headline else "unknown")
    assert result.summary == (headline if valid_headline else REVIEW_NOTE)
    assert result.action == "현장 신청"
    assert result.notes == ["신분증 지참"]
    assert [(entry.kind, entry.start_date, entry.end_date) for entry in result.dates] == [
        ("event", "2026-10-29", "2026-10-31")
    ]
    assert result.status == "unknown"
    if not valid_headline:
        assert all(item.field != "category" for item in result.evidence)
        assert result.uncertainties == [REVIEW_NOTE]
    else:
        # This change intentionally preserves legacy rules for supported titles.
        assert result.uncertainties == []
    validate_evidence(result, body_text=notice.body_text, attachment_texts=[])


@pytest.mark.parametrize(
    ("headline", "category", "expected"),
    [
        ("청소년 역사·평화·환경 캠프", "event", "event"),
        ("캠프 참가자 모집", "application", "application"),
        ("캠프 참가자 모집", "event", "unknown"),
    ],
)
def test_camp_category_distinguishes_an_event_title_from_participant_recruitment(
    headline: str, category: str, expected: str
) -> None:
    notice = _notice(headline, title=headline)
    result = ground_summary(
        _summary(
            notice,
            category=category,
            summary=headline,
            evidence=[{"field": "summary", "excerpt": headline}],
        ),
        notice,
    )
    assert result.category == expected
    assert result.summary == headline


@pytest.mark.parametrize("category", ["application", "unknown"])
def test_summary_rejection_does_not_erase_independently_supported_action_dates_or_notes(
    captures: dict[str, Any], category: str
) -> None:
    notice, raw, _ = _case(captures, "camp_text")
    summary = _summary(
        notice,
        category=category,
        summary="지원금을 모든 주민에게 지급",
        action=raw.action,
        action_requirement="optional",
        dates=[raw.dates[0].model_dump()],
        status="closed",
        notes=["모집기간 내 상시 신청 가능"],
        evidence=[
            {"field": "summary", "excerpt": "「청소년 역사·평화·환경 캠프」참가자 모집 안내"},
            {"field": "action", "excerpt": "■ 신청방법 : 노원구청 홈페이지 인터넷 접수"},
            {
                "field": "dates",
                "excerpt": "■ 모집기간 : 2026. 9. 14. (월) 09:00 ~ 10. 2. (금) 18:00",
            },
            {"field": "notes", "excerpt": "※ 모집기간 내 상시 신청 가능"},
        ],
    )
    result = ground_summary(summary, notice)
    assert result.summary == REVIEW_NOTE
    assert result.category == category
    assert result.action == "노원구청 홈페이지 인터넷 접수"
    assert result.notes == ["모집기간 내 상시 신청 가능"]
    assert [(entry.kind, entry.start_date, entry.end_date) for entry in result.dates] == [
        ("application", "2026-09-14", "2026-10-02")
    ]
    assert result.status == ("closed" if category == "application" else "unknown")
    assert result.uncertainties == [REVIEW_NOTE]
    validate_evidence(result, body_text=notice.body_text, attachment_texts=[])


def test_a_wrong_event_category_does_not_destroy_supported_recruitment_fields(
    captures: dict[str, Any],
) -> None:
    notice, raw, _ = _case(captures, "camp_text")
    values = raw.model_dump(mode="json")
    values["category"] = "event"
    values["notes"] = ["모집기간 내 상시 신청 가능"]
    values["evidence"].append({"field": "notes", "excerpt": "※ 모집기간 내 상시 신청 가능"})
    result = ground_summary(NoticeSummary.model_validate(values), notice)
    assert result.category == "unknown"
    assert result.summary == "청소년 역사·평화·환경 캠프 참가자 모집"
    assert result.action == "노원구청 홈페이지 인터넷 접수"
    assert result.notes == ["모집기간 내 상시 신청 가능"]
    assert any(entry.kind == "application" for entry in result.dates)
    assert result.status == "unknown"


def test_the_captured_image_response_without_source_ids_is_still_rejected(
    captures: dict[str, Any],
) -> None:
    case = captures["cases"]["camp_pdf"]
    image = case["image_counterexample"]
    raw = NoticeSummary.model_validate_json(image["response"]["raw"])
    assert all(item.source_id is None for item in raw.evidence)
    result = ground_summary(
        raw,
        NoticeInput.model_validate(case["notice"]),
        media_sources=tuple(MediaSource(**value) for value in image["media_sources"]),
    )
    assert result.category == "unknown"
    assert result.summary == REVIEW_NOTE
    assert result.location is None
    assert result.dates == []
    assert result.evidence == []


def test_valid_unknown_classification_retains_fields_but_does_not_infer_status(
    captures: dict[str, Any],
) -> None:
    notice, raw, _ = _case(captures, "camp_text")
    values = raw.model_dump(mode="json")
    values["category"] = "unknown"
    values["notes"] = ["모집기간 내 상시 신청 가능"]
    values["evidence"].append({"field": "notes", "excerpt": "※ 모집기간 내 상시 신청 가능"})
    result = ground_summary(NoticeSummary.model_validate(values), notice)
    assert result.category == "unknown"
    assert result.summary == "청소년 역사·평화·환경 캠프 참가자 모집"
    assert result.action == "노원구청 홈페이지 인터넷 접수"
    assert result.notes == ["모집기간 내 상시 신청 가능"]
    assert any(entry.kind == "application" for entry in result.dates)
    assert result.status == "unknown"
