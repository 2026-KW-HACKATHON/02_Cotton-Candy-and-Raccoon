"""Missing explicit text conditions share the existing one-response retry budget."""

import json
from copy import deepcopy
from typing import Any

import pytest
from test_gemini_multimodal import _media, _prepared

from pipeline.transform import summarize as summarize_module
from pipeline.transform.gemini_client import GeminiRequestError
from pipeline.transform.grounding import REVIEW_NOTE, unknown_summary
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_schema import SummaryValidationError

TITLE = "청소년 캠프 참가자 모집 안내"
COST = "참가비용 : 30,000원"
SUPPORT = "사회적배려대상 본인부담금 전액 노원구 지원"
SELECTION = "모집정원 초과 신청 시, 전산추첨을 통하여 참가자를 선정함"
CONDITIONS = (COST, SUPPORT, SELECTION)
PRIVATE_MARKER = "MOCK_PRIVATE_NOTICE_COVERAGE_123"


def _notice(*conditions: str) -> NoticeInput:
    return NoticeInput.model_validate(
        {
            "title": TITLE,
            "body_text": "\n".join((TITLE, "모집대상 : 노원구 거주 초등학생", *conditions)),
            "publisher": "노원구청",
            "reference_datetime": "2026-10-04T12:00:00+09:00",
        }
    )


