"""Exercise SDK error compatibility without private imports or live requests."""

import ast
import json
import subprocess
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from importlib import metadata
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.genai import errors
from support.gemini_multimodal import _media, _mock_sdk
from support.paths import PIPELINE_DIR

from pipeline.gemini_execution import GeminiExecutionError
from pipeline.gemini_sdk_adapter import retry_at_from_headers
from pipeline.transform import gemini_client
from pipeline.transform.summary_schema import GeminiNoticeSummary

PRIVATE_MARKER = "MOCK_PRIVATE_SDK_ERROR_CONTENT_123"
PIPELINE_ROOT = PIPELINE_DIR


def _raise_from_client(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    """Replace only the SDK boundary, leaving request validation in place."""

    class FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.interactions = SimpleNamespace(
                create=self.create, sdk_configuration=SimpleNamespace(retry_config=None)
            )

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def create(self, **_kwargs: Any) -> None:
            raise error

    monkeypatch.setattr(gemini_client.genai, "Client", FakeClient)


def _sdk_error(
    base_name: str = "APIError", *, status_code: Any = None, code: Any = None
) -> Exception:
    """Simulate a moved SDK hierarchy rather than importing its private location."""
    api_base = type(
        base_name,
        (Exception,),
        {"__module__": "google.genai.future_interactions.errors"},
    )
    specific_error = type(
        "ProviderSpecificFailure",
        (api_base,),
        {"__module__": "google.genai.future_interactions.errors"},
    )
    error = specific_error(PRIVATE_MARKER)
    error.status_code = status_code
    error.code = code
    return error


def _request() -> str:
    return gemini_client._generate_summary_json_direct(
        prompt="instructions", notice_text=[_media("document")], api_key="test-key"
    )


def test_installed_sdk_sends_the_required_four_card_response_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "steps": [{
                    "type": "model_output",
                    "content": [{"type": "text", "text": '{"summary":"mock"}'}],
                }],
            },
        )

    _mock_sdk(monkeypatch, handler)
    assert _request() == '{"summary":"mock"}'
    assert len(sent) == 1
    response_format = sent[0]["response_format"]
    assert response_format["mime_type"] == "application/json"
    schema = response_format["schema"]
    assert schema == GeminiNoticeSummary.model_json_schema()
    assert "card_summaries" in schema["required"]
    assert schema["properties"]["card_summaries"] == {"$ref": "#/$defs/GeminiCardSummaries"}
    assert set(schema["$defs"]["GeminiCardSummaries"]["required"]) == {
        "audience", "deadline", "action", "notes"
    }
    assert sent[0]["store"] is False


@pytest.mark.parametrize(
    ("status", "reason_code"),
    [(403, "api_error"), (413, "input_too_large"), (429, "api_error"), (503, "api_error")],
)
def test_installed_sdk_http_errors_remain_failures_after_removing_private_imports(
    monkeypatch: pytest.MonkeyPatch, status: int, reason_code: str
) -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(status, json={"error": {"code": status, "message": PRIVATE_MARKER}})

    _mock_sdk(monkeypatch, handler)
    with pytest.raises(gemini_client.GeminiRequestError) as failure:
        _request()
    assert sent
    assert all(request["store"] is False for request in sent)
    assert all(request["model"] == gemini_client.DEFAULT_MODEL for request in sent)
    assert failure.value.status_code == status
    assert failure.value.reason_code == reason_code
    assert str(failure.value) == f"Gemini API returned status {status}."
    assert PRIVATE_MARKER not in str(failure.value)
    assert failure.value.__cause__ is None
    assert failure.value.__suppress_context__ is True


def test_public_sdk_api_error_is_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    error = errors.APIError(429, {"error": {"message": PRIVATE_MARKER}})
    _raise_from_client(monkeypatch, error)
    with pytest.raises(gemini_client.GeminiRequestError) as failure:
        _request()
    assert failure.value.status_code == 429
    assert failure.value.reason_code == "api_error"
    assert PRIVATE_MARKER not in str(failure.value)


