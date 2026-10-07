"""A single correction preserves usable facts without hiding terminal failures."""

import json
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any

import pytest
from test_gemini_multimodal import PreparedInput, _media

from pipeline.storage.summary_record import SummaryMetadata, build_summary_record
from pipeline.transform import summarize as summarize_module
from pipeline.transform.gemini_client import GeminiRequestError
from pipeline.transform.grounding import REVIEW_NOTE, unknown_summary
from pipeline.transform.notice_input import NoticeInput, render_notice_input
from pipeline.transform.prepared_summary import PreparedSummaryResult
from pipeline.transform.summary_schema import NoticeSummary, SummaryValidationError

TITLE = "청소년 캠프 참가자 모집 안내"
COST = "참가비: 30,000원"
SCHEDULE = "신청: 2026-10-02~2026-10-06"
ACTION = "현장 방문 신청"


def _notice() -> NoticeInput:
    return NoticeInput.model_validate(
        {
            "title": TITLE,
            "body_text": "\n".join(
                (
                    TITLE,
                    "대상: 노원구 거주 초등학생",
                    "신청방법: " + ACTION,
                    "장소: 월계문화센터",
                    SCHEDULE,
                    COST,
                )
            ),
            "publisher": "노원구청",
            "reference_datetime": "2026-10-04T12:00:00+09:00",
        }
    )


def _output(notice: NoticeInput, *, include_cost: bool = True) -> dict[str, Any]:
    data = unknown_summary(notice).model_dump()
    data.update(
        category="application",
        summary=TITLE,
        audience="노원구 거주 초등학생",
        audience_scope="conditional",
        action=ACTION,
        action_requirement="optional",
        location="월계문화센터",
        dates=[
            {
                "kind": "application",
                "label": "신청",
                "text": "2026-10-02~2026-10-06",
                "start_date": "2026-10-02",
                "end_date": "2026-10-06",
                "start_time": None,
                "end_time": None,
            }
        ],
        status="open",
        notice_update="new",
        notes=[COST] if include_cost else [],
        uncertainties=[],
        card_summaries={
            "audience": "노원구에 거주하는 초등학생이 대상이에요.",
            "deadline": "신청 기간은 2026-10-02부터 2026-10-06까지예요.",
            "action": "현장을 방문하여 신청해 주세요.",
            "notes": "참가비는 30,000원이에요." if include_cost else None,
        },
        evidence=[
            {"field": "summary", "excerpt": TITLE},
            {"field": "audience", "excerpt": "대상: 노원구 거주 초등학생"},
            {"field": "action", "excerpt": "신청방법: " + ACTION},
            {"field": "location", "excerpt": "장소: 월계문화센터"},
            {"field": "dates", "excerpt": SCHEDULE},
            *([{"field": "notes", "excerpt": COST}] if include_cost else []),
        ],
    )
    return data


def _prepared(notice: NoticeInput) -> PreparedInput:
    return PreparedInput(
        17,
        notice,
        [
            {"type": "text", "text": render_notice_input(notice)},
            _media("document", b"original-pdf-bytes"),
            _media("image", b"original-image-bytes"),
        ],
    )


