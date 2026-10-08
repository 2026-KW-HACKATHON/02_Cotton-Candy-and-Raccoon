"""Call Gemini for one stateless, structured notice summary."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from google import genai
from google.genai import types

from pipeline.gemini_execution import GeminiExecutionError, run_gemini_request
from pipeline.gemini_sdk_adapter import (
    disable_interaction_retries,
    is_api_error,
    record_request,
    reject_failed_response,
    safe_api_failure,
)
from pipeline.transform.gemini_input import (
    GeminiInput,
    GeminiInputError,
    validate_gemini_input,
    validate_gemini_request_size,
)
from pipeline.transform.gemini_logging import private_gemini_logging
from pipeline.transform.summary_schema import GeminiNoticeSummary

DEFAULT_MODEL = "gemini-3.5-flash-lite"
REQUEST_TIMEOUT_MS = 60_000

_request_counter: ContextVar[list[int] | None] = ContextVar(
    "gemini_summary_request_counter", default=None
)


@contextmanager
def count_summary_requests() -> Iterator[list[int]]:
    """Count Gemini summary requests sent in this context, as ``counter[0]``.

    This legacy counter counts logical calls after input validation, not HTTP
    attempts. Execution statistics separately count actual transport dispatches.
    """
    counter = [0]
    token = _request_counter.set(counter)
    try:
        yield counter
    finally:
        _request_counter.reset(token)


class GeminiRequestError(GeminiExecutionError):
    """A safe Gemini failure with retry metadata for the execution caller."""

    def __init__(self, message: str, *, reason_code: str = "api_error", **metadata) -> None:
        super().__init__(reason_code, **metadata)
        if self.reason_code != reason_code:
            self.reason_code = "summary_processing_failed"
        self.args = (message,)


def _request_error(error: GeminiExecutionError) -> GeminiRequestError:
    message = (
        f"Gemini API returned status {error.status_code}."
        if error.status_code is not None else {
            "empty_response": "Gemini returned no summary text.",
            "response_incomplete": "Gemini interaction did not complete.",
        }.get(error.reason_code, "Gemini API request failed.")
    )
    return GeminiRequestError(
        message, reason_code=error.reason_code, failure_kind=error.failure_kind,
        retryable=error.retryable, retry_at=error.retry_at, status_code=error.status_code,
    )


def generate_summary_json(
    *, prompt: str, notice_text: GeminiInput, api_key: str, model: str = DEFAULT_MODEL
) -> str:
    """Validate locally, then run bounded Gemini I/O in an isolated worker."""
    validated_input, _ = _validate_request(prompt, notice_text, api_key, model)
    counter = _request_counter.get()
    if counter is not None:
        counter[0] += 1
    try:
        return run_gemini_request("summary", {
            "prompt": prompt, "notice_text": validated_input, "api_key": api_key, "model": model,
        })
    except GeminiExecutionError as error:
        raise _request_error(error) from None


def _validate_request(prompt: str, notice_text: GeminiInput, api_key: str, model: str):
    """Share validation with the child without allowing invalid inputs to launch it."""
    if not api_key.strip():
        raise GeminiRequestError("GEMINI_API_KEY is required.", reason_code="missing_api_key")
    if not prompt.strip():
        raise GeminiRequestError(
            "Both prompt and notice text are required.", reason_code="empty_prompt"
        )

    response_format = {
        "type": "text",
        "mime_type": "application/json",
        "schema": GeminiNoticeSummary.model_json_schema(),
    }
    try:
        validated_input = validate_gemini_input(notice_text)
        validate_gemini_request_size(
            prompt=prompt,
            notice_input=validated_input,
            model=model,
            response_format=response_format,
        )
    except GeminiInputError as exc:
        raise GeminiRequestError(exc.reason_code, reason_code=exc.reason_code) from None

    return validated_input, response_format


def _generate_summary_json_direct(
    *, prompt: str, notice_text: GeminiInput, api_key: str, model: str = DEFAULT_MODEL
) -> str:
    """One SDK attempt inside the worker; never use directly for production jobs."""
    validated_input, response_format = _validate_request(prompt, notice_text, api_key, model)
    try:
        with (
            private_gemini_logging(),
            genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=REQUEST_TIMEOUT_MS,
                    retry_options=types.HttpRetryOptions(attempts=1),
                    client_args={"event_hooks": {
                        "request": [record_request], "response": [reject_failed_response],
                    }},
                ),
            ) as client,
        ):
            disable_interaction_retries(client.interactions)
            interaction = client.interactions.create(
                model=model,
                system_instruction=prompt,
                input=validated_input,
                response_format=response_format,
                store=False,
            )
    except GeminiExecutionError as error:
        raise _request_error(error) from None
    except Exception as error:
        if not is_api_error(error):
            raise
        raise _request_error(safe_api_failure(error)) from None

    if getattr(interaction, "status", None) != "completed":
        raise GeminiRequestError(
            "Gemini interaction did not complete.", reason_code="response_incomplete"
        )
    if getattr(interaction, "errors", None):
        raise GeminiRequestError("Gemini interaction reported an API error.")
    output = getattr(interaction, "output_text", None)
    if not isinstance(output, str) or not output.strip():
        raise GeminiRequestError("Gemini returned no summary text.", reason_code="empty_response")
    return output