@pytest.mark.parametrize(
    ("body", "content_type", "reason_code"),
    [
        ('{"steps": []}', "application/json", "response_incomplete"),
        ('{"status": 42, "steps": []}', "application/json", "response_incomplete"),
        ("null", "application/json", "response_incomplete"),
        ("[]", "application/json", "response_incomplete"),
        ('{"status": "completed", "steps": []}', "application/json", "empty_response"),
        ("<html>" + PRIVATE_MARKER + "</html>", "text/html", "api_error"),
        ("{invalid-json " + PRIVATE_MARKER, "application/json", "api_error"),
    ],
)
def test_installed_sdk_malformed_success_response_is_not_returned_as_a_summary(
    monkeypatch: pytest.MonkeyPatch, body: str, content_type: str, reason_code: str
) -> None:
    _mock_sdk(
        monkeypatch,
        lambda _request: httpx.Response(
            200, content=body.encode("utf-8"), headers={"Content-Type": content_type}
        ),
    )
    with pytest.raises(gemini_client.GeminiRequestError) as failure:
        _request()
    assert failure.value.status_code is None
    assert failure.value.reason_code == reason_code
    assert PRIVATE_MARKER not in str(failure.value)


@pytest.mark.parametrize("base_name", ["APIError", "GenAiError"])
@pytest.mark.parametrize("status", [400, 413, 429, 599])
def test_moved_sdk_error_bases_are_recognized_through_their_mro(
    monkeypatch: pytest.MonkeyPatch, base_name: str, status: int
) -> None:
    _raise_from_client(monkeypatch, _sdk_error(base_name, status_code=status))
    with pytest.raises(gemini_client.GeminiRequestError) as failure:
        _request()
    assert failure.value.status_code == status
    assert failure.value.reason_code == ("input_too_large" if status == 413 else "api_error")
    assert PRIVATE_MARKER not in str(failure.value)


def test_api_error_base_at_public_sdk_package_root_is_recognized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moved_type = type("APIError", (Exception,), {"__module__": "google.genai"})
    error = moved_type(PRIVATE_MARKER)
    error.code = 429
    _raise_from_client(monkeypatch, error)
    with pytest.raises(gemini_client.GeminiRequestError) as failure:
        _request()
    assert failure.value.status_code == 429
    assert PRIVATE_MARKER not in str(failure.value)


def test_public_http_error_can_use_its_response_status_without_provider_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://gemini.invalid/interactions")
    response = httpx.Response(503, request=request, content=PRIVATE_MARKER)
    error = httpx.HTTPStatusError(PRIVATE_MARKER, request=request, response=response)
    _raise_from_client(monkeypatch, error)
    with pytest.raises(gemini_client.GeminiRequestError) as failure:
        _request()
    assert failure.value.status_code == 503
    assert failure.value.reason_code == "api_error"
    assert PRIVATE_MARKER not in str(failure.value)


@pytest.mark.parametrize(
    ("status_code", "code", "expected"),
    [(429, 503, 429), (None, 503, 503), (200, 429, 429), (True, 413, 413), ("429", 503, 503)],
)
def test_only_valid_http_integer_status_attributes_are_used(
    monkeypatch: pytest.MonkeyPatch, status_code: Any, code: Any, expected: int
) -> None:
    _raise_from_client(monkeypatch, _sdk_error(status_code=status_code, code=code))
    with pytest.raises(gemini_client.GeminiRequestError) as failure:
        _request()
    assert failure.value.status_code == expected
    assert str(failure.value) == f"Gemini API returned status {expected}."


@pytest.mark.parametrize(
    "invalid_status", [None, False, True, -1, 0, 200, 399, 600, 999, "429", 429.0, PRIVATE_MARKER]
)
def test_unknown_or_non_http_status_is_not_reported_as_a_provider_status(
    monkeypatch: pytest.MonkeyPatch, invalid_status: Any
) -> None:
    _raise_from_client(monkeypatch, _sdk_error(status_code=invalid_status, code=invalid_status))
    with pytest.raises(gemini_client.GeminiRequestError) as failure:
        _request()
    assert failure.value.status_code is None
    assert failure.value.reason_code == "api_error"
    assert str(failure.value) == "Gemini API request failed."
    assert PRIVATE_MARKER not in str(failure.value)


