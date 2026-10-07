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


def _current_contract_raw(raw: str, *, fresh_response: bool = False) -> str:
    """Adapt historical replay shape without editing captures or inferring a field.

    The old prompt never requested category_code. Use null only for these offline
    grounding regressions; production continues to require the new response key.
    Fresh API mocks also need prose for known source fields. These quoted test
    cards adapt the contract only; they are not captured historical AI prose.
    """
    data = json.loads(raw)
    data.setdefault("category_code", None)
    if fresh_response:
        data["card_summaries"] = {
            "audience": f"“{data['audience']}”가 대상이에요." if data.get("audience") else None,
            "deadline": " ".join(
                "일정은 “" + " / ".join(str(value) for value in entry.values() if value)
                + "”를 확인해 주세요." for entry in data.get("dates", [])
            ) or None,
            "action": f"“{data['action']}”를 진행해 주세요." if data.get("action") else None,
            "notes": " ".join(
                f"“{note}”를 확인해 주세요." for note in data.get("notes", [])
            ) or None,
        }
    return json.dumps(data, ensure_ascii=False)


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
    raw = NoticeSummary.model_validate_json(_current_contract_raw(case["responses"][-1]["raw"]))
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


def test_actual_pdf_only_response_preserves_summary_period_and_place(
    captures: dict[str, Any],
) -> None:
    result = _ground_case(captures, "camp_pdf")
    assert result.summary == _case(captures, "camp_pdf")[1].summary
    assert result.category == "event"
    assert result.location == "강원도 고성 일원"
    assert [(entry.kind, entry.start_date, entry.end_date) for entry in result.dates] == [
        ("event", "2026-10-29", "2026-10-31")
    ]
    assert result.status == "upcoming"
    assert result.uncertainties == _case(captures, "camp_pdf")[1].uncertainties
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
def test_historical_text_responses_replay_with_unknown_field_and_without_api(
    monkeypatch: pytest.MonkeyPatch, captures: dict[str, Any], case_name: str
) -> None:
    case = captures["cases"][case_name]
    notice = NoticeInput.model_validate(case["notice"])
    responses = iter(
        _current_contract_raw(response["raw"], fresh_response=True)
        for response in case["responses"]
    )
    calls: list[dict[str, Any]] = []

    def generate(**kwargs: Any) -> str:
        calls.append(deepcopy(kwargs))
        return next(responses)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    result = summarize_module.summarize_notice(notice, api_key="offline-test-key")
    assert len(calls) == 2
    assert result.category_code is None
    assert result.category == "application"
    if case_name == "camp_text":
        assert result.action == "노원구청 홈페이지 인터넷 접수"
        assert any(entry.start_date == "2026-10-29" for entry in result.dates)
    else:
        assert result.summary == "노원영재교육원 심화과정 신입생 모집 신청 안내"
    assert REVIEW_NOTE in result.uncertainties


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


def test_mixed_generic_pdf_period_cannot_borrow_event_context_from_another_file() -> None:
    notice = _notice("첨부 자료를 확인하세요.")
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


def test_mixed_same_pdf_page_preserves_both_quotes_for_different_application_periods() -> None:
    notice = _notice("첨부 자료를 확인하세요.")
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


def test_captured_image_only_response_without_source_ids_is_preserved_as_unverified(
    captures: dict[str, Any],
) -> None:
    case = captures["cases"]["camp_pdf"]
    image = case["image_counterexample"]
    raw = NoticeSummary.model_validate_json(_current_contract_raw(image["response"]["raw"]))
    assert all(item.source_id is None for item in raw.evidence)
    result = ground_summary(
        raw,
        NoticeInput.model_validate(case["notice"]),
        media_sources=tuple(MediaSource(**value) for value in image["media_sources"]),
    )
    assert result.category == raw.category
    assert result.summary == raw.summary
    assert result.location == raw.location
    assert result.dates == raw.dates
    assert result.evidence
    assert all(item.verification is None for item in result.evidence)
    assert result.uncertainties == [REVIEW_NOTE]


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


