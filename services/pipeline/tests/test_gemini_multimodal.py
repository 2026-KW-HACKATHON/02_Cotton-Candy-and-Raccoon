"""Prepared-input handoff and real SDK serialization without live services."""

import json
from base64 import b64encode
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from google.genai import types

from pipeline.transform import gemini_client, gemini_input
from pipeline.transform import summarize as summarize_module
from pipeline.transform.gemini_prompt import load_summary_prompt
from pipeline.transform.grounding import unknown_summary
from pipeline.transform.notice_input import NoticeInput, render_notice_input


@dataclass(frozen=True)
class PreparationIssue:
    stage: str
    item_id: int
    reason_code: str


@dataclass
class PreparedInput:
    """Match #13's handoff contract without importing or copying its implementation."""

    notice_id: int
    notice: NoticeInput
    blocks: list[dict[str, str]]
    failures: tuple[PreparationIssue, ...] = ()
    warnings: tuple[PreparationIssue, ...] = ()
    calls: list[str] = field(default_factory=list)

    def to_gemini_input(self) -> list[dict[str, str]]:
        self.calls.append("to_gemini_input")
        if self.failures:
            raise ValueError("summary input preparation is incomplete")
        return deepcopy(self.blocks)


def _notice(body: str = "") -> NoticeInput:
    return NoticeInput.model_validate(
        {
            "title": "행사 안내",
            "body_text": body,
            "reference_datetime": "2026-10-03T12:00:00+09:00",
        }
    )


def _media(kind: str, marker: bytes = b"original attachment") -> dict[str, str]:
    return {
        "type": kind,
        "mime_type": "application/pdf" if kind == "document" else "image/png",
        "data": b64encode(marker).decode("ascii"),
    }


def _prepared(
    body: str = "", *media: dict[str, str], attachment_text: bool = False
) -> PreparedInput:
    notice = _notice(body)
    if attachment_text:
        notice = NoticeInput.model_validate(
            notice.model_dump() | {"attachments": [{"name": "안내.hwp", "text": "행사 안내"}]}
        )
    return PreparedInput(
        17,
        notice,
        [{"type": "text", "text": render_notice_input(notice)}, *deepcopy(media)],
    )


@pytest.fixture
def summary_output() -> dict[str, Any]:
    """Start from the checked-in prompt example, then remove unrelated claims."""
    prompt = load_summary_prompt()
    start = prompt.index("{", prompt.index("[8. 출력 형식]"))
    example, _ = json.JSONDecoder().raw_decode(prompt[start:])
    example.update(unknown_summary(_notice()).model_dump())
    example.update(
        category="event",
        summary="행사 안내",
        uncertainties=[],
        evidence=[{"field": "summary", "excerpt": "행사 안내"}],
    )
    return example


def _file_summary(summary: dict[str, Any], kind: str) -> str:
    data = deepcopy(summary)
    data["evidence"][0].update(
        source_type=kind,
        source_id="media_1",
        page=1 if kind == "document" else None,
    )
    return json.dumps(data, ensure_ascii=False)


def _mock_sdk(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Keep Google's real serializer and response parser behind MockTransport."""
    real_client = gemini_client.genai.Client

    def client_factory(*, api_key: str, http_options: types.HttpOptions) -> Any:
        options = http_options.model_copy(
            update={
                "base_url": "https://gemini.invalid",
                "client_args": {"transport": httpx.MockTransport(handler)},
                "retry_options": types.HttpRetryOptions(attempts=1),
            }
        )
        return real_client(api_key=api_key, http_options=options)

    monkeypatch.setattr(gemini_client.genai, "Client", client_factory)


@pytest.mark.parametrize("kind", ["legacy", "text", "document", "image", "mixed"])
def test_real_sdk_serializes_text_and_media_in_the_original_order(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    blocks = [{"type": "text", "text": "notice source"}]
    if kind in {"document", "mixed"}:
        blocks.append(_media("document", b"%PDF-first"))
    if kind in {"image", "mixed"}:
        blocks.append(_media("image", b"second image"))
    supplied = "notice source" if kind == "legacy" else blocks
    original = deepcopy(supplied)
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": '{"summary":"mock"}'}],
                    }
                ],
            },
        )

    _mock_sdk(monkeypatch, handler)
    raw = gemini_client.generate_summary_json(
        prompt="source-only instructions", notice_text=supplied, api_key="test-key"
    )

    assert raw == '{"summary":"mock"}'
    assert len(requests) == 1
    wire = requests[0]
    assert wire["input"] == (
        supplied if kind == "legacy" else [{"type": "user_input", "content": blocks}]
    )
    assert wire["store"] is False
    assert wire["model"] == gemini_client.DEFAULT_MODEL
    assert wire["response_format"]["mime_type"] == "application/json"
    assert supplied == original


