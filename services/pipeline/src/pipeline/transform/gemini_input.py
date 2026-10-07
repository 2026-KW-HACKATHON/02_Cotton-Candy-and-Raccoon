"""Validate prepared Gemini content and preserve it during a retry."""

import base64
import binascii
import json

type GeminiInput = str | list[dict[str, str]]
MAX_GEMINI_INPUT_BYTES = 50 * 1024 * 1024
IMAGE_MIME_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/heic",
        "image/heif",
    }
)


class GeminiInputError(ValueError):
    """A safe reason code for an unusable or oversized prepared input."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _check_size(value: object) -> None:
    """Count the escaped JSON bytes used by the installed SDK's serializer."""
    try:
        encoded = json.dumps(value, ensure_ascii=True)
    except (TypeError, ValueError):
        raise GeminiInputError("invalid_input") from None
    if len(encoded) > MAX_GEMINI_INPUT_BYTES:
        raise GeminiInputError("input_too_large")


def validate_gemini_input(value: GeminiInput) -> GeminiInput:
    """Return a validated copy without reading files or changing the caller's blocks."""
    if isinstance(value, str):
        if not value.strip():
            raise GeminiInputError("empty_input")
        _check_size(value)
        return value
    if not isinstance(value, list):
        raise GeminiInputError("invalid_input")
    if not value:
        raise GeminiInputError("empty_input")

    copied: list[dict[str, str]] = []
    for block in value:
        if not isinstance(block, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in block.items()
        ):
            raise GeminiInputError("invalid_input_block")
        kind = block.get("type")
        if kind == "text":
            if set(block) != {"type", "text"}:
                raise GeminiInputError("invalid_input_block")
            if not block["text"].strip():
                raise GeminiInputError("empty_input_block")
        elif kind in {"document", "image"}:
            if set(block) != {"type", "data", "mime_type"}:
                raise GeminiInputError("invalid_input_block")
            allowed = {"application/pdf"} if kind == "document" else IMAGE_MIME_TYPES
            if block["mime_type"] not in allowed:
                raise GeminiInputError("unsupported_mime_type")
            if not block["data"]:
                raise GeminiInputError("empty_input_block")
        else:
            raise GeminiInputError("unsupported_input_block")
        copied.append(block.copy())

    # Reject oversized encoded data before allocating decoded binary copies.
    _check_size(copied)
    for block in copied:
        if block["type"] in {"document", "image"}:
            try:
                decoded = base64.b64decode(block["data"], validate=True)
            except (binascii.Error, ValueError):
                raise GeminiInputError("invalid_media_data") from None
            if not decoded:
                raise GeminiInputError("empty_input_block")
    return copied


def validate_gemini_request_size(
    *, prompt: str, notice_input: GeminiInput, model: str, response_format: dict
) -> None:
    """Include instructions, the response schema, and the SDK content-list wrapper."""
    wire_input = (
        [{"type": "user_input", "content": notice_input}]
        if isinstance(notice_input, list)
        else notice_input
    )
    _check_size(
        {
            "model": model,
            "system_instruction": prompt,
            "input": wire_input,
            "response_format": response_format,
            "store": False,
        }
    )


def append_retry_text(original: GeminiInput, feedback: str) -> GeminiInput:
    """Add correction instructions while retaining every original media block."""
    if not isinstance(feedback, str) or not feedback.strip():
        raise GeminiInputError("invalid_retry_text")
    copied = validate_gemini_input(original)
    if isinstance(copied, str):
        return validate_gemini_input(copied + "\n\n" + feedback)
    copied.append({"type": "text", "text": feedback})
    return validate_gemini_input(copied)