def test_actual_camp_short_quotes_keep_source_place_and_optional_application(
    captures: dict[str, Any],
) -> None:
    notice, raw, _ = _case(captures, "camp_text")
    values = raw.model_dump(mode="json")
    values["action_requirement"] = "optional"
    for item in values["evidence"]:
        if item["field"] in ("location", "action"):
            item["excerpt"] = values[item["field"]]
    result = ground_summary(NoticeSummary.model_validate(values), notice)
    assert result.location == "강원도 고성군 일원"
    assert result.action == "노원구청 홈페이지 인터넷 접수"
    assert result.action_requirement == "optional"
    assert result.status == "closed"
    for field in ("location", "action"):
        item = next(item for item in result.evidence if item.field == field)
        assert item.excerpt == values[field]
        assert item.verification == "text_matched"


@pytest.mark.parametrize("label", ["장소", "장 소", "장\t소", "위치", "개최지", "행사장"])
def test_short_place_quote_uses_only_its_actual_source_label(label: str) -> None:
    place = "강원도 고성군 일원"
    notice = _notice(f"캠프 안내\n■ {label} : {place}")
    result = ground_summary(
        _summary(
            notice,
            location=place,
            evidence=[
                {"field": "summary", "excerpt": "캠프 안내"},
                {"field": "location", "excerpt": place},
            ],
        ),
        notice,
    )
    assert result.location == place


@pytest.mark.parametrize("label", ["문의", "접수기관", "적용지역", "대상", "주최"])
def test_contact_application_or_area_label_does_not_establish_a_place(label: str) -> None:
    notice = _notice(f"캠프 안내\n{label}: 노원구청")
    result = ground_summary(
        _summary(
            notice,
            location="노원구청",
            evidence=[
                {"field": "summary", "excerpt": "캠프 안내"},
                {"field": "location", "excerpt": "노원구청"},
            ],
        ),
        notice,
    )
    assert result.location is None


@pytest.mark.parametrize(
    "source",
    [
        "장소: 노원구청 홈페이지 인터넷 접수",
        "장소: 노원구청의 홈페이지에서 신청",
        "장소: 노원구청 공식 홈페이지에서 신청",
        "장소: 노원구청 아님",
        "노원구청에서 캠프를 개최하지 않습니다",
        "장소: 노원구청은 행사가 열리지 않는 장소입니다",
        "장소: 노원구청은 행사 장소가 아닙니다",
        "문의: 노원구청; 장소: 월계공원",
    ],
)
def test_place_quote_cannot_hide_online_route_negation_or_another_place(source: str) -> None:
    notice = _notice(f"캠프 안내\n{source}")
    result = ground_summary(
        _summary(
            notice,
            location="노원구청",
            evidence=[
                {"field": "summary", "excerpt": "캠프 안내"},
                {"field": "location", "excerpt": "노원구청"},
            ],
        ),
        notice,
    )
    assert result.location is None


@pytest.mark.parametrize("separator", ["\n", "; ", ", "])
@pytest.mark.parametrize("second_role", ["장소", "문의"])
def test_repeated_short_place_quote_requires_all_possible_origins_to_agree(
    separator: str,
    second_role: str,
) -> None:
    place = "노원구청"
    notice = _notice(f"캠프 안내\n장소: {place}{separator}{second_role}: {place}")
    result = ground_summary(
        _summary(
            notice,
            location=place,
            evidence=[
                {"field": "summary", "excerpt": "캠프 안내"},
                {"field": "location", "excerpt": place},
            ],
        ),
        notice,
    )
    assert result.location == (place if second_role == "장소" else None)


def test_full_place_quote_disambiguates_a_repeated_contact_name() -> None:
    notice = _notice("캠프 안내\n장소: 노원구청\n문의: 노원구청")
    result = ground_summary(
        _summary(
            notice,
            location="노원구청",
            evidence=[
                {"field": "summary", "excerpt": "캠프 안내"},
                {"field": "location", "excerpt": "장소: 노원구청"},
            ],
        ),
        notice,
    )
    assert result.location == "노원구청"