@pytest.mark.parametrize("status", [403, 413, 429])
def test_real_sdk_http_failure_is_not_a_successful_summary(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(
            status,
            json={"error": {"code": status, "message": "private provider response"}},
        )

    _mock_sdk(monkeypatch, handler)
    with pytest.raises(gemini_client.GeminiRequestError, match=f"status {status}") as error:
        gemini_client.generate_summary_json(
            prompt="instructions", notice_text=[_media("document")], api_key="test-key"
        )
    # Interactions may retry rate limits internally; every attempt stays mocked.
    assert calls and all(method == "POST" for method in calls)
    assert "private provider response" not in str(error.value)


def test_real_sdk_empty_output_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_sdk(
        monkeypatch,
        lambda _request: httpx.Response(200, json={"status": "completed", "steps": []}),
    )
    with pytest.raises(gemini_client.GeminiRequestError, match="no summary text"):
        gemini_client.generate_summary_json(
            prompt="instructions", notice_text=[_media("image")], api_key="test-key"
        )


@pytest.mark.parametrize(
    "status",
    [
        "failed",
        "in_progress",
        "requires_action",
        "cancelled",
        "incomplete",
        "budget_exceeded",
        "queued",
    ],
)
def test_unfinished_or_failed_sdk_interaction_with_partial_text_is_not_success(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    _mock_sdk(
        monkeypatch,
        lambda _request: httpx.Response(
            200,
            json={
                "status": status,
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": '{"summary":"partial"}'}],
                    }
                ],
            },
        ),
    )
    with pytest.raises(gemini_client.GeminiRequestError):
        gemini_client.generate_summary_json(
            prompt="instructions", notice_text=[_media("document")], api_key="test-key"
        )


def test_request_budget_includes_prompt_and_schema_before_opening_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gemini_input, "MAX_GEMINI_INPUT_BYTES", 100)

    def forbidden_client(**_kwargs: Any) -> None:
        pytest.fail("oversized request must not open an API client")

    monkeypatch.setattr(gemini_client.genai, "Client", forbidden_client)
    with pytest.raises(gemini_client.GeminiRequestError, match="input_too_large"):
        gemini_client.generate_summary_json(
            prompt="instructions", notice_text="small source", api_key="test-key"
        )


@pytest.mark.parametrize(
    "supplied",
    [
        [{"type": "image", "mime_type": "image/png", "data": "invalid base64%"}],
        [{"type": "image", "mime_type": "image/png", "data": ""}],
        [{"type": "video", "mime_type": "video/mp4", "data": "eA=="}],
        [{"type": "image", "mime_type": "application/pdf", "data": "eA=="}],
        [{"type": "document", "mime_type": "application/msword", "data": "eA=="}],
        [
            {
                "type": "document",
                "mime_type": "application/pdf",
                "data": "eA==",
                "uri": "https://private.invalid/file",
            }
        ],
        [{"type": "image", "mime_type": "image/png", "data": b"raw binary"}],
        ["not a content block"],
    ],
    ids=[
        "invalid-base64",
        "empty-image",
        "unknown-block-kind",
        "image-mime-mismatch",
        "unsupported-document-mime",
        "extra-uri",
        "raw-bytes",
        "non-dictionary-block",
    ],
)
def test_invalid_prepared_blocks_are_rejected_before_sdk_client(
    monkeypatch: pytest.MonkeyPatch, supplied: Any
) -> None:
    original = deepcopy(supplied)

    def forbidden_client(**_kwargs: Any) -> None:
        pytest.fail("invalid content must not open an API client")

    monkeypatch.setattr(gemini_client.genai, "Client", forbidden_client)
    with pytest.raises(gemini_client.GeminiRequestError):
        gemini_client.generate_summary_json(
            prompt="instructions", notice_text=supplied, api_key="test-key"
        )
    assert supplied == original


@pytest.mark.parametrize(
    ("body", "kinds", "attachment_text"),
    [
        ("행사 안내", [], False),
        ("", [], True),
        ("", ["document"], False),
        ("", ["image"], False),
        ("행사 안내", ["image", "document"], True),
    ],
)
def test_prepared_text_hwp_pdf_image_and_mixed_sources_are_sent(
    monkeypatch: pytest.MonkeyPatch,
    summary_output: dict[str, Any],
    body: str,
    kinds: list[str],
    attachment_text: bool,
) -> None:
    prepared = _prepared(body, *(_media(kind) for kind in kinds), attachment_text=attachment_text)
    original = deepcopy(prepared.blocks)
    requests: list[dict[str, Any]] = []
    raw = (
        _file_summary(summary_output, kinds[0])
        if kinds and not body and not attachment_text
        else json.dumps(summary_output, ensure_ascii=False)
    )

    def generate(**kwargs: Any) -> str:
        requests.append(deepcopy(kwargs))
        return raw

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    result = summarize_module.summarize_prepared_notice(prepared, api_key="test-key")

    assert len(requests) == 1
    sent = requests[0]["notice_text"]
    assert isinstance(sent, list)
    assert [part for part in sent if part["type"] != "text"] == original[1:]
    assert render_notice_input(prepared.notice) in sent[0]["text"]
    if kinds:
        text = "\n".join(part["text"] for part in sent if part["type"] == "text")
        for index in range(1, len(kinds) + 1):
            assert f"media_{index}" in text
    assert result.notice_id == 17
    assert result.summary.summary == "행사 안내"
    assert result.warnings == ()
    assert [(item.source_id, item.source_type) for item in result.media_sources] == [
        (f"media_{index}", kind) for index, kind in enumerate(kinds, start=1)
    ]
    assert prepared.blocks == original
    assert prepared.calls == ["to_gemini_input"]
    if kinds and not body and not attachment_text:
        assert result.summary.evidence[0].verification == "file_reference_only"


