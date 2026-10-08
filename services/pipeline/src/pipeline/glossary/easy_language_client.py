"""Public Gemini Generate Content API for question-section notice rewrites."""

from google import genai
from google.genai import types

from pipeline.gemini_execution import GeminiExecutionError, run_gemini_request
from pipeline.gemini_sdk_adapter import (
    is_api_error,
    record_request,
    reject_failed_response,
    safe_api_failure,
)
from pipeline.glossary.easy_language import (
    DEFAULT_MODEL,
    EasyLanguageAPIError,
    EasyLanguageConfigurationError,
    EasyRewriteResponse,
)
from pipeline.transform.gemini_logging import private_gemini_logging

REQUEST_TIMEOUT_MS = 60_000


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def generate_easy_language_json(
    *, prompt: str, notice_text: str, api_key: str, model: str = DEFAULT_MODEL
) -> str:
    """Run this logical request inside the caller's shared execution budget."""
    _validate_request(prompt, notice_text, api_key, model)
    try:
        return run_gemini_request("easy_language", {
            "prompt": prompt, "notice_text": notice_text, "api_key": api_key, "model": model,
        })
    except GeminiExecutionError as error:
        raise _api_error(error) from None


def _api_error(error: GeminiExecutionError) -> EasyLanguageAPIError:
    return EasyLanguageAPIError(
        "Gemini easy-language request failed.", reason_code=error.reason_code,
        failure_kind=error.failure_kind, retryable=error.retryable,
        retry_at=error.retry_at, status_code=error.status_code,
    )


def _validate_request(prompt: str, notice_text: str, api_key: str, model: str) -> None:
    if not isinstance(api_key, str) or not api_key.strip():
        raise EasyLanguageConfigurationError("GEMINI_API_KEY is required.")
    if not isinstance(model, str) or not model.strip():
        raise EasyLanguageConfigurationError("Gemini model is required.")
    if (
        not isinstance(prompt, str)
        or not prompt.strip()
        or not isinstance(notice_text, str)
        or not notice_text.strip()
    ):
        raise EasyLanguageConfigurationError("Gemini prompt and notice text are required.")


def _generate_easy_language_json_direct(
    *, prompt: str, notice_text: str, api_key: str, model: str = DEFAULT_MODEL
) -> str:
    """One SDK attempt inside the worker, including its response contract check."""
    _validate_request(prompt, notice_text, api_key, model)
    try:
        with private_gemini_logging(), genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=REQUEST_TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(attempts=1),
                client_args={"event_hooks": {
                    "request": [record_request], "response": [reject_failed_response],
                }},
            ),
        ) as client:
            response = client.models.generate_content(
                model=model,
                contents=notice_text,
                config=types.GenerateContentConfig(
                    system_instruction=prompt,
                    response_mime_type="application/json",
                    response_json_schema=EasyRewriteResponse.model_json_schema(),
                    candidate_count=1,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            feedback = getattr(response, "prompt_feedback", None)
            block_reason = _enum_value(getattr(feedback, "block_reason", None))
            if block_reason not in (None, "", "BLOCKED_REASON_UNSPECIFIED"):
                raise ValueError("Blocked Gemini response.")
            candidates = getattr(response, "candidates", None)
            if not candidates or len(candidates) != 1:
                raise ValueError("Gemini must return one completed candidate.")
            if _enum_value(getattr(candidates[0], "finish_reason", None)) != "STOP":
                raise ValueError("Incomplete or blocked Gemini candidate.")
            output = response.text
            if not isinstance(output, str) or not output.strip():
                raise ValueError("Empty Gemini text.")
    except GeminiExecutionError as error:
        raise _api_error(error) from None
    except Exception as error:
        # Includes malformed responses and cleanup errors, without retaining private data.
        if is_api_error(error):
            raise _api_error(safe_api_failure(error)) from None
        raise EasyLanguageAPIError("Gemini easy-language request failed.") from None
    return output