def test_place_quote_keeps_a_venue_despite_a_conditional_weather_cancellation() -> None:
    notice = _notice("캠프 안내\n장소: 월계공원 (우천 시 행사 취소 가능)")
    result = ground_summary(
        _summary(
            notice,
            location="월계공원",
            evidence=[
                {"field": "summary", "excerpt": "캠프 안내"},
                {"field": "location", "excerpt": "월계공원"},
            ],
        ),
        notice,
    )
    assert result.location == "월계공원"


def test_short_place_quote_does_not_borrow_a_label_from_a_different_text_document() -> None:
    notice = NoticeInput.model_validate(
        _notice("캠프 안내\n문의: 노원구청").model_dump()
        | {"attachments": [{"name": "다른 행사", "text": "장소: 노원구청"}]}
    )
    result = ground_summary(
        _summary(
            notice,
            location="노원구청",
            evidence=[
                {"field": "summary", "excerpt": "캠프 안내"},
                {"field": "location", "excerpt": "노원구청"},
            ],
        ),
        notice,
    )
    assert result.location is None


@pytest.mark.parametrize(
    ("source", "requirement", "expected", "action_kept"),
    [
        ("신청방법: 온라인 접수", "optional", "optional", True),
        ("접수방법: 희망자는 온라인 접수", "optional", "optional", True),
        ("신청방법: 반드시 온라인 접수", "required", "required", True),
        ("신청방법: 반드시 온라인 접수", "optional", "unknown", True),
        ("신청방법: 온라인 접수 불가", "optional", "unknown", False),
        ("필수 아님: 온라인 접수", "required", "unknown", True),
        ("신청방법: 온라인 접수 (필수 아님)", "optional", "optional", True),
        ("신청방법: 온라인 접수는 필수가 아닙니다", "required", "unknown", True),
        ("신청방법: 온라인 접수는 의무가 아닙니다", "required", "unknown", True),
        ("신청방법: 온라인 접수는 필수가 아닙니다", "optional", "optional", True),
        ("신청방법: 온라인 접수는 의무가 아닙니다", "optional", "optional", True),
        ("필수서류: 신분증; 신청방법: 온라인 접수", "required", "unknown", True),
        ("필수서류: 신분증; 신청방법: 온라인 접수", "optional", "optional", True),
    ],
)
def test_short_action_quote_retains_local_requirement_without_borrowing_or_negating_it(
    source: str,
    requirement: str,
    expected: str,
    action_kept: bool,
) -> None:
    action = "온라인 접수"
    notice = _notice(f"캠프 안내\n{source}")
    result = ground_summary(
        _summary(
            notice,
            action=action,
            action_requirement=requirement,
            evidence=[
                {"field": "summary", "excerpt": "캠프 안내"},
                {"field": "action", "excerpt": action},
            ],
        ),
        notice,
    )
    assert result.action_requirement == expected
    assert result.action == (action if action_kept else None)


