"""Keep response failures separate from usable, conservatively repaired summaries."""

import json
import logging
import sys
from base64 import b64encode
from copy import deepcopy
from typing import Any

import httpx
import pytest
from test_gemini_multimodal import PreparationIssue, _media, _mock_sdk, _notice, _prepared

from pipeline.transform import gemini_client, gemini_input, summary_cli
from pipeline.transform import summarize as summarize_module
from pipeline.transform.grounding import REVIEW_NOTE, unknown_summary
from pipeline.transform.summary_schema import SummaryValidationError

PRIVATE_MARKER = "MOCK_PRIVATE_PROVIDER_TOKEN_123"


def _valid_output() -> dict[str, Any]:
    data = unknown_summary(_notice("행사 안내")).model_dump()
    data.update(
        category="event",
        summary="행사 안내",
        uncertainties=[],
        evidence=[{"field": "summary", "excerpt": "행사 안내"}],
    )
    return data


@pytest.mark.parametrize("response", ["not-json", "{}", "[]", "null"])
@pytest.mark.parametrize("prepared_input", [False, True], ids=["text", "prepared"])
def test_unrecoverable_response_after_one_retry_is_failure(
    monkeypatch: pytest.MonkeyPatch, response: str, prepared_input: bool
) -> None:
    calls: list[dict[str, Any]] = []

    def generate(**kwargs: Any) -> str:
        calls.append(deepcopy(kwargs))
        return response

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    with pytest.raises(SummaryValidationError) as failure:
        if prepared_input:
            summarize_module.summarize_prepared_notice(
                _prepared("행사 안내", _media("document")), api_key="test-key"
            )
        else:
            summarize_module.summarize_notice(_notice("행사 안내"), api_key="test-key")
    assert len(calls) == 2
    assert failure.value.reason_code == "response_validation_failed"


def test_prepared_response_failure_preserves_warnings_and_all_original_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared("행사 안내", _media("image", b"first"), _media("document", b"second"))
    warnings = (PreparationIssue("hwp", 21, "table_layout_not_preserved"),)
    prepared.warnings = warnings
    original = deepcopy(prepared.blocks)
    calls: list[list[dict[str, str]]] = []

    def generate(**kwargs: Any) -> str:
        calls.append(deepcopy(kwargs["notice_text"]))
        return "{}"

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    with pytest.raises(SummaryValidationError):
        summarize_module.summarize_prepared_notice(prepared, api_key="test-key")
    assert len(calls) == 2
    assert prepared.warnings == warnings
    assert prepared.failures == ()
    assert prepared.blocks == original
    for request in calls:
        assert [block for block in request if block["type"] != "text"] == original[1:]
    assert calls[1][: len(calls[0])] == calls[0]