def test_preparation_failure_blocks_adapter_credentials_and_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared("행사 안내", _media("document"))
    prepared.failures = (PreparationIssue("file", 8, "timeout"),)

    def forbidden(**_kwargs: Any) -> None:
        pytest.fail("preparation failure must stop before credentials and the API")

    monkeypatch.setattr(summarize_module, "load_gemini_api_key", forbidden)
    monkeypatch.setattr(summarize_module, "generate_summary_json", forbidden)
    with pytest.raises(summarize_module.SummaryPreparationError):
        summarize_module.summarize_prepared_notice(prepared)
    assert prepared.calls == []


def test_preparation_warnings_are_preserved_without_mutation(
    monkeypatch: pytest.MonkeyPatch, summary_output: dict[str, Any]
) -> None:
    prepared = _prepared("행사 안내", attachment_text=True)
    warnings = (
        PreparationIssue("hwp", 5, "table_layout_not_preserved"),
        PreparationIssue("hwp", 6, "unsupported_control"),
    )
    prepared.warnings = warnings
    monkeypatch.setattr(
        summarize_module,
        "generate_summary_json",
        lambda **_kwargs: json.dumps(summary_output, ensure_ascii=False),
    )
    result = summarize_module.summarize_prepared_notice(prepared, api_key="test-key")
    assert result.summary.summary == "행사 안내"
    assert result.warnings == warnings
    assert prepared.warnings == warnings


def test_retry_preserves_every_media_block_order_and_source_manifest(
    monkeypatch: pytest.MonkeyPatch, summary_output: dict[str, Any]
) -> None:
    prepared = _prepared("", _media("image", b"first"), _media("document", b"second"))
    original = deepcopy(prepared.blocks)
    valid = _file_summary(summary_output, "image")
    invalid = json.loads(valid)
    invalid["summary"] = "가" * 41
    responses = iter((json.dumps(invalid, ensure_ascii=False), valid))
    requests: list[list[dict[str, str]]] = []

    def generate(**kwargs: Any) -> str:
        requests.append(deepcopy(kwargs["notice_text"]))
        return next(responses)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    result = summarize_module.summarize_prepared_notice(prepared, api_key="test-key")
    assert result.summary.summary == "행사 안내"
    assert len(requests) == 2
    for sent in requests:
        assert [part for part in sent if part["type"] != "text"] == original[1:]
    assert requests[1][: len(requests[0])] == requests[0]
    assert requests[1][-1]["type"] == "text"
    assert "40자" in requests[1][-1]["text"]
    assert prepared.blocks == original
    assert prepared.calls == ["to_gemini_input"]


@pytest.mark.parametrize("after_retry", [False, True])
def test_prepared_api_error_propagates_instead_of_returning_unknown_success(
    monkeypatch: pytest.MonkeyPatch,
    summary_output: dict[str, Any],
    after_retry: bool,
) -> None:
    prepared = _prepared("", _media("document"))
    invalid = summary_output | {"summary": "가" * 41}
    calls: list[str] = []

    def generate(**_kwargs: Any) -> str:
        calls.append("api")
        if after_retry and len(calls) == 1:
            return json.dumps(invalid, ensure_ascii=False)
        raise gemini_client.GeminiRequestError("Gemini API returned status 413.")

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    with pytest.raises(gemini_client.GeminiRequestError, match="status 413"):
        summarize_module.summarize_prepared_notice(prepared, api_key="test-key")
    assert len(calls) == (2 if after_retry else 1)


def test_prepared_capacity_failure_does_not_call_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared("", _media("document"))
    monkeypatch.setattr(gemini_input, "MAX_GEMINI_INPUT_BYTES", 10)

    def forbidden(**_kwargs: Any) -> None:
        pytest.fail("oversized prepared input must not be sent")

    monkeypatch.setattr(summarize_module, "generate_summary_json", forbidden)
    with pytest.raises((gemini_input.GeminiInputError, summarize_module.SummaryPreparationError)):
        summarize_module.summarize_prepared_notice(prepared, api_key="test-key")