@pytest.mark.parametrize("quote_style", ["text_short", "text_full", "document_full"])
@pytest.mark.parametrize(
    ("source", "requirement", "expected"),
    [
        ("온라인 접수 가능, 현장 참여자는 반드시 신분증 지참", "optional", "optional"),
        ("현장 참여자는 반드시 신분증 지참, 온라인 접수 가능", "optional", "optional"),
        ("온라인 접수 가능, 반드시 신분증 지참", "optional", "optional"),
        ("온라인 접수 가능, 신청자는 반드시 신분증 지참", "optional", "optional"),
        ("신청자는 반드시 신분증 지참, 온라인 접수 가능", "optional", "optional"),
        ("온라인 접수 가능，현장 참여자는 반드시 신분증 지참", "optional", "optional"),
        ("온라인 접수 가능하며 현장 참여자는 반드시 신분증 지참", "optional", "optional"),
        ("신청방법: 온라인 접수, 신분증 지참은 모두 필수입니다", "optional", "unknown"),
        ("온라인 접수 가능, 신분증 지참은 모두 필수입니다", "optional", "unknown"),
        ("온라인 접수 가능, 현장 참여자는 모두 반드시 신분증 지참", "optional", "optional"),
        ("온라인 접수 가능, 반드시 신분증 지참", "required", "unknown"),
        ("신청방법: 온라인 접수, 반드시 해야 합니다", "optional", "unknown"),
        ("신청방법: 온라인 접수, 반드시 해야 합니다", "required", "required"),
        ("신청방법: 반드시, 온라인 접수", "optional", "unknown"),
        ("신청방법: 반드시, 온라인 접수", "required", "required"),
        (
            "온라인 접수 필수, 현장 참여자의 신분증 지참은 필수가 아닙니다",
            "required",
            "required",
        ),
        (
            "온라인 접수는 필수, 신분증 지참은 필수 사항이 아닙니다",
            "required",
            "required",
        ),
        (
            "온라인 접수는 필수, 신분증 지참은 필수 사항이 아닙니다",
            "optional",
            "unknown",
        ),
    ],
)
def test_other_actions_obligations_do_not_change_this_action_requirement(
    source: str, requirement: str, expected: str, quote_style: str
) -> None:
    action = "온라인 접수"
    notice = _notice(f"캠프 안내\n{source}" if quote_style != "document_full" else "캠프 안내")
    quote: dict[str, Any] = {
        "field": "action",
        "excerpt": action if quote_style == "text_short" else source,
    }
    if quote_style == "document_full":
        quote.update(source_type="document", source_id="media_1", page=1)
    result = ground_summary(
        _summary(
            notice,
            action=action,
            action_requirement=requirement,
            evidence=[{"field": "summary", "excerpt": "캠프 안내"}, quote],
        ),
        notice,
        media_sources=(MediaSource("media_1", "document"),)
        if quote_style == "document_full"
        else (),
    )
    assert result.action == action
    assert result.action_requirement == expected


@pytest.mark.parametrize("quote_style", ["text_short", "text_full", "document_full"])
def test_a_shared_leading_obligation_is_not_removed_from_an_application_list(
    quote_style: str,
) -> None:
    action = "온라인 신청"
    source = f"신청방법: 반드시 신분증 지참, {action}"
    notice = _notice(f"캠프 안내\n{source}" if quote_style != "document_full" else "캠프 안내")
    quote: dict[str, Any] = {
        "field": "action",
        "excerpt": action if quote_style == "text_short" else source,
    }
    if quote_style == "document_full":
        quote.update(source_type="document", source_id="media_1", page=1)
    result = ground_summary(
        _summary(
            notice,
            action=action,
            action_requirement="optional",
            evidence=[{"field": "summary", "excerpt": "캠프 안내"}, quote],
        ),
        notice,
        media_sources=(MediaSource("media_1", "document"),)
        if quote_style == "document_full"
        else (),
    )
    assert result.action == action
    assert result.action_requirement == "unknown"
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("second_role", ["희망자는", "반드시"])
def test_repeated_short_action_quote_requires_compatible_requirement_at_every_origin(
    second_role: str,
) -> None:
    action = "온라인 접수"
    notice = _notice(f"캠프 안내\n신청방법: {action}\n접수방법: {second_role} {action}")
    result = ground_summary(
        _summary(
            notice,
            action=action,
            action_requirement="optional",
            evidence=[
                {"field": "summary", "excerpt": "캠프 안내"},
                {"field": "action", "excerpt": action},
            ],
        ),
        notice,
    )
    assert result.action == action
    assert result.action_requirement == ("optional" if second_role == "희망자는" else "unknown")