def _mock_responses(
    monkeypatch: pytest.MonkeyPatch, *responses: str | dict[str, Any] | Exception
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []

    def generate(**kwargs: Any) -> str:
        index = len(requests)
        requests.append(deepcopy(kwargs))
        assert index < len(responses), "the shared correction budget permits only one retry"
        response = responses[index]
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    monkeypatch.setattr(summarize_module, "load_summary_prompt", lambda: "mock instructions")
    return requests


def _summarize(notice: NoticeInput, prepared: PreparedInput | None) -> NoticeSummary:
    if prepared is None:
        return summarize_module.summarize_notice(notice, api_key="test-key")
    return summarize_module.summarize_prepared_notice(prepared, api_key="test-key").summary


def _assert_original_media(
    requests: list[dict[str, Any]], prepared: PreparedInput | None, original: Any
) -> None:
    if prepared is None:
        return
    assert prepared.blocks == original
    media = [block for block in original if block["type"] != "text"]
    for request in requests:
        assert [block for block in request["notice_text"] if block["type"] != "text"] == media
    if len(requests) == 2:
        assert (
            requests[1]["notice_text"][: len(requests[0]["notice_text"])]
            == requests[0]["notice_text"]
        )


@pytest.mark.parametrize("mixed", (False, True), ids=("text", "mixed"))
def test_shape_correction_preserves_valid_first_action_and_marks_review(
    monkeypatch: pytest.MonkeyPatch, mixed: bool
) -> None:
    notice = _notice()
    prepared = _prepared(notice) if mixed else None
    original = deepcopy(prepared.blocks) if prepared else None
    initial = _output(notice)
    initial["summary"] = "가" * 41
    corrected = _output(notice)
    corrected.update(action=None, action_requirement="unknown")
    corrected["evidence"] = [item for item in corrected["evidence"] if item["field"] != "action"]
    requests = _mock_responses(monkeypatch, initial, corrected)

    result = _summarize(notice, prepared)

    assert len(requests) == 2
    assert result.summary == TITLE
    assert result.action == ACTION
    assert result.action_requirement == "optional"
    assert result.audience == initial["audience"]
    assert result.location == initial["location"]
    assert [entry.model_dump() for entry in result.dates] == initial["dates"]
    assert result.notes == [COST]
    assert REVIEW_NOTE in result.uncertainties
    assert any(
        item.field == "action"
        and item.excerpt == "신청방법: " + ACTION
        and item.verification == "text_matched"
        for item in result.evidence
    )
    record = build_summary_record(
        PreparedSummaryResult(17, result, ()),
        SummaryMetadata("a" * 64, "gemini-3.5-flash-lite", "1", "none"),
        deadline_on=date(2026, 10, 6),
        generated_at=datetime(2026, 10, 4, tzinfo=UTC),
    )
    assert record.status == "needs_review"
    assert record.deadline_on is None
    _assert_original_media(requests, prepared, original)


@pytest.mark.parametrize("mixed", (False, True), ids=("text", "mixed"))
@pytest.mark.parametrize("terminal_error", ("broken-json", "missing-action", "missing-summary"))
def test_terminal_unrecoverable_response_fails_even_after_repairable_first_response(
    monkeypatch: pytest.MonkeyPatch, mixed: bool, terminal_error: str
) -> None:
    notice = _notice()
    prepared = _prepared(notice) if mixed else None
    original = deepcopy(prepared.blocks) if prepared else None
    initial = _output(notice)
    initial["summary"] = "가" * 41
    final: str | dict[str, Any]
    if terminal_error == "broken-json":
        final = "{broken-json"
    else:
        final = _output(notice)
        del final[terminal_error.removeprefix("missing-")]
    requests = _mock_responses(monkeypatch, initial, final)

    with pytest.raises(SummaryValidationError) as failure:
        _summarize(notice, prepared)

    assert failure.value.reason_code == "response_validation_failed"
    assert len(requests) == 2
    _assert_original_media(requests, prepared, original)


@pytest.mark.parametrize("mixed", (False, True), ids=("text", "mixed"))
def test_notes_correction_accepts_status_corrected_to_match_the_same_dates(
    monkeypatch: pytest.MonkeyPatch, mixed: bool
) -> None:
    notice = _notice()
    prepared = _prepared(notice) if mixed else None
    original = deepcopy(prepared.blocks) if prepared else None
    initial = _output(notice, include_cost=False)
    initial["status"] = "closed"
    corrected = _output(notice)
    requests = _mock_responses(monkeypatch, initial, corrected)

    result = _summarize(notice, prepared)

    assert len(requests) == 2
    assert result.status == "open"
    assert [entry.model_dump() for entry in result.dates] == corrected["dates"]
    assert result.summary == TITLE
    assert result.action == ACTION
    assert result.notes == [COST]
    _assert_original_media(requests, prepared, original)


@pytest.mark.parametrize("mixed", (False, True), ids=("text", "mixed"))
@pytest.mark.parametrize("failed_attempt", (1, 2), ids=("initial-api-failure", "retry-api-failure"))
def test_api_failure_never_falls_back_to_a_partial_success(
    monkeypatch: pytest.MonkeyPatch, mixed: bool, failed_attempt: int
) -> None:
    notice = _notice()
    prepared = _prepared(notice) if mixed else None
    original = deepcopy(prepared.blocks) if prepared else None
    initial = _output(notice)
    initial["summary"] = "가" * 41
    api_error = GeminiRequestError(
        "Gemini API returned status 413.", reason_code="input_too_large", status_code=413
    )
    responses = (api_error,) if failed_attempt == 1 else (initial, api_error)
    requests = _mock_responses(monkeypatch, *responses)

    with pytest.raises(GeminiRequestError) as failure:
        _summarize(notice, prepared)

    assert failure.value is api_error
    assert failure.value.reason_code == "input_too_large"
    assert failure.value.status_code == 413
    assert len(requests) == failed_attempt
    _assert_original_media(requests, prepared, original)


def test_complete_mixed_response_needs_no_retry_and_keeps_all_original_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notice = _notice()
    prepared = _prepared(notice)
    original = deepcopy(prepared.blocks)
    requests = _mock_responses(monkeypatch, _output(notice))

    result = _summarize(notice, prepared)

    assert len(requests) == 1
    assert result.action == ACTION
    assert result.status == "open"
    assert result.notes == [COST]
    assert result.uncertainties == []
    _assert_original_media(requests, prepared, original)


@pytest.mark.parametrize(
    "bad_uncertainties",
    (42, None, False, "invalid-list", {"item": "invalid-list"}, [{"item": "invalid-entry"}]),
    ids=("integer", "null", "boolean", "string", "mapping", "mapping-in-list"),
)
@pytest.mark.parametrize("mixed", (False, True), ids=("text", "mixed"))
def test_shape_retry_does_not_restore_invalid_raw_uncertainties(
    monkeypatch: pytest.MonkeyPatch, mixed: bool, bad_uncertainties: Any
) -> None:
    notice = _notice()
    prepared = _prepared(notice) if mixed else None
    original = deepcopy(prepared.blocks) if prepared else None
    initial = _output(notice)
    initial["uncertainties"] = deepcopy(bad_uncertainties)
    corrected = _output(notice)
    corrected.update(action=None, action_requirement="unknown", uncertainties=[])
    corrected["evidence"] = [item for item in corrected["evidence"] if item["field"] != "action"]
    requests = _mock_responses(monkeypatch, initial, corrected)

    result = _summarize(notice, prepared)

    assert len(requests) == 2
    assert result.summary == TITLE
    assert result.action == ACTION
    assert result.action_requirement == "optional"
    # Invalid raw strings/dict keys must not become accepted warning messages.
    assert result.uncertainties == [REVIEW_NOTE]
    _assert_original_media(requests, prepared, original)


@pytest.mark.parametrize("extra_location", ("root", "evidence"))
@pytest.mark.parametrize("mixed", (False, True), ids=("text", "mixed"))
def test_removing_extra_keys_does_not_abandon_other_valid_first_facts(
    monkeypatch: pytest.MonkeyPatch, mixed: bool, extra_location: str
) -> None:
    notice = _notice()
    prepared = _prepared(notice) if mixed else None
    original = deepcopy(prepared.blocks) if prepared else None
    initial = _output(notice)
    initial["summary"] = "가" * 41
    if extra_location == "root":
        initial["unexpected_field"] = "unrequested data"
    else:
        initial["evidence"][0]["unexpected_field"] = "unrequested data"
    corrected = _output(notice)
    corrected.update(action=None, action_requirement="unknown")
    corrected["evidence"] = [item for item in corrected["evidence"] if item["field"] != "action"]
    requests = _mock_responses(monkeypatch, initial, corrected)

    result = _summarize(notice, prepared)

    assert len(requests) == 2
    assert result.summary == TITLE
    assert result.action == ACTION
    assert result.action_requirement == "optional"
    assert REVIEW_NOTE in result.uncertainties
    assert "unexpected_field" not in result.model_dump()
    assert all("unexpected_field" not in item.model_dump() for item in result.evidence)
    _assert_original_media(requests, prepared, original)
