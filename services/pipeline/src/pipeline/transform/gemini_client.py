"""Call Gemini for one stateless, structured notice summary."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

import httpx
from google import genai
from google.genai import errors, types

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

    Only calls that reach the SDK request are counted; input validation and
    configuration failures before it are not. A run reports gemini_called from this.
    """
    counter = [0]
    token = _request_counter.set(counter)
    try:
        yield counter
    finally:
        _request_counter.reset(token)


class GeminiRequestError(RuntimeError):
    """Gemini did not return a usable response."""

    def __init__(
        self, message: str, *, reason_code: str = "api_error", status_code: int | None = None
    ) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(message)


def _is_api_error(error: Exception) -> bool:
    """Recognize SDK API errors without importing its private exception modules.

    Interactions in the locked SDK uses a separate APIError/GenAiError hierarchy
    from google.genai.errors.APIError. Check API base classes in the SDK namespace,
    allowing its internal module path to move without suppressing ordinary bugs.
    """
    if isinstance(error, (errors.APIError, httpx.HTTPError)):
        return True
    return any(
        base.__name__ in {"APIError", "GenAiError"}
        and (base.__module__ == "google.genai" or base.__module__.startswith("google.genai."))
        for base in type(error).__mro__
    )


def _api_status(error: Exception) -> int | None:
    """Use only an actual HTTP failure status, never provider text or payload codes."""
    values = [getattr(error, "status_code", None), getattr(error, "code", None)]
    response = getattr(error, "response", None)
    if isinstance(response, httpx.Response):
        values.append(response.status_code)
    return next(
        (
            value
            for value in values
            if isinstance(value, int) and not isinstance(value, bool) and 400 <= value <= 599
        ),
        None,
    )


def _transport_reason(error: BaseException) -> str:
    """Recognize public HTTPX causes without retaining their request or message."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.TimeoutException):
            return "api_timeout"
        if isinstance(current, httpx.HTTPError):
            return "api_connection_error"
        current = current.__cause__
    return "api_error"


def generate_summary_json(
    *, prompt: str, notice_text: GeminiInput, api_key: str, model: str = DEFAULT_MODEL
) -> str:
    """Request JSON without retaining the interaction on Gemini's server.

    The locked Interactions SDK maps public retry attempts=1 to one retry, so
    one logical call can send two HTTP attempts. summarize.py permits at most
    two logical calls for response correction, for at most four HTTP attempts.
    """
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

    try:
        with (
            private_gemini_logging(),
            genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=REQUEST_TIMEOUT_MS,
                    # Without this explicit option the locked Interactions SDK
                    # sends up to four HTTP attempts for a single logical call.
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            ) as client,
        ):
            counter = _request_counter.get()
            if counter is not None:
                counter[0] += 1
            interaction = client.interactions.create(
                model=model,
                system_instruction=prompt,
                input=validated_input,
                response_format=response_format,
                store=False,
            )
    except Exception as exc:
        # Keep this boundary around the SDK call only. Unexpected programming or
        # configuration exceptions must retain their original type and traceback.
        if not _is_api_error(exc):
            raise
        status = _api_status(exc)
        if status is None:
            reason_code = _transport_reason(exc)
            raise GeminiRequestError(
                "Gemini API request failed.", reason_code=reason_code
            ) from None
        raise GeminiRequestError(
            f"Gemini API returned status {status}.",
            reason_code="input_too_large" if status == 413 else "api_error",
            status_code=status,
        ) from None

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