@pytest.mark.parametrize("missing", ["category", "summary", "publisher", "action", "dates"])
def test_one_missing_required_field_is_not_silently_filled_after_retry(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    data = _valid_output()
    del data[missing]
    calls: list[str] = []

    def generate(**_kwargs: Any) -> str:
        calls.append("request")
        return json.dumps(data)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    with pytest.raises(SummaryValidationError) as failure:
        summarize_module.summarize_notice(_notice("행사 안내"), api_key="test-key")
    assert failure.value.reason_code == "response_validation_failed"
    assert len(calls) == 2


def test_retry_can_supply_a_missing_required_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    complete = _valid_output()
    incomplete = deepcopy(complete)
    del incomplete["action"]
    responses = iter((json.dumps(incomplete), json.dumps(complete)))
    monkeypatch.setattr(
        summarize_module, "generate_summary_json", lambda **_kwargs: next(responses)
    )
    result = summarize_module.summarize_notice(_notice("행사 안내"), api_key="test-key")
    assert result.summary == "행사 안내"
    assert result.category == "event"


@pytest.mark.parametrize("bad_item", [None, "x" * 61, {}, False])
def test_notes_overflow_with_invalid_items_does_not_raise_or_choose_five_notes(
    monkeypatch: pytest.MonkeyPatch, bad_item: Any
) -> None:
    data = _valid_output() | {"notes": ["행사 안내"] * 5 + [bad_item]}
    monkeypatch.setattr(
        summarize_module, "generate_summary_json", lambda **_kwargs: json.dumps(data)
    )
    result = summarize_module.summarize_notice(_notice("행사 안내"), api_key="test-key")
    assert result.summary == "행사 안내"
    assert result.notes == []
    assert result.uncertainties == [REVIEW_NOTE]


def test_repeated_notes_overflow_remains_a_usable_partial_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _valid_output()
    data["notes"] = ["행사 안내"] * 6
    data["evidence"].append({"field": "notes", "excerpt": "행사 안내"})
    calls: list[str] = []

    def generate(**_kwargs: Any) -> str:
        calls.append("request")
        return json.dumps(data, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    result = summarize_module.summarize_notice(_notice("행사 안내"), api_key="test-key")
    assert calls == ["request", "request"]
    assert result.category == "event"
    assert result.summary == "행사 안내"
    assert result.notes == []
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("unsupported_summary", [False, True])
def test_inconclusive_grounding_preserves_claims_instead_of_becoming_shape_failure(
    monkeypatch: pytest.MonkeyPatch, unsupported_summary: bool
) -> None:
    data = _valid_output()
    if unsupported_summary:
        data["summary"] = "신청 접수"
    else:
        data.update(action="온라인 신청", action_requirement="optional")
        data["evidence"].append({"field": "action", "excerpt": "원문에 없는 내용"})
    calls: list[str] = []

    def generate(**_kwargs: Any) -> str:
        calls.append("request")
        return json.dumps(data, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    result = summarize_module.summarize_notice(_notice("행사 안내"), api_key="test-key")
    assert calls == ["request"]
    assert result.category == "event"
    assert result.summary == data["summary"]
    assert result.action == data["action"]
    assert any(item.field == "summary" for item in result.evidence)
    if not unsupported_summary:
        assert next(item for item in result.evidence if item.field == "action").verification is None
    assert result.uncertainties == [REVIEW_NOTE]


def test_cli_unrecoverable_response_returns_nonzero_without_success_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["summarize-notice", "mock-input.json"])
    # Keep the notice readable and mock only its file read; no filesystem write is needed.
    monkeypatch.setattr(
        "pathlib.Path.read_text", lambda *_args, **_kwargs: _notice("행사 안내").model_dump_json()
    )
    monkeypatch.setattr(summarize_module, "load_gemini_api_key", lambda: "test-key")
    monkeypatch.setattr(
        summarize_module, "generate_summary_json", lambda **_kwargs: "not-json " + PRIVATE_MARKER
    )
    # load_summary_prompt reads another file through Path; keep that read independent.
    monkeypatch.setattr(summarize_module, "load_summary_prompt", lambda: "mock instructions")
    code = summary_cli.main()
    output = capsys.readouterr()
    assert code == 1
    assert output.out == ""
    assert "Summary failed:" in output.err
    assert PRIVATE_MARKER not in output.err


def test_unknown_extra_response_key_does_not_leak_into_failure_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _valid_output() | {PRIVATE_MARKER: "unrecognized field"}
    monkeypatch.setattr(
        summarize_module, "generate_summary_json", lambda **_kwargs: json.dumps(data)
    )
    with pytest.raises(SummaryValidationError) as failure:
        summarize_module.summarize_notice(_notice("행사 안내"), api_key="test-key")
    assert failure.value.reason_code == "response_validation_failed"
    assert PRIVATE_MARKER not in str(failure.value)


@pytest.mark.parametrize(
    ("status", "reason_code"),
    [(403, "api_error"), (413, "input_too_large"), (429, "api_error")],
)
def test_sdk_http_failures_have_safe_codes_and_status(
    monkeypatch: pytest.MonkeyPatch, status: int, reason_code: str
) -> None:
    _mock_sdk(
        monkeypatch,
        lambda _request: httpx.Response(
            status,
            json={"error": {"code": status, "message": PRIVATE_MARKER}},
        ),
    )
    with pytest.raises(gemini_client.GeminiRequestError) as failure:
        gemini_client.generate_summary_json(
            prompt="instructions", notice_text=[_media("document")], api_key="test-key"
        )
    assert failure.value.reason_code == reason_code
    assert failure.value.status_code == status
    assert PRIVATE_MARKER not in str(failure.value)


@pytest.mark.parametrize(
    ("exception_type", "reason_code"),
    [(httpx.ReadTimeout, "api_timeout"), (httpx.ConnectError, "api_connection_error")],
)
def test_sdk_transport_failures_are_wrapped_without_private_error_text(
    monkeypatch: pytest.MonkeyPatch, exception_type: type[httpx.RequestError], reason_code: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exception_type(PRIVATE_MARKER, request=request)

    _mock_sdk(monkeypatch, handler)
    with pytest.raises(gemini_client.GeminiRequestError) as failure:
        gemini_client.generate_summary_json(
            prompt="instructions", notice_text=[_media("image")], api_key="test-key"
        )
    assert failure.value.reason_code == reason_code
    assert failure.value.status_code is None
    assert PRIVATE_MARKER not in str(failure.value)


@pytest.mark.parametrize(
    ("status", "content", "reason_code"),
    [
        ("failed", "partial", "response_incomplete"),
        ("in_progress", "partial", "response_incomplete"),
        ("completed", "", "empty_response"),
    ],
)
def test_interaction_completion_failures_have_distinct_reason_codes(
    monkeypatch: pytest.MonkeyPatch, status: str, content: str, reason_code: str
) -> None:
    _mock_sdk(
        monkeypatch,
        lambda _request: httpx.Response(
            200,
            json={
                "status": status,
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": content}]}],
            },
        ),
    )
    with pytest.raises(gemini_client.GeminiRequestError) as failure:
        gemini_client.generate_summary_json(
            prompt="instructions", notice_text=[_media("document")], api_key="test-key"
        )
    assert failure.value.reason_code == reason_code
    assert failure.value.status_code is None


def test_completed_interaction_diagnostic_errors_are_not_a_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_sdk(
        monkeypatch,
        lambda _request: httpx.Response(
            200,
            json={
                "status": "completed",
                "errors": [{"code": "RESOURCE_EXHAUSTED", "message": PRIVATE_MARKER}],
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": '{"summary":"partial"}'}],
                    }
                ],
            },
        ),
    )
    with pytest.raises(gemini_client.GeminiRequestError) as failure:
        gemini_client.generate_summary_json(
            prompt="instructions", notice_text=[_media("document")], api_key="test-key"
        )
    assert failure.value.reason_code == "api_error"
    assert failure.value.status_code is None
    assert PRIVATE_MARKER not in str(failure.value)


def test_sdk_debug_logging_does_not_expose_credentials_source_media_or_provider_body(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api_key = "MOCK_PRIVATE_API_KEY_123"
    notice_text = "MOCK_PRIVATE_NOTICE_SOURCE_123"
    media_data = b"MOCK_PRIVATE_IMAGE_BYTES_123"
    encoded_media = b64encode(media_data).decode("ascii")
    monkeypatch.setenv("GOOGLE_GENAI_DEBUG", "1")
    _mock_sdk(
        monkeypatch,
        lambda _request: httpx.Response(
            403,
            json={"error": {"code": 403, "message": PRIVATE_MARKER}},
        ),
    )
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(gemini_client.GeminiRequestError):
            gemini_client.generate_summary_json(
                prompt="instructions",
                notice_text=[{"type": "text", "text": notice_text}, _media("image", media_data)],
                api_key=api_key,
            )
    captured = capsys.readouterr()
    diagnostic_text = caplog.text + captured.out + captured.err
    for secret in (api_key, notice_text, encoded_media, PRIVATE_MARKER):
        assert secret not in diagnostic_text


def test_local_request_capacity_failure_has_no_api_client_or_success_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gemini_input, "MAX_GEMINI_INPUT_BYTES", 100)

    def forbidden_client(**_kwargs: Any) -> None:
        pytest.fail("oversized request must not open a Gemini client")

    monkeypatch.setattr(gemini_client.genai, "Client", forbidden_client)
    with pytest.raises(gemini_client.GeminiRequestError) as failure:
        gemini_client.generate_summary_json(
            prompt="instructions", notice_text="small input", api_key="test-key"
        )
    assert failure.value.reason_code == "input_too_large"
    assert failure.value.status_code is None


def test_input_adapter_error_is_safe_and_keeps_preparation_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared("행사 안내", _media("document"))
    warnings = (PreparationIssue("hwp", 21, "table_layout_not_preserved"),)
    prepared.warnings = warnings

    def broken_adapter() -> None:
        raise ValueError(PRIVATE_MARKER)

    def forbidden_generate(**_kwargs: Any) -> None:
        pytest.fail("bad prepared input must not reach Gemini")

    monkeypatch.setattr(prepared, "to_gemini_input", broken_adapter)
    monkeypatch.setattr(summarize_module, "generate_summary_json", forbidden_generate)
    with pytest.raises(summarize_module.SummaryPreparationError) as failure:
        summarize_module.summarize_prepared_notice(prepared, api_key="test-key")
    assert failure.value.reason_code == "invalid_prepared_input"
    assert PRIVATE_MARKER not in str(failure.value)
    assert prepared.warnings == warnings