def _output(notice: NoticeInput, *notes: str) -> dict[str, Any]:
    data = unknown_summary(notice).model_dump()
    data.update(
        category="application",
        summary=TITLE,
        publisher=notice.publisher,
        audience="노원구 거주 초등학생",
        audience_scope="conditional",
        notice_update="new",
        notes=list(notes),
        uncertainties=[],
        evidence=[
            {"field": "summary", "excerpt": TITLE},
            {"field": "audience", "excerpt": "모집대상 : 노원구 거주 초등학생"},
            *({"field": "notes", "excerpt": note} for note in notes),
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
        assert index < len(responses), "summary correction must not exceed the shared retry budget"
        response = responses[index]
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    return requests


@pytest.mark.parametrize("missing", CONDITIONS, ids=("cost", "support", "selection"))
def test_explicit_condition_omission_retries_once_and_returns_repaired_notes(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    notice = _notice(*CONDITIONS)
    initial_notes = tuple(condition for condition in CONDITIONS if condition != missing)
    requests = _mock_responses(
        monkeypatch, _output(notice, *initial_notes), _output(notice, *CONDITIONS)
    )

    result = summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 2
    retry_feedback = requests[1]["notice_text"][len(requests[0]["notice_text"]) :]
    assert missing in retry_feedback
    assert len(result.notes) == len(CONDITIONS)
    assert set(result.notes) == set(CONDITIONS)
    assert result.notes[: len(initial_notes)] == list(initial_notes)
    assert result.uncertainties == []
    assert result.summary == TITLE
    assert result.audience == "노원구 거주 초등학생"
    assert all(
        any(
            item.field == "notes" and item.excerpt == note and item.verification == "text_matched"
            for item in result.evidence
        )
        for note in CONDITIONS
    )


def test_repeated_omission_preserves_valid_information_and_marks_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notice = _notice(*CONDITIONS)
    incomplete = _output(notice, COST)
    requests = _mock_responses(monkeypatch, incomplete, incomplete)

    result = summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 2
    assert result.category == "application"
    assert result.summary == TITLE
    assert result.publisher == "노원구청"
    assert result.audience == "노원구 거주 초등학생"
    assert result.notes == [COST]
    assert result.uncertainties == [REVIEW_NOTE]
    assert any(item.field == "notes" and item.excerpt == COST for item in result.evidence)


def test_evidence_without_a_note_does_not_hide_an_omitted_condition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notice = _notice(COST)
    incomplete = _output(notice)
    incomplete["evidence"].append({"field": "notes", "excerpt": notice.body_text})
    requests = _mock_responses(monkeypatch, incomplete, incomplete)

    result = summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 2
    assert result.notes == []
    assert result.uncertainties == [REVIEW_NOTE]


def test_recoverable_shape_error_and_omission_share_the_first_retry_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notice = _notice(*CONDITIONS)
    invalid = _output(notice)
    invalid["summary"] = "가" * 41
    requests = _mock_responses(monkeypatch, invalid, _output(notice, *CONDITIONS))

    result = summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 2
    retry_feedback = requests[1]["notice_text"][len(requests[0]["notice_text"]) :]
    assert "summary: 40자 제한 초과" in retry_feedback
    assert all(condition in retry_feedback for condition in CONDITIONS)
    assert result.summary == TITLE
    assert result.notes == list(CONDITIONS)
    assert result.uncertainties == []


def test_shape_retry_does_not_grant_a_third_call_for_remaining_omissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notice = _notice(COST)
    invalid = _output(notice)
    invalid["summary"] = "가" * 41
    requests = _mock_responses(monkeypatch, invalid, _output(notice))

    result = summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 2
    assert result.summary == TITLE
    assert result.notes == []
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("bad_response", ("invalid-json", "missing-required-field"))
def test_unrecoverable_retry_remains_failure_without_exposing_source_text(
    monkeypatch: pytest.MonkeyPatch, bad_response: str
) -> None:
    notice = _notice(COST, PRIVATE_MARKER)
    if bad_response == "invalid-json":
        response: str | dict[str, Any] = "not-json " + PRIVATE_MARKER
    else:
        response = _output(notice)
        del response["action"]
        response["evidence"].append({"field": "notes", "excerpt": PRIVATE_MARKER})
    requests = _mock_responses(monkeypatch, response, response)

    with pytest.raises(SummaryValidationError) as failure:
        summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 2
    assert failure.value.reason_code == "response_validation_failed"
    assert PRIVATE_MARKER not in str(failure.value)
    assert COST not in str(failure.value)


@pytest.mark.parametrize("bad_retry", ("invalid-json", "missing-required-field"))
def test_unrecoverable_coverage_retry_does_not_return_the_first_incomplete_summary(
    monkeypatch: pytest.MonkeyPatch, bad_retry: str
) -> None:
    notice = _notice(COST, PRIVATE_MARKER)
    if bad_retry == "invalid-json":
        retry: str | dict[str, Any] = "not-json " + PRIVATE_MARKER
    else:
        retry = _output(notice, COST)
        del retry["action"]
    requests = _mock_responses(monkeypatch, _output(notice), retry)

    with pytest.raises(SummaryValidationError) as failure:
        summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 2
    assert failure.value.reason_code == "response_validation_failed"
    assert PRIVATE_MARKER not in str(failure.value)
    assert COST not in str(failure.value)


@pytest.mark.parametrize("reason_code", ("api_error", "input_too_large"))
def test_api_failure_during_coverage_retry_does_not_return_partial_success(
    monkeypatch: pytest.MonkeyPatch, reason_code: str
) -> None:
    notice = _notice(COST)
    failure = GeminiRequestError("Gemini request failed.", reason_code=reason_code)
    requests = []

    def generate(**kwargs: Any) -> str:
        requests.append(deepcopy(kwargs))
        if len(requests) == 1:
            return json.dumps(_output(notice), ensure_ascii=False)
        raise failure

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)

    with pytest.raises(GeminiRequestError) as caught:
        summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 2
    assert caught.value is failure
    assert caught.value.reason_code == reason_code


def test_coverage_retry_preserves_all_original_pdf_and_image_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared(
        _notice(*CONDITIONS).body_text,
        _media("document", b"%PDF-original"),
        _media("image", b"original image"),
    )
    original_blocks = deepcopy(prepared.blocks)
    requests = _mock_responses(
        monkeypatch, _output(prepared.notice), _output(prepared.notice, *CONDITIONS)
    )

    result = summarize_module.summarize_prepared_notice(prepared, api_key="test-key")

    assert len(requests) == 2
    assert prepared.blocks == original_blocks
    assert prepared.calls == ["to_gemini_input"]
    first_blocks = requests[0]["notice_text"]
    retry_blocks = requests[1]["notice_text"]
    assert retry_blocks[: len(first_blocks)] == first_blocks
    assert [block for block in retry_blocks if block["type"] != "text"] == original_blocks[1:]
    assert result.summary.notes == list(CONDITIONS)


@pytest.mark.parametrize("media_kind", ("document", "image"))
def test_files_without_extracted_text_do_not_trigger_text_coverage_retry(
    monkeypatch: pytest.MonkeyPatch, media_kind: str
) -> None:
    prepared = _prepared("", _media(media_kind))
    data = unknown_summary(prepared.notice, has_media=True).model_dump()
    data.update(
        category="event",
        summary="행사 안내",
        uncertainties=[],
        evidence=[
            {
                "field": "summary",
                "excerpt": "행사 안내\n" + COST,
                "source_type": media_kind,
                "source_id": "media_1",
                "page": 1 if media_kind == "document" else None,
            }
        ],
    )
    requests = _mock_responses(monkeypatch, data)

    result = summarize_module.summarize_prepared_notice(prepared, api_key="test-key")

    assert len(requests) == 1
    assert result.summary.summary == "행사 안내"
    assert result.summary.notes == []
    assert result.summary.uncertainties == []
    assert result.summary.evidence[0].verification == "file_reference_only"


def test_complete_text_conditions_do_not_spend_a_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notice = _notice(*CONDITIONS)
    requests = _mock_responses(monkeypatch, _output(notice, *CONDITIONS))

    result = summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 1
    assert result.notes == list(CONDITIONS)
    assert result.uncertainties == []


def test_condition_in_extracted_hwp_text_uses_the_same_coverage_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notice = NoticeInput.model_validate(
        _notice().model_dump() | {"attachments": [{"name": "캠프안내.hwp", "text": COST}]}
    )
    requests = _mock_responses(monkeypatch, _output(notice), _output(notice, COST))

    result = summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 2
    assert result.notes == [COST]
    assert result.uncertainties == []
    assert any(
        item.field == "notes" and item.excerpt == COST and item.verification == "text_matched"
        for item in result.evidence
    )


def test_note_rejected_by_grounding_does_not_count_as_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notice = _notice(COST)
    incorrect = _output(notice, "참가비용 : 3,000원")
    requests = _mock_responses(monkeypatch, incorrect, _output(notice, COST))

    result = summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 2
    assert result.notes == [COST]
    assert not any(item.excerpt == "참가비용 : 3,000원" for item in result.evidence)


def test_notes_retry_cannot_remove_verified_audience_location_dates_or_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = "신청기간 : 2026-10-01~2026-10-10"
    place = "장소 : 강원도 고성군 일원"
    notice = _notice(COST, schedule, place)
    initial = _output(notice)
    initial.update(
        location="강원도 고성군 일원",
        status="open",
        dates=[
            {
                "kind": "application",
                "label": "신청기간",
                "text": None,
                "start_date": "2026-10-01",
                "end_date": "2026-10-10",
                "start_time": None,
                "end_time": None,
            }
        ],
    )
    initial["evidence"].extend(
        [
            {"field": "location", "excerpt": place},
            {"field": "dates", "excerpt": schedule},
        ]
    )
    correction = _output(notice, COST)
    correction.update(audience=None, audience_scope="unknown", notice_update="unknown")
    correction["evidence"] = [
        item for item in correction["evidence"] if item["field"] != "audience"
    ]
    requests = _mock_responses(monkeypatch, initial, correction)

    result = summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 2
    assert result.audience == "노원구 거주 초등학생"
    assert result.audience_scope == "conditional"
    assert result.location == "강원도 고성군 일원"
    assert result.status == "open"
    assert result.notice_update == "new"
    assert [entry.model_dump() for entry in result.dates] == initial["dates"]
    assert result.notes == [COST]
    assert {"audience", "location", "dates"}.issubset({item.field for item in result.evidence})


def test_notes_retry_keeps_an_existing_cancellation_rule_when_adding_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restriction = "선정된 학생은 임의로 취소 또는 포기할 수 없습니다."
    notice = _notice(COST, restriction)
    requests = _mock_responses(monkeypatch, _output(notice, restriction), _output(notice, COST))

    result = summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 2
    assert result.notes == [restriction, COST]
    assert result.uncertainties == []
    assert all(
        any(item.field == "notes" and note in item.excerpt for item in result.evidence)
        for note in result.notes
    )


def test_notes_union_overflow_preserves_first_conditions_and_marks_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_notes = [
        "신청 시 보호자 동의서 제출",
        "선정된 학생은 임의로 취소 또는 포기할 수 없습니다.",
        "모집대상 외에는 참여할 수 없습니다.",
        "개인 물품은 직접 준비해야 합니다.",
        "집결 시간 이후에는 참여할 수 없습니다.",
    ]
    notice = _notice(COST, *original_notes)
    requests = _mock_responses(monkeypatch, _output(notice, *original_notes), _output(notice, COST))

    result = summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 2
    assert result.notes == original_notes
    assert len(result.notes) == 5
    assert result.uncertainties == [REVIEW_NOTE]
    assert result.summary == TITLE
    assert result.audience == "노원구 거주 초등학생"


@pytest.mark.parametrize("separator", [", ", ". ", "\n", ". 다만 "])
def test_note_limit_keeps_a_returned_cancellation_exception_with_its_restriction(
    monkeypatch: pytest.MonkeyPatch, separator: str
) -> None:
    restriction = "선정된 학생은 취소할 수 없다"
    exception = (
        "사전 연락 후 처리할 수 있다"
        if "다만" in separator
        else "불가피하면 사전 연락 후 처리할 수 있다"
    )
    rule = restriction + separator + exception
    standalone = [
        "신청 시 보호자 동의서 제출",
        "개인 물품은 직접 준비해야 합니다",
        "집결 시간을 확인해야 합니다",
        "참가자는 안내 문자를 확인해야 합니다",
    ]
    notice = _notice(COST, rule, *standalone)
    first = _output(notice, *standalone, restriction)
    correction = _output(notice, COST, restriction, exception)
    requests = _mock_responses(monkeypatch, first, correction)

    result = summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 2
    assert restriction in result.notes
    assert exception in result.notes
    assert result.notes.index(exception) == result.notes.index(restriction) + 1
    assert len(result.notes) == 5
    assert result.uncertainties == [REVIEW_NOTE]
    assert result.summary == TITLE
    assert result.audience == "노원구 거주 초등학생"
    assert all(any(note in item.excerpt for item in result.evidence) for note in result.notes)


def test_a_rule_larger_than_the_note_limit_is_omitted_as_a_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule_notes = [
        "선정된 학생은 취소할 수 없다",
        "불가피하면 사전 연락 후 처리할 수 있다",
        "사전 연락은 담당 부서로 해야 합니다",
        "담당 부서의 승인 이후에만 처리합니다",
        "처리 결과는 개별 안내합니다",
        "연락 없이 불참하면 다음 행사 참여가 제한됩니다",
    ]
    rule = ", ".join(rule_notes)
    notice = _notice(COST, rule)
    first = _output(notice, *rule_notes[:5])
    correction = _output(notice, COST, *rule_notes[4:])
    requests = _mock_responses(monkeypatch, first, correction)

    result = summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 2
    assert result.notes == [COST]
    assert result.uncertainties == [REVIEW_NOTE]
    assert result.summary == TITLE
    assert result.audience == "노원구 거주 초등학생"
    assert not any(
        item.field == "notes" and any(note in item.excerpt for note in rule_notes)
        for item in result.evidence
    )


@pytest.mark.parametrize("condition", ["수급자 수강료 전액 면제", "참가비 없음", "참가비 없음."])
@pytest.mark.parametrize("repaired", [True, False], ids=["repaired", "still-missing"])
def test_exemption_and_no_fee_conditions_use_the_same_omission_retry(
    monkeypatch: pytest.MonkeyPatch, condition: str, repaired: bool
) -> None:
    notice = _notice(condition)
    first = _output(notice)
    retry = _output(notice, condition) if repaired else first
    requests = _mock_responses(monkeypatch, first, retry)

    result = summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 2
    retry_feedback = requests[1]["notice_text"][len(requests[0]["notice_text"]) :]
    assert condition in retry_feedback
    assert result.notes == ([condition] if repaired else [])
    assert result.uncertainties == ([] if repaired else [REVIEW_NOTE])
    assert result.summary == TITLE
    assert result.audience == "노원구 거주 초등학생"


@pytest.mark.parametrize("family", ("다문화가정", "한부모가정", "맞벌이가정"))
@pytest.mark.parametrize("repaired", (True, False), ids=("repaired", "still-missing"))
def test_family_audience_title_does_not_suppress_explicit_cost_coverage(
    monkeypatch: pytest.MonkeyPatch, family: str, repaired: bool
) -> None:
    title = f"{family} 캠프 참가자 모집"
    cost = "참가비: 30,000원"
    notice = NoticeInput.model_validate(
        {
            "title": title,
            "body_text": f"{title}\n{cost}",
            "reference_datetime": "2026-10-04T12:00:00+09:00",
        }
    )
    initial = unknown_summary(notice).model_dump()
    initial.update(
        category="application",
        summary=title,
        notice_update="new",
        uncertainties=[],
        evidence=[{"field": "summary", "excerpt": title}],
    )
    correction = deepcopy(initial)
    if repaired:
        correction["notes"] = [cost]
        correction["evidence"].append({"field": "notes", "excerpt": cost})
    requests = _mock_responses(monkeypatch, initial, correction)

    result = summarize_module.summarize_notice(notice, api_key="test-key")

    assert len(requests) == 2
    retry_feedback = requests[1]["notice_text"][len(requests[0]["notice_text"]) :]
    assert cost in retry_feedback
    assert result.category == "application"
    assert result.summary == title
    assert result.notes == ([cost] if repaired else [])
    assert result.uncertainties == ([] if repaired else [REVIEW_NOTE])