@pytest.mark.parametrize(
    ("error_type", "reason_code"),
    [(httpx.ConnectTimeout, "api_timeout"), (httpx.ConnectError, "api_connection_error")],
)
def test_installed_sdk_transport_errors_preserve_safe_reason_codes(
    monkeypatch: pytest.MonkeyPatch, error_type: type[httpx.RequestError], reason_code: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type(PRIVATE_MARKER, request=request)

    _mock_sdk(monkeypatch, handler)
    with pytest.raises(gemini_client.GeminiRequestError) as failure:
        _request()
    assert failure.value.status_code is None
    assert failure.value.reason_code == reason_code
    assert PRIVATE_MARKER not in str(failure.value)


@pytest.mark.parametrize(
    ("cause", "reason_code"),
    [(httpx.ReadTimeout, "api_timeout"), (httpx.ConnectError, "api_connection_error")],
)
def test_moved_sdk_errors_keep_public_transport_cause_classification(
    monkeypatch: pytest.MonkeyPatch, cause: type[httpx.RequestError], reason_code: str
) -> None:
    error = _sdk_error("GenAiError")
    error.__cause__ = cause(PRIVATE_MARKER)
    _raise_from_client(monkeypatch, error)
    with pytest.raises(gemini_client.GeminiRequestError) as failure:
        _request()
    assert failure.value.reason_code == reason_code
    assert failure.value.status_code is None
    assert PRIVATE_MARKER not in str(failure.value)


@pytest.mark.parametrize(
    ("name", "base", "module"),
    [
        ("ValueError", ValueError, "builtins"),
        ("TypeError", TypeError, "builtins"),
        ("AttributeError", AttributeError, "builtins"),
        ("CustomFailure", RuntimeError, "pipeline.example"),
        ("APIError", RuntimeError, "another_provider.errors"),
        ("GenAiError", RuntimeError, "google.genai_unrelated.errors"),
        ("ValueError", ValueError, "google.genai.future_interactions.errors"),
        ("AttributeError", AttributeError, "google.genai.future_interactions.errors"),
    ],
)
def test_programming_and_unrelated_provider_errors_are_not_disguised_as_api_failures(
    monkeypatch: pytest.MonkeyPatch, name: str, base: type[Exception], module: str
) -> None:
    exception_type = type(name, (base,), {"__module__": module})
    error = exception_type("a programming or unrelated provider failure")
    error.status_code = 429
    _raise_from_client(monkeypatch, error)
    with pytest.raises(exception_type) as failure:
        _request()
    assert failure.value is error
    assert not isinstance(failure.value, gemini_client.GeminiRequestError)


def test_application_and_tests_do_not_import_private_google_sdk_modules() -> None:
    violations: list[str] = []
    for folder in (PIPELINE_ROOT / "src", PIPELINE_ROOT / "tests"):
        for file_path in folder.rglob("*.py"):
            tree = ast.parse(file_path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                modules = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module]
                    if isinstance(node, ast.ImportFrom) and node.module
                    else []
                )
                for module in modules:
                    parts = module.split(".")
                    if parts[:2] == ["google", "genai"] and any(
                        part.startswith("_") for part in parts[2:]
                    ):
                        violations.append(
                            f"{file_path.relative_to(PIPELINE_ROOT)}:{node.lineno}: {module}"
                        )
    assert not violations, "Direct private SDK imports remain: " + "; ".join(violations)


