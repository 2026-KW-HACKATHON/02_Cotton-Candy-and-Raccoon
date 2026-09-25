"""Load the resident-facing Gemini notice prompt and local API credential.

This module performs no network calls. Callers can use the two loaders when the
Gemini summarization step is connected to the collection pipeline.
"""

import os
from pathlib import Path

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "gemini_notice_summary.md"
DEFAULT_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


class GeminiConfigurationError(ValueError):
    """A required local prompt or credential is missing."""


def load_summary_prompt() -> str:
    """Return the checked-in prompt without changing its content."""
    try:
        prompt = PROMPT_PATH.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        raise GeminiConfigurationError("Gemini prompt file is missing.") from None
    if not prompt:
        raise GeminiConfigurationError("Gemini prompt file is empty.")
    return prompt


def load_gemini_api_key(dotenv_path: Path | None = None) -> str:
    """Read GEMINI_API_KEY from the process environment, then a local .env file.

    The key is never included in an error message. This intentionally reads only
    the one setting needed by this step and leaves other environment variables
    unchanged.
    """
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key

    path = dotenv_path or DEFAULT_ENV_PATH
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        raise GeminiConfigurationError("Set GEMINI_API_KEY or create services/pipeline/.env.") from None

    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != "GEMINI_API_KEY":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if value:
            return value

    raise GeminiConfigurationError("Set GEMINI_API_KEY in services/pipeline/.env.")