@pytest.mark.parametrize("field", ["location", "action"])
@pytest.mark.parametrize("quote_has_label", [False, True])
def test_file_quote_cannot_borrow_its_place_or_requirement_label_from_body_text(
    field: str,
    quote_has_label: bool,
) -> None:
    value = "노원구청" if field == "location" else "온라인 접수"
    label = "장소" if field == "location" else "신청방법"
    quote = f"{label}: {value}" if quote_has_label else value
    notice = _notice(f"캠프 안내\n{label}: {value}")
    values: dict[str, Any] = {field: value}
    if field == "action":
        values["action_requirement"] = "optional"
    result = ground_summary(
        _summary(
            notice,
            **values,
            evidence=[
                {"field": "summary", "excerpt": "캠프 안내"},
                {
                    "field": field,
                    "excerpt": quote,
                    "source_type": "document",
                    "source_id": "media_1",
                    "page": 1,
                },
            ],
        ),
        notice,
        media_sources=(MediaSource("media_1", "document"),),
    )
    if field == "location":
        assert result.location == (value if quote_has_label else None)
    else:
        assert result.action == value
        assert result.action_requirement == ("optional" if quote_has_label else "unknown")


@pytest.mark.parametrize(
    ("field", "label"),
    [
        ("location", "장소:"),
        ("location", "장소"),
        ("location", "장 소"),
        ("action", "신청방법:"),
    ],
)
@pytest.mark.parametrize("source_type", ["text", "document"])
@pytest.mark.parametrize("quote_prefix", ["", "캠프 안내\n", "행사명: 캠프\n"])
def test_a_label_only_preceding_line_is_preserved_when_included_in_the_quote(
    field: str,
    label: str,
    source_type: str,
    quote_prefix: str,
) -> None:
    value = "노원구청" if field == "location" else "온라인 접수"
    quote = f"{quote_prefix}{label}\n{value}"
    notice = _notice(f"캠프 안내\n{quote}" if source_type == "text" else "캠프 안내")
    values: dict[str, Any] = {field: value}
    if field == "action":
        values["action_requirement"] = "optional"
    evidence: dict[str, Any] = {"field": field, "excerpt": quote}
    if source_type == "document":
        evidence.update(source_type="document", source_id="media_1", page=1)
    result = ground_summary(
        _summary(
            notice,
            **values,
            evidence=[{"field": "summary", "excerpt": "캠프 안내"}, evidence],
        ),
        notice,
        media_sources=(MediaSource("media_1", "document"),),
    )
    assert getattr(result, field) == value
    if field == "action":
        assert result.action_requirement == "optional"
    assert next(item.excerpt for item in result.evidence if item.field == field) == quote


@pytest.mark.parametrize("field", ["location", "action"])
def test_a_short_quote_does_not_borrow_a_disconnected_previous_line_label(field: str) -> None:
    value = "노원구청" if field == "location" else "온라인 접수"
    label = "장소" if field == "location" else "신청방법"
    notice = _notice(f"캠프 안내\n{label}:\n다른 항목\n{value}")
    values: dict[str, Any] = {field: value}
    if field == "action":
        values["action_requirement"] = "optional"
    result = ground_summary(
        _summary(
            notice,
            **values,
            evidence=[
                {"field": "summary", "excerpt": "캠프 안내"},
                {"field": field, "excerpt": value},
            ],
        ),
        notice,
    )
    if field == "location":
        assert result.location is None
    else:
        assert result.action == value
        assert result.action_requirement == "unknown"