def test_locked_sdk_public_contract_imports_in_a_fresh_process() -> None:
    lock = tomllib.loads((PIPELINE_ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked_version = next(
        package["version"] for package in lock["package"] if package["name"] == "google-genai"
    )
    assert metadata.version("google-genai") == locked_version
    script = """
from importlib.metadata import version
from google import genai
from google.genai import errors, types
from pipeline.transform.gemini_client import generate_summary_json
assert callable(genai.Client)
assert issubclass(errors.APIError, Exception)
assert types.HttpOptions(timeout=60000).timeout == 60000
assert callable(generate_summary_json)
print(version('google-genai'))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PIPELINE_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == locked_version


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
@pytest.mark.parametrize("header", ["retry-after", "retry-after-ms"])
def test_locked_interactions_sdk_sends_once_and_preserves_server_delay(
    monkeypatch, status, header,
) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status, headers={header: "3600" if header == "retry-after" else "3600000"},
            json={"error": {"code": status, "message": PRIVATE_MARKER}},
        )

    before = datetime.now(UTC)
    _mock_sdk(monkeypatch, handler)
    with pytest.raises(gemini_client.GeminiRequestError) as caught:
        _request()
    assert len(requests) == 1
    assert caught.value.retryable
    assert caught.value.failure_kind == "transient"
    assert caught.value.retry_at >= before + timedelta(hours=1)


def test_retry_after_date_and_milliseconds_keep_the_latest_valid_instant():
    now = datetime(2026, 10, 8, 12, tzinfo=UTC)
    assert retry_at_from_headers(httpx.Headers({
        "retry-after": "Thu, 08 Oct 2026 13:00:00 GMT", "retry-after-ms": "500",
    }), now=now) == now + timedelta(hours=1)
    assert retry_at_from_headers(httpx.Headers({
        "retry-after": "NaN", "retry-after-ms": "-1",
    }), now=now) is None
    assert retry_at_from_headers(httpx.Headers({
        "retry-after": "1e100",
    }), now=now) == datetime.max.replace(tzinfo=UTC)


def test_summary_public_boundary_preserves_deferred_error_metadata(monkeypatch):
    retry_at = datetime(2026, 10, 8, 13, tzinfo=UTC)
    calls = []

    def stopped(operation, payload):
        calls.append((operation, payload))
        raise GeminiExecutionError(
            "api_error", failure_kind="deferred", retryable=True,
            retry_at=retry_at, status_code=429,
        )

    monkeypatch.setattr(gemini_client, "run_gemini_request", stopped)
    with pytest.raises(gemini_client.GeminiRequestError) as caught:
        gemini_client.generate_summary_json(
            prompt="instructions", notice_text="notice data", api_key="test-key",
        )
    assert len(calls) == 1 and calls[0][0] == "summary"
    assert caught.value.failure_kind == "deferred"
    assert caught.value.retryable and caught.value.retry_at == retry_at
    assert caught.value.status_code == 429


def test_summary_invalid_input_does_not_launch_the_worker(monkeypatch):
    def forbidden(*_args):
        pytest.fail("invalid input must not launch a worker")

    monkeypatch.setattr(gemini_client, "run_gemini_request", forbidden)
    with pytest.raises(gemini_client.GeminiRequestError):
        gemini_client.generate_summary_json(prompt="", notice_text="notice", api_key="test-key")


@pytest.mark.parametrize("operation", ["summary", "easy_language"])
@pytest.mark.parametrize("status", [403, 429, 503])
def test_error_headers_are_classified_without_reading_the_response_body(
    monkeypatch, operation, status,
):
    from pipeline.glossary.easy_language_client import _generate_easy_language_json_direct

    class WaitingBody(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            pytest.fail("SDK must not wait for the body after an HTTP failure")
            yield b""  # pragma: no cover - keep this a stream iterator

        def close(self):
            self.closed = True

    stream = WaitingBody()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Retry-After": "3600"}, stream=stream)

    before = datetime.now(UTC)
    _mock_sdk(monkeypatch, handler)
    generate = (
        gemini_client._generate_summary_json_direct
        if operation == "summary" else _generate_easy_language_json_direct
    )
    with pytest.raises(GeminiExecutionError) as caught:
        generate(prompt="instructions", notice_text="notice", api_key="fake-test-key")
    assert len(calls) == 1
    assert stream.closed
    assert caught.value.status_code == status
    if status in {429, 503}:
        assert caught.value.retry_at >= before + timedelta(hours=1)
        assert caught.value.retryable
    else:
        assert not caught.value.retryable
