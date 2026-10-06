"""File-only output keeps model facts while schema and reference checks still run."""

import json
from copy import deepcopy
from typing import Any

import pytest
from test_gemini_multimodal import _media, _prepared

from pipeline.transform import summarize as summarize_module
from pipeline.transform.grounding import REVIEW_NOTE, unknown_summary
from pipeline.transform.summary_schema import SummaryValidationError


def _file_output(kind: str = "image") -> dict[str, Any]:
    """Use valid shape with paraphrases that literal content grounding would drop."""
    data = unknown_summary(_prepared().notice, has_media=True).model_dump()
    data.update(
        category="mixed",
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
            )
        ],
    )
    return data


def _mock_responses(
    monkeypatch: pytest.MonkeyPatch, *responses: str | dict[str, Any]
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []

    def generate(**kwargs: Any) -> str:
        index = len(requests)
        requests.append(deepcopy(kwargs))
        assert index < len(responses), "file corrections share the single retry budget"
        response = responses[index]
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    return requests


def _facts(output: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in output.items() if key not in {"evidence", "uncertainties"}}


@pytest.mark.parametrize("kind", ("document", "image"))
def test_file_only_preserves_place_booking_multiple_programs_and_returned_status(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    prepared = _prepared("", _media(kind))
    original_blocks = deepcopy(prepared.blocks)
    output = _file_output(kind)
    requests = _mock_responses(monkeypatch, output)

    result = summarize_module.summarize_prepared_notice(prepared, api_key="mock-key")

    assert len(requests) == 1
    assert _facts(result.summary.model_dump()) == _facts(output)
    assert result.summary.uncertainties == []
    assert all(item.verification == "file_reference_only" for item in result.summary.evidence)
    assert prepared.blocks == original_blocks


@pytest.mark.parametrize(
    ("kind", "missing_key"),
    (("document", "source_id"), ("document", "page"), ("image", "source_id")),
)
def test_missing_file_reference_retries_once_and_accepts_corrected_reference(
    monkeypatch: pytest.MonkeyPatch, kind: str, missing_key: str
) -> None:
    prepared = _prepared("", _media(kind))
    valid = _file_output(kind)
    missing = deepcopy(valid)
    for item in missing["evidence"]:
        del item[missing_key]
    requests = _mock_responses(monkeypatch, missing, valid)

    result = summarize_module.summarize_prepared_notice(prepared, api_key="mock-key")

    assert len(requests) == 2
    assert (
        requests[1]["notice_text"][: len(requests[0]["notice_text"])] == requests[0]["notice_text"]
    )
    assert [block for block in requests[1]["notice_text"] if block["type"] == kind] == [
        _media(kind)
    ]
    assert _facts(result.summary.model_dump()) == _facts(valid)
    assert result.summary.uncertainties == []
    assert all(item.verification == "file_reference_only" for item in result.summary.evidence)


@pytest.mark.parametrize(
    ("kind", "reference_error"),
    (
        ("document", "missing_id"),
        ("document", "missing_page"),
        ("image", "missing_id"),
        ("document", "wrong_id"),
        ("image", "wrong_id"),
        ("image", "wrong_type"),
        ("image", "text_type"),
    ),
)
def test_repeated_reference_problem_preserves_facts_and_marks_unverified_evidence(
    monkeypatch: pytest.MonkeyPatch, kind: str, reference_error: str
) -> None:
    output = _file_output(kind)
    for item in output["evidence"]:
        if reference_error == "missing_id":
            item.pop("source_id")
        elif reference_error == "missing_page":
            item.pop("page")
        elif reference_error == "wrong_id":
            item["source_id"] = "media_99"
        elif reference_error == "text_type":
            item.update(source_type="text", source_id=None, page=None)
        else:
            item.update(source_type="document", page=1)
        item["verification"] = "file_reference_only"
    requests = _mock_responses(monkeypatch, output, output)

    result = summarize_module.summarize_prepared_notice(
        _prepared("", _media(kind)), api_key="mock-key"
    )

    assert len(requests) == 2
    assert _facts(result.summary.model_dump()) == _facts(output)
    assert result.summary.uncertainties == [REVIEW_NOTE]
    assert len(result.summary.evidence) == len(output["evidence"])
    assert all(item.verification is None for item in result.summary.evidence)


def test_reference_verification_is_per_quote_when_only_one_reference_is_wrong(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _file_output()
    output["evidence"][6]["source_id"] = "media_99"
    output["evidence"][6]["verification"] = "file_reference_only"
    requests = _mock_responses(monkeypatch, output, output)

    result = summarize_module.summarize_prepared_notice(
        _prepared("", _media("image")), api_key="mock-key"
    )

    assert len(requests) == 2
    assert _facts(result.summary.model_dump()) == _facts(output)
    assert result.summary.uncertainties == [REVIEW_NOTE]
    for item in result.summary.evidence:
        expected = None if item.field == "location" else "file_reference_only"
        assert item.verification == expected


@pytest.mark.parametrize("kind", ("document", "image"))
def test_missing_all_evidence_preserves_file_only_facts_after_retry(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    output = _file_output(kind)
    output["evidence"] = []
    requests = _mock_responses(monkeypatch, output, output)

    result = summarize_module.summarize_prepared_notice(
        _prepared("", _media(kind)), api_key="mock-key"
    )

    assert len(requests) == 2
    assert _facts(result.summary.model_dump()) == _facts(output)
    assert result.summary.evidence == []
    assert result.summary.uncertainties == [REVIEW_NOTE]


def test_pdf_page_reference_does_not_claim_to_validate_binary_page_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _file_output("document")
    for item in output["evidence"]:
        item["page"] = 999_999
    requests = _mock_responses(monkeypatch, output)

    result = summarize_module.summarize_prepared_notice(
        _prepared("", _media("document")), api_key="mock-key"
    )

    assert len(requests) == 1
    assert all(item.page == 999_999 for item in result.summary.evidence)
    assert all(item.verification == "file_reference_only" for item in result.summary.evidence)


@pytest.mark.parametrize(
    ("field", "invalid", "repaired"),
    (
        ("start_date", "2026/10/10", "2026-10-10"),
        ("end_date", "2026-02-30", "2026-10-10"),
        ("start_time", "오전 10시", "10:00"),
        ("end_time", "24:00", "11:00"),
        ("end_date", "2026-10-09", "2026-10-10"),
        ("end_time", "09:00", "11:00"),
    ),
)
def test_date_format_and_reversed_order_still_use_the_single_retry(
    monkeypatch: pytest.MonkeyPatch, field: str, invalid: str, repaired: str
) -> None:
    first = _file_output()
    first["dates"][0][field] = invalid
    second = deepcopy(first)
    second["dates"][0][field] = repaired
    requests = _mock_responses(monkeypatch, first, second)

    result = summarize_module.summarize_prepared_notice(
        _prepared("", _media("image")), api_key="mock-key"
    )

    assert len(requests) == 2
    assert getattr(result.summary.dates[0], field) == repaired
    assert result.summary.location == first["location"]
    assert result.summary.notes == first["notes"]
    assert result.summary.uncertainties == []


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("start_date", "2026/10/10"),
        ("end_date", "2026-10-09"),
        ("start_time", "오전 10시"),
        ("end_time", "09:00"),
    ),
)
def test_repeated_date_error_repairs_only_invalid_value_and_preserves_other_facts(
    monkeypatch: pytest.MonkeyPatch, field: str, invalid: str
) -> None:
    output = _file_output()
    output["dates"][0][field] = invalid
    requests = _mock_responses(monkeypatch, output, output)

    result = summarize_module.summarize_prepared_notice(
        _prepared("", _media("image")), api_key="mock-key"
    )

    assert len(requests) == 2
    assert len(result.summary.dates) == 1
    assert getattr(result.summary.dates[0], field) is None
    for key, value in output["dates"][0].items():
        if field == "end_date" and key == "end_time":
            assert result.summary.dates[0].end_time is None
            continue
        if key != field:
            assert getattr(result.summary.dates[0], key) == value
    assert result.summary.location == output["location"]
    assert result.summary.action == output["action"]
    assert result.summary.notes == output["notes"]
    assert result.summary.uncertainties == [REVIEW_NOTE]
    if field in {"end_date", "end_time"}:
        assert result.summary.status == "unknown"


def test_overnight_program_is_not_a_reversed_time_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _file_output()
    output["dates"][0].update(
        start_date="2026-10-10", end_date="2026-10-11", start_time="22:00", end_time="01:00"
    )
    requests = _mock_responses(monkeypatch, output)

    result = summarize_module.summarize_prepared_notice(
        _prepared("", _media("image")), api_key="mock-key"
    )

    assert len(requests) == 1
    assert result.summary.dates[0].end_date == "2026-10-11"
    assert result.summary.dates[0].end_time == "01:00"


@pytest.mark.parametrize("readable", ("body", "attachment"))
def test_mixed_readable_text_preserves_uncertain_claims_with_review(
    monkeypatch: pytest.MonkeyPatch, readable: str
) -> None:
    body = "주민 문화 프로그램 안내" if readable == "body" else ""
    prepared = _prepared(body, _media("image"), attachment_text=readable == "attachment")
    output = _file_output()
    requests = _mock_responses(monkeypatch, output)

    result = summarize_module.summarize_prepared_notice(prepared, api_key="mock-key")

    assert len(requests) == 1
    assert result.summary.location == output["location"]
    assert result.summary.notes == output["notes"]
    assert result.summary.uncertainties == [REVIEW_NOTE]


def test_text_only_preserves_a_claim_when_quote_does_not_support_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared("주민 문화 프로그램 안내\n장소: 월계공원")
    output = unknown_summary(prepared.notice).model_dump()
    output.update(
        category="event",
        summary="주민 문화 프로그램 안내",
        location="월계문화센터",
        uncertainties=[],
        evidence=[
            {"field": "summary", "excerpt": "주민 문화 프로그램 안내"},
            {"field": "location", "excerpt": "장소: 월계공원"},
        ],
    )
    requests = _mock_responses(monkeypatch, output)

    result = summarize_module.summarize_prepared_notice(prepared, api_key="mock-key")

    assert len(requests) == 1
    assert result.summary.summary == output["summary"]
    assert result.summary.location == output["location"]
    assert result.summary.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("kind", ("document", "image"))
def test_file_reference_retry_preserves_claims_it_replaces_with_empty_values(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    first = _file_output(kind)
    for item in first["evidence"]:
        item["source_id"] = None
    retry = deepcopy(first)
    for field in ("audience", "action", "location", "changed_details", "status_detail"):
        retry[field] = None
    for field in ("dates", "notes", "topics"):
        retry[field] = []
    retry["audience_scope"] = "unknown"
    retry["action_requirement"] = "unknown"
    retry["summary"] = REVIEW_NOTE
    retry["evidence"] = []
    requests = _mock_responses(monkeypatch, first, retry)

    result = summarize_module.summarize_prepared_notice(
        _prepared("", _media(kind)), api_key="mock-key"
    ).summary

    assert len(requests) == 2
    for field in ("summary", "audience", "action", "location", "dates", "notes", "topics"):
        assert result.model_dump()[field] == first[field]
    assert result.audience_scope == "general"
    assert result.action_requirement == "required"
    assert result.uncertainties == [REVIEW_NOTE]
    assert all(item.verification is None for item in result.evidence)


def test_file_reference_retry_accepts_a_nonempty_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _file_output()
    for item in first["evidence"]:
        item["source_id"] = None
    retry = _file_output() | {"location": "월계공원"}
    requests = _mock_responses(monkeypatch, first, retry)

    result = summarize_module.summarize_prepared_notice(
        _prepared("", _media("image")), api_key="mock-key"
    ).summary

    assert len(requests) == 2
    assert result.location == "월계공원"
    assert result.uncertainties == []


@pytest.mark.parametrize("equal_count", (False, True))
def test_file_reference_retry_preserves_omitted_array_items_and_accepts_date_correction(
    monkeypatch: pytest.MonkeyPatch,
    equal_count: bool,
) -> None:
    first = _file_output("document")
    first["dates"].append(
        first["dates"][0]
        | {"label": "다음 문화 프로그램", "start_date": "2026-10-11", "end_date": "2026-10-11"}
    )
    retry = deepcopy(first)
    retry["dates"] = [retry["dates"][0] | {"end_time": "12:00"}]
    retry["notes"] = retry["notes"][:1]
    retry["topics"] = retry["topics"][:1]
    if equal_count:
        retry["dates"].append(retry["dates"][0] | {"label": "새 문화 프로그램"})
        retry["notes"].append("새 주의사항")
        retry["topics"].append(retry["topics"][0] | {"title": "새 문화 교실"})
    for item in first["evidence"]:
        item["source_id"] = None
    requests = _mock_responses(monkeypatch, first, retry)

    result = summarize_module.summarize_prepared_notice(
        _prepared("", _media("document")), api_key="mock-key"
    ).summary

    assert len(requests) == 2
    assert len(result.dates) == (3 if equal_count else 2)
    assert result.dates[0].end_time == "12:00"
    assert result.dates[1].start_date == "2026-10-11"
    assert result.notes == first["notes"] + (["새 주의사항"] if equal_count else [])
    assert [item.title for item in result.topics] == [item["title"] for item in first["topics"]] + (
        ["새 문화 교실"] if equal_count else []
    )
    assert REVIEW_NOTE in result.uncertainties


def test_broken_json_on_a_reference_retry_does_not_fall_back_to_the_first_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _file_output()
    first["evidence"] = []
    requests = _mock_responses(monkeypatch, first, "not-json")

    with pytest.raises(SummaryValidationError):
        summarize_module.summarize_prepared_notice(
            _prepared("", _media("image")), api_key="mock-key"
        )
    assert len(requests) == 2


@pytest.mark.parametrize("kind", ("document", "image"))
def test_broken_json_twice_is_processing_failure_for_file_only(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    marker = "MOCK_PRIVATE_BROKEN_FILE_OUTPUT"
    requests = _mock_responses(monkeypatch, "invalid " + marker, "invalid " + marker)

    with pytest.raises(SummaryValidationError, match="after one retry") as error:
        summarize_module.summarize_prepared_notice(_prepared("", _media(kind)), api_key="mock-key")

    assert len(requests) == 2
    assert marker not in str(error.value)


def test_reference_retry_returning_broken_json_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _file_output("document")
    for item in first["evidence"]:
        item.pop("page")
    requests = _mock_responses(monkeypatch, first, "not-json")

    with pytest.raises(SummaryValidationError, match="after one retry"):
        summarize_module.summarize_prepared_notice(
            _prepared("", _media("document")), api_key="mock-key"
        )

    assert len(requests) == 2