@pytest.mark.parametrize("quote_style", ["text_short", "text_full", "document_full"])
@pytest.mark.parametrize(
    ("source", "action", "requirement", "expected"),
    [
        ("희망자는 신청 가능, 당첨자는 반드시 등록", "신청", "optional", "optional"),
        ("희망자는 신청 가능, 당첨자는 반드시 등록", "등록", "required", "required"),
        (
            "온라인 접수 가능, 현장 참여자는 반드시 방문 접수",
            "온라인 접수",
            "optional",
            "optional",
        ),
        (
            "신청자는 반드시 온라인 접수, 신청자는 반드시 등록",
            "온라인 접수",
            "required",
            "required",
        ),
        (
            "온라인 접수 필수, 현장 참여자는 방문 접수를 반드시 하지 않아도 됩니다",
            "온라인 접수",
            "required",
            "required",
        ),
        (
            "신청자는 온라인 신청, 신청자는 현장 등록을 모두 해야 합니다",
            "온라인 신청",
            "optional",
            "unknown",
        ),
        (
            "신청자는 현장 등록, 신청자는 온라인 신청을 모두 해야 합니다",
            "현장 등록",
            "optional",
            "unknown",
        ),
    ],
)
def test_same_action_role_does_not_borrow_another_actor_or_route_requirement(
    source: str, action: str, requirement: str, expected: str, quote_style: str
) -> None:
    notice = _notice(f"캠프 안내\n{source}" if quote_style != "document_full" else "캠프 안내")
    quote: dict[str, Any] = {
        "field": "action",
        "excerpt": action if quote_style == "text_short" else source,
    }
    if quote_style == "document_full":
        quote.update(source_type="document", source_id="media_1", page=1)
    result = ground_summary(
        _summary(
            notice,
            action=action,
            action_requirement=requirement,
            evidence=[{"field": "summary", "excerpt": "캠프 안내"}, quote],
        ),
        notice,
        media_sources=(MediaSource("media_1", "document"),)
        if quote_style == "document_full"
        else (),
    )
    assert result.action == action
    assert result.action_requirement == expected


@pytest.mark.parametrize("quote_style", ["text_short", "text_full", "document_full"])
@pytest.mark.parametrize(
    "source",
    [
        "온라인 신청은 필수 사항이 아닙니다",
        "온라인 신청을 반드시 해야 하는 것은 아닙니다",
        "온라인 신청을 반드시 하지 않아도 됩니다",
    ],
)
@pytest.mark.parametrize("requirement", ["optional", "required"])
def test_negated_obligation_is_not_a_positive_requirement(
    source: str, requirement: str, quote_style: str
) -> None:
    action = "온라인 신청"
    notice = _notice(f"캠프 안내\n{source}" if quote_style != "document_full" else "캠프 안내")
    quote: dict[str, Any] = {
        "field": "action",
        "excerpt": action if quote_style == "text_short" else source,
    }
    if quote_style == "document_full":
        quote.update(source_type="document", source_id="media_1", page=1)
    result = ground_summary(
        _summary(
            notice,
            action=action,
            action_requirement=requirement,
            evidence=[{"field": "summary", "excerpt": "캠프 안내"}, quote],
        ),
        notice,
        media_sources=(MediaSource("media_1", "document"),)
        if quote_style == "document_full"
        else (),
    )
    assert result.action == action
    assert result.action_requirement == ("optional" if requirement == "optional" else "unknown")


@pytest.mark.parametrize("quote_style", ["text_short", "text_full", "document_full"])
@pytest.mark.parametrize(
    ("source", "place", "keep"),
    [
        ("월계공원에서 개최하며 신청은 온라인 접수", "월계공원", True),
        ("월계공원에서 행사 진행, 온라인 신청 가능", "월계공원", True),
        ("온라인 신청 가능, 월계공원에서 행사 진행", "월계공원", True),
        ("노원구청에서 온라인 신청", "노원구청", False),
        ("장소: 노원구청에서 온라인 신청", "노원구청", False),
    ],
)
def test_physical_venue_is_separate_from_an_online_application_route(
    source: str, place: str, keep: bool, quote_style: str
) -> None:
    notice = _notice(f"캠프 안내\n{source}" if quote_style != "document_full" else "캠프 안내")
    quote: dict[str, Any] = {
        "field": "location",
        "excerpt": place if quote_style == "text_short" else source,
    }
    if quote_style == "document_full":
        quote.update(source_type="document", source_id="media_1", page=1)
    result = ground_summary(
        _summary(
            notice,
            location=place,
            evidence=[{"field": "summary", "excerpt": "캠프 안내"}, quote],
        ),
        notice,
        media_sources=(MediaSource("media_1", "document"),)
        if quote_style == "document_full"
        else (),
    )
    assert result.location == (place if keep else None)
