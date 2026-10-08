"""Content correction failures preserve a first strict response, never partial JSON."""

import json
import sys
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from support.file_only_summary import _file_output
from support.gemini_multimodal import PreparationIssue, _media, _prepared
from support.summary_grounding_retry import FACT, _notice, _response

from pipeline import summary_job
from pipeline.gemini_execution import (
    ExecutionBudget,
    GeminiExecutionError,
    current_execution,
    execution_budget,
)
from pipeline.storage import summaries as storage_module
from pipeline.storage.summary_record import SummaryMetadata, SummaryRecord
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.transform import summarize as summarize_module
from pipeline.transform import summary_cli
from pipeline.transform.gemini_client import DEFAULT_MODEL, GeminiRequestError
from pipeline.transform.gemini_input import GeminiInputError
from pipeline.transform.gemini_prompt import SUMMARY_PROMPT_VERSION
from pipeline.transform.grounding import REVIEW_NOTE, unknown_summary
from pipeline.transform.notice_input import render_notice_input
from pipeline.transform.prepared_summary import SummaryPreparationError
from pipeline.transform.summary_schema import (
    Evidence,
    GeminiNoticeSummary,
    NoticeSummary,
    SummaryValidationError,
)

COST = "참가비 : 30,000원"


def _failed_correction(first: dict[str, Any], problem: str) -> dict[str, Any] | str | Exception:
    if problem == "malformed":
        return "{broken-json"
    if problem == "timeout":
        return GeminiRequestError("mock correction timeout", reason_code="api_timeout")
    broken = deepcopy(first)
    if problem == "missing-required":
        del broken["category"]
    elif problem == "missing-card-object":
        del broken["card_summaries"]
    elif problem == "bad-card-style":
        broken["card_summaries"]["notes"] = "지원품은 식료품 꾸러미입니다."
    else:
        raise AssertionError(f"unknown mock problem: {problem}")
    return broken


