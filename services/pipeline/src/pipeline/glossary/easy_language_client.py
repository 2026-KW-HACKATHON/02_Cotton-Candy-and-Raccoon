"""Public Gemini Generate Content API for contextual notice term replacements."""

from google import genai
from google.genai import types

from pipeline.glossary.easy_language import (
    DEFAULT_MODEL,
    EasyLanguageAPIError,
    EasyLanguageConfigurationError,
    EasyLanguageResponse,
)

REQUEST_TIMEOUT_MS = 60_000


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def generate_easy_language_json(
    *, prompt: str, notice_text: str, api_key: str, model: str = DEFAULT_MODEL
) -> str:
    """Send exact text as data with separate instructions and a required JSON schema.

    The public ``models.generate_content`` endpoint is stateless. Disable SDK
    retries so the caller's one validation retry is the only extra request.
    """
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
    try:
        with genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=REQUEST_TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        ) as client:
            response = client.models.generate_content(
                model=model,
                contents=notice_text,
                config=types.GenerateContentConfig(
                    system_instruction=prompt,
                    response_mime_type="application/json",
                    response_json_schema=EasyLanguageResponse.model_json_schema(),
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
    except Exception:
        # Includes public SDK APIError, network/httpx errors, parsing and cleanup errors.
        # Never reflect remote data, credentials, HTTP headers or exception messages.
        raise EasyLanguageAPIError("Gemini easy-language request failed.") from None
    return output
