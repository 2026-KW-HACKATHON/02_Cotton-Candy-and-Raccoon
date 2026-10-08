"""Adapt downloaded PDFs/images to Gemini Interactions inline input blocks."""

from base64 import b64encode

from pipeline.attachments.download import DownloadedAttachment


def to_gemini_media_part(file: DownloadedAttachment) -> dict[str, str]:
    """Return one document/image block for the teammate's multimodal caller.

    HWP must first be extracted as text. This adapter neither invokes the model
    nor changes the existing text-only summarizer and grounding contract.
    """
    if file.media_type == "application/pdf":
        kind = "document"
    elif file.media_type in {"image/png", "image/jpeg", "image/webp"}:
        kind = "image"
    else:
        raise ValueError("HWP requires text extraction before Gemini input")
    if not file.data:
        raise ValueError("media data must not be empty")
    return {
        "type": kind, "mime_type": file.media_type, "data": b64encode(file.data).decode("ascii")
    }