def _generate(
    monkeypatch: pytest.MonkeyPatch, *responses: dict[str, Any] | str | Exception
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def generate(**kwargs: Any) -> str:
        index = len(calls)
        calls.append(deepcopy(kwargs))
        assert index < len(responses), "a content correction must not consume a third call"
        response = responses[index]
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    return calls


def _assert_first_is_preserved(
    summary: NoticeSummary, first: dict[str, Any], *, attachment_status: str
) -> None:
    original = NoticeSummary.model_validate(first)
    assert summary.model_dump(exclude={"evidence", "uncertainties"}) == original.model_dump(
        exclude={"evidence", "uncertainties"}
    )
    assert [item.model_dump(exclude={"verification"}) for item in summary.evidence] == [
        Evidence.model_validate(item).model_dump(exclude={"verification"})
        for item in first["evidence"]
    ]
    assert REVIEW_NOTE in summary.uncertainties
    view = build_notice_summary_view(
        status="needs_review", result=summary, attachment_status=attachment_status
    )
    assert view.status == "needs_review"
    assert view.message == "원문 확인 요함"
    assert view.content is not None
    assert view.content.headline.value == first["summary"]
    assert view.content.headline.text == first["summary"]
    assert set(view.content.cards.model_dump()) == {"audience", "deadline", "action", "notes"}
    assert summary.card_summaries is not None
    assert summary.card_summaries.model_dump() == first["card_summaries"]


@pytest.mark.parametrize(
    "problem", ["malformed", "missing-required", "missing-card-object", "bad-card-style"]
)
def test_news_content_retry_preserves_first_shape_valid_response(monkeypatch, problem) -> None:
    first = _response()
    snapshot = deepcopy(first)
    GeminiNoticeSummary.model_validate(first)
    calls = _generate(monkeypatch, first, _failed_correction(first, problem))

    result = summarize_module.summarize_notice(_notice(), api_key="mock-key")

    assert len(calls) == 2
    assert "summary/category/category_code:" in calls[1]["notice_text"]
    _assert_first_is_preserved(result, first, attachment_status="none")
    assert first == snapshot


def test_news_timeout_after_first_strict_response_retains_the_summary(monkeypatch) -> None:
    first = _response()
    calls = _generate(monkeypatch, first, _failed_correction(first, "timeout"))

    result = summarize_module.summarize_notice(_notice(), api_key="mock-key")

    assert len(calls) == 2
    _assert_first_is_preserved(result, first, attachment_status="none")


@pytest.mark.parametrize("problem", ["malformed", "timeout"])
def test_notes_coverage_retry_failure_retains_existing_facts_and_card_text(
    monkeypatch, problem
) -> None:
    notice = _notice().model_copy(update={"body_text": _notice().body_text + "\n" + COST})
    first = _response(FACT)
    GeminiNoticeSummary.model_validate(first)
    calls = _generate(monkeypatch, first, _failed_correction(first, problem))

    result = summarize_module.summarize_notice(notice, api_key="mock-key")

    assert len(calls) == 2
    assert "notes: 원문에 명시된 중요 조건" in calls[1]["notice_text"]
    _assert_first_is_preserved(result, first, attachment_status="none")
    assert COST not in result.notes


@pytest.mark.parametrize(
    "problem", ["malformed", "missing-required", "bad-card-style", "timeout"]
)
def test_pdf_reference_retry_failure_preserves_first_cards_and_original_media(
    monkeypatch, problem
) -> None:
    prepared = _prepared("", _media("document", b"mock-original-pdf"))
    first = _file_output("document")
    for item in first["evidence"]:
        del item["source_id"]
    GeminiNoticeSummary.model_validate(first)
    first_snapshot = deepcopy(first)
    media_snapshot = deepcopy(prepared.blocks)
    calls = _generate(monkeypatch, first, _failed_correction(first, problem))

    result = summarize_module.summarize_prepared_notice(prepared, api_key="mock-key")

    assert len(calls) == 2
    assert calls[1]["notice_text"][: len(calls[0]["notice_text"])] == calls[0]["notice_text"]
    assert [block for block in calls[1]["notice_text"] if block["type"] != "text"] == (
        media_snapshot[1:]
    )
    _assert_first_is_preserved(result.summary, first, attachment_status="all_read")
    assert result.notice_id == prepared.notice_id
    assert first == first_snapshot
    assert prepared.blocks == media_snapshot


@pytest.mark.parametrize("problem", ["malformed", "missing-required", "bad-card-style"])
@pytest.mark.parametrize("prepared_input", [False, True], ids=["text", "pdf"])
def test_first_response_without_strict_contract_never_becomes_a_fallback(
    monkeypatch, problem, prepared_input
):
    first = _file_output("document") if prepared_input else _response()
    broken_first = _failed_correction(first, problem)
    calls = _generate(monkeypatch, broken_first, "{broken-json")

    with pytest.raises(SummaryValidationError) as failure:
        if prepared_input:
            summarize_module.summarize_prepared_notice(
                _prepared("", _media("document")), api_key="mock-key"
            )
        else:
            summarize_module.summarize_notice(_notice(), api_key="mock-key")

    assert failure.value.reason_code == "response_validation_failed"
    assert len(calls) == 2


@pytest.mark.parametrize("prepared_input", [False, True], ids=["text", "pdf"])
def test_correction_request_preparation_error_retains_strict_first_response(
    monkeypatch, prepared_input
) -> None:
    first = _file_output("document") if prepared_input else _response()
    if prepared_input:
        for item in first["evidence"]:
            del item["source_id"]
    calls = _generate(monkeypatch, first)

    def reject_feedback(*_args: Any, **_kwargs: Any) -> None:
        raise GeminiInputError("input_too_large")

    monkeypatch.setattr(summarize_module, "append_retry_text", reject_feedback)

    if prepared_input:
        result = summarize_module.summarize_prepared_notice(
            _prepared("", _media("document")), api_key="mock-key"
        ).summary
    else:
        result = summarize_module.summarize_notice(_notice(), api_key="mock-key")

    assert len(calls) == 1
    _assert_first_is_preserved(
        result, first, attachment_status="all_read" if prepared_input else "none"
    )


def test_first_api_timeout_without_a_candidate_remains_a_failure(monkeypatch) -> None:
    calls = _generate(monkeypatch, _failed_correction(_response(), "timeout"))

    with pytest.raises(GeminiRequestError) as failure:
        summarize_module.summarize_notice(_notice(), api_key="mock-key")

    assert failure.value.reason_code == "api_timeout"
    assert len(calls) == 1


def test_failed_preparation_does_not_call_the_model_or_invent_a_candidate(monkeypatch) -> None:
    prepared = _prepared("", _media("document"))
    prepared.failures = (PreparationIssue("pdf", 1, "download_failed"),)
    calls = _generate(monkeypatch)

    with pytest.raises(SummaryPreparationError):
        summarize_module.summarize_prepared_notice(prepared, api_key="mock-key")

    assert calls == []
    assert prepared.calls == []


@pytest.mark.parametrize(
    ("kind", "problem", "expected_code"),
    [
        ("text", "malformed", "response_validation_failed"),
        ("text", "timeout", "api_timeout"),
        ("pdf", "malformed", "response_validation_failed"),
        ("pdf", "timeout", "api_timeout"),
        ("text", "input-too-large", "input_too_large"),
    ],
)
def test_correction_failure_code_reaches_storage_handoff_without_public_json(
    monkeypatch, kind, problem, expected_code
) -> None:
    if kind == "pdf":
        prepared = _prepared("", _media("document"))
        first = _file_output("document")
        for item in first["evidence"]:
            del item["source_id"]
    else:
        notice = _notice()
        prepared = _prepared()
        prepared.notice = notice
        prepared.blocks = [{"type": "text", "text": render_notice_input(notice)}]
        first = _response()
    if problem == "input-too-large":
        def reject_feedback(*_args: Any, **_kwargs: Any) -> None:
            raise GeminiInputError(expected_code)

        monkeypatch.setattr(summarize_module, "append_retry_text", reject_feedback)
        calls = _generate(monkeypatch, first)
    else:
        calls = _generate(monkeypatch, first, _failed_correction(first, problem))

    result = summarize_module.summarize_prepared_notice(prepared, api_key="mock-key")

    assert len(calls) == (1 if problem == "input-too-large" else 2)
    assert result.correction_failure_code == expected_code
    assert result.summary._correction_failure_code == expected_code
    public_summary = result.summary.model_dump(mode="json")
    assert "correction_failure_code" not in public_summary
    assert "_correction_failure_code" not in public_summary
    public_json = result.summary.model_dump_json()
    assert "correction_failure_code" not in public_json
    assert expected_code not in public_json
    view = build_notice_summary_view(
        status="needs_review", result=result.summary,
        attachment_status="all_read" if kind == "pdf" else "none",
    )
    assert "correction_failure_code" not in view.model_dump_json()
    assert expected_code not in view.model_dump_json()


@pytest.mark.parametrize("kind", ["text", "pdf"])
def test_normal_first_response_has_no_correction_failure_flag(monkeypatch, kind) -> None:
    if kind == "pdf":
        prepared = _prepared("", _media("document"))
        first = _file_output("document")
    else:
        notice = _notice()
        prepared = _prepared()
        prepared.notice = notice
        prepared.blocks = [{"type": "text", "text": render_notice_input(notice)}]
        first = _response(FACT)
    calls = _generate(monkeypatch, first)

    result = summarize_module.summarize_prepared_notice(prepared, api_key="mock-key")

    assert len(calls) == 1
    assert result.correction_failure_code is None
    assert result.summary._correction_failure_code is None


def test_valid_second_news_response_survives_an_invalid_first_card_restoration(monkeypatch) -> None:
    first = _response()
    first["card_summaries"]["audience"] = "주민입니다."
    NoticeSummary.model_validate(first)
    second = _response(FACT)
    GeminiNoticeSummary.model_validate(second)
    second_snapshot = deepcopy(second)
    calls = _generate(monkeypatch, first, second)

    result = summarize_module.summarize_notice(_notice(), api_key="mock-key")

    assert len(calls) == 2
    _assert_first_is_preserved(result, second, attachment_status="none")
    assert result.summary == FACT
    assert result.card_summaries is not None
    assert result.card_summaries.audience is None
    assert result._correction_failure_code == "response_validation_failed"
    assert "correction_failure_code" not in result.model_dump_json()
    assert second == second_snapshot


def test_valid_second_pdf_response_does_not_resurrect_the_invalid_first_card(monkeypatch) -> None:
    prepared = _prepared("", _media("document"))
    first = _file_output("document")
    first["card_summaries"]["audience"] = "지역 주민입니다."
    NoticeSummary.model_validate(first)
    second = unknown_summary(prepared.notice, has_media=True).model_dump(mode="json")
    GeminiNoticeSummary.model_validate(second)
    second_snapshot = deepcopy(second)
    calls = _generate(monkeypatch, first, second)

    result = summarize_module.summarize_prepared_notice(prepared, api_key="mock-key")

    assert len(calls) == 2
    _assert_first_is_preserved(result.summary, second, attachment_status="all_read")
    assert result.summary.audience is None
    assert result.summary.card_summaries is not None
    assert result.summary.card_summaries.model_dump() == {
        "audience": None, "deadline": None, "action": None, "notes": None,
    }
    assert result.correction_failure_code == "response_validation_failed"
    assert result.summary._correction_failure_code == "response_validation_failed"
    assert "correction_failure_code" not in result.summary.model_dump_json()
    assert second == second_snapshot


def test_job_stores_correction_fallback_as_review_and_public_view_keeps_it(monkeypatch) -> None:
    notice = _notice().model_copy(update={"body_text": _notice().body_text + "\n" + COST})
    prepared = _prepared()
    prepared.notice = notice
    prepared.blocks = [{"type": "text", "text": render_notice_input(notice)}]
    first = _response(FACT)
    calls = _generate(monkeypatch, first, _failed_correction(first, "timeout"))
    metadata = SummaryMetadata(
        source_hash="ab" * 32,
        model=DEFAULT_MODEL,
        prompt_version=SUMMARY_PROMPT_VERSION,
        attachment_status="none",
    )
    stored: list[SummaryRecord] = []
    failure_codes: list[str] = []
    registrations: list[tuple[int, int | None]] = []

    def register(_conn: object, notice_id: int, *, expected_source_revision=None) -> int:
        registrations.append((notice_id, expected_source_revision))
        return 101

    def save_fallback(
        _conn: object, record: SummaryRecord, *, reason_code: str
    ) -> tuple:
        stored.append(record)
        failure_codes.append(reason_code)
        return record.notice_id, record.status, record.deadline_on, record.generated_at

    def unexpected_failure(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("a usable correction fallback must be stored as review, not failed")

    def unexpected_deadline(_summary: NoticeSummary) -> None:
        pytest.fail("a correction fallback must not compute a sorting deadline")

    monkeypatch.setattr(summary_job, "begin_summary_execution", register)
    monkeypatch.setattr(summary_job, "record_summary_failure", unexpected_failure)
    monkeypatch.setattr(storage_module, "save_notice_summary", unexpected_failure)
    monkeypatch.setattr(
        storage_module, "_save_summary_correction_fallback", save_fallback, raising=False
    )

    outcome = summary_job.summarize_and_save_prepared_notice(
        object(), prepared, metadata, api_key="mock-key",
        deadline_resolver=unexpected_deadline, expected_source_revision=3,
    )

    assert len(calls) == 2
    assert registrations == [(prepared.notice_id, 3)]
    assert len(stored) == 1
    assert failure_codes == ["api_timeout"]
    record = stored[0]
    assert record.status == outcome.status == "needs_review"
    assert record.execution_token == 101
    assert record.attempt_increment == 1
    assert record.deadline_on is outcome.deadline_on is None
    assert record.last_error_code is None
    assert record.result is not None
    _assert_first_is_preserved(record.result, first, attachment_status="none")
    assert outcome.result.summary.model_dump() == record.result.model_dump()
    assert outcome.result.correction_failure_code == "api_timeout"


@pytest.mark.parametrize("kind", ["text", "pdf"])
def test_expired_shared_budget_skips_correction_and_keeps_first_valid_candidate(
    monkeypatch, kind,
):
    if kind == "pdf":
        prepared = _prepared("", _media("document"))
        first = _file_output("document")
        for item in first["evidence"]:
            del item["source_id"]
    else:
        prepared = _prepared()
        prepared.notice = _notice()
        prepared.blocks = [{"type": "text", "text": render_notice_input(prepared.notice)}]
        first = _response()
    budget = ExecutionBudget()
    calls = _generate(monkeypatch, first)
    append_feedback = summarize_module.append_retry_text

    def expire_after_first_candidate(*args, **kwargs):
        assert current_execution() is budget
        request = append_feedback(*args, **kwargs)
        budget.deadline = 0
        return request

    monkeypatch.setattr(summarize_module, "append_retry_text", expire_after_first_candidate)
    result = summarize_module.summarize_prepared_notice(prepared, api_key="mock-key", budget=budget)

    assert len(calls) == 1
    assert result.correction_failure_code == "api_timeout"
    assert result.execution_failure == budget.last_failure.to_dict()
    assert result.execution_failure["failure_kind"] == "deadline"
    _assert_first_is_preserved(
        result.summary, first, attachment_status="all_read" if kind == "pdf" else "none",
    )
    assert current_execution() is None


def test_expired_correction_without_a_valid_first_candidate_is_a_failure(monkeypatch):
    budget = ExecutionBudget()
    calls = _generate(monkeypatch, "{invalid-json")
    append_feedback = summarize_module.append_retry_text

    def expire_after_invalid_response(*args, **kwargs):
        request = append_feedback(*args, **kwargs)
        budget.deadline = 0
        return request

    monkeypatch.setattr(summarize_module, "append_retry_text", expire_after_invalid_response)
    with pytest.raises(GeminiExecutionError) as error:
        summarize_module.summarize_notice(_notice(), api_key="mock-key", budget=budget)
    assert error.value.failure_kind == "deadline"
    assert len(calls) == 1


def test_corrections_reuse_the_callers_budget_without_resetting_deadline(monkeypatch):
    budget = ExecutionBudget()
    deadline = budget.deadline
    seen = []

    def generate(**kwargs):
        seen.append(current_execution())
        return json.dumps(_response() if len(seen) == 1 else _response(FACT))

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    with execution_budget(budget):
        result = summarize_module.summarize_notice(_notice(), api_key="mock-key")
    assert result.summary == FACT
    assert seen == [budget, budget]
    assert budget.deadline == deadline


def test_late_first_response_is_rejected_before_it_can_become_a_candidate(monkeypatch):
    budget = ExecutionBudget()

    def generate(**kwargs):
        budget.deadline = 0
        return json.dumps(_response(FACT))

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    with pytest.raises(GeminiExecutionError) as error:
        summarize_module.summarize_notice(_notice(), api_key="mock-key", budget=budget)
    assert error.value.reason_code == "api_timeout"
    assert error.value.failure_kind == "deadline"


def test_cli_returns_failure_with_usable_output_and_safe_deferral_metadata(
    monkeypatch, tmp_path, capsys,
):
    source = tmp_path / "notice.json"
    source.write_text(_notice().model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["summarize-notice", str(source)])
    monkeypatch.setattr(summarize_module, "load_gemini_api_key", lambda: "private-test-key")
    failure = GeminiExecutionError(
        "api_error", failure_kind="deferred", retryable=True,
        retry_at=datetime(2026, 10, 9, tzinfo=UTC), status_code=429,
    )
    calls = _generate(monkeypatch, _response(), failure)

    assert summary_cli.main() == 1
    captured = capsys.readouterr()
    assert len(calls) == 2
    assert json.loads(captured.out)["summary"] == _response()["summary"]
    details = json.loads(captured.err.removeprefix("Summary failed: "))
    assert details["execution_failure"] == failure.to_dict()
    assert "private-test-key" not in captured.out + captured.err


def test_cli_invalid_execution_budget_is_safe_without_starting_generation(
    monkeypatch, tmp_path, capsys,
):
    source = tmp_path / "notice.json"
    source.write_text(_notice().model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["summarize-notice", str(source)])
    monkeypatch.setenv("GEMINI_EXECUTION_TIMEOUT_SECONDS", "private-invalid-value")
    calls = _generate(monkeypatch)

    assert summary_cli.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert calls == []
    details = json.loads(captured.err.removeprefix("Summary failed: "))
    assert details["execution_failure"]["reason_code"] == "configuration_error"
    assert details["gemini_requests"] == details["gemini_http_attempts"] == 0
    assert "private-invalid-value" not in captured.err


@pytest.mark.parametrize(
    "stage", [
        "_validate_summary", "_require_generated_cards",
        "validation_error", "final_validation_error",
    ],
)
@pytest.mark.parametrize("attempt", [1, 2])
def test_local_validation_must_finish_before_a_new_candidate_can_be_published(
    monkeypatch, stage, attempt,
):
    budget = ExecutionBudget()
    first = _response() if attempt == 2 else _response(FACT)
    calls = _generate(monkeypatch, first, _response(FACT))
    validator = {
        "validation_error": "_validate_summary",
        "final_validation_error": "_require_generated_cards",
    }.get(stage, stage)
    validate = getattr(summarize_module, validator)

    def expire_during_validation(*args, **kwargs):
        result = validate(*args, **kwargs)
        if len(calls) == attempt:
            budget.deadline = 0
            if stage == "validation_error" or (
                stage == "final_validation_error" and result._correction_failure_code is None
            ):
                raise SummaryValidationError("validation did not complete")
        return result

    monkeypatch.setattr(summarize_module, validator, expire_during_validation)
    if attempt == 1:
        with pytest.raises(GeminiExecutionError) as error:
            summarize_module.summarize_notice(_notice(), api_key="mock-key", budget=budget)
        assert error.value.failure_kind == "deadline"
    else:
        result = summarize_module.summarize_notice(_notice(), api_key="mock-key", budget=budget)
        _assert_first_is_preserved(result, first, attachment_status="none")
        assert result._correction_failure_code == "api_timeout"
    assert len(calls) == attempt
