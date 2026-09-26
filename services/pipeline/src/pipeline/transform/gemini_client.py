"""Call Gemini for one stateless, structured notice summary."""

from google import genai
from google.genai import errors, types
from google.genai._gaos.lib.compat_errors import APIError as InteractionAPIError

from pipeline.transform.summary_schema import NoticeSummary

DEFAULT_MODEL = "gemini-3.5-flash-lite"
REQUEST_TIMEOUT_MS = 60_000


class GeminiRequestError(RuntimeError):
    """Gemini did not return a usable response."""


def generate_summary_json(
    *, prompt: str, notice_text: str, api_key: str, model: str = DEFAULT_MODEL
) -> str:
    """Request JSON without retaining the interaction on Gemini's server."""
    if not api_key.strip():
        raise GeminiRequestError("GEMINI_API_KEY is required.")
    if not prompt.strip() or not notice_text.strip():
        raise GeminiRequestError("Both prompt and notice text are required.")

    try:
        with genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        ) as client:
            interaction = client.interactions.create(
                model=model,
                system_instruction=prompt,
                input=notice_text,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": NoticeSummary.model_json_schema(),
                },
                store=False,
            )
    except (errors.APIError, InteractionAPIError) as exc:
        status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if status is None:
            raise GeminiRequestError("Gemini API request failed.") from None
        raise GeminiRequestError(f"Gemini API returned status {status}.") from None

    output = interaction.output_text
    if not isinstance(output, str) or not output.strip():
        raise GeminiRequestError("Gemini returned no summary text.")
    return output
