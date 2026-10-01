"""Bounded downloads from the public Nowon notice attachment endpoint."""

from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Literal
from urllib.parse import parse_qs, urlsplit

import httpx

MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
ImageMediaType = Literal["image/png", "image/jpeg", "image/webp"]
_FILE_PATH = "/component/file/ND_fileDownload.do"
_OLE_SIGNATURE = bytes.fromhex("D0 CF 11 E0 A1 B1 1A E1")
_CONTENT_TYPES = {
    "pdf": {"application/pdf"},
    "hwp": {"application/x-hwp", "application/x-hwp-v5", "application/haansofthwp"},
    "image": {"image/png", "image/jpeg", "image/webp"},
}
_GENERIC_CONTENT_TYPES = {"", "application/octet-stream", "binary/octet-stream"}


class AttachmentDownloadError(ValueError):
    """A safe, URL-free explanation of why an attachment could not be downloaded."""

    def __init__(self, reason_code: str, *, status_code: int | None = None) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(f"첨부파일 다운로드 실패: {reason_code}")


@dataclass(frozen=True, slots=True)
class DownloadedAttachment:
    name: str
    media_type: Literal[
        "application/pdf", "application/x-hwp", "image/png", "image/jpeg", "image/webp"
    ]
    data: bytes = field(repr=False)


def _validate_source_url(url: str, *, image: bool = False) -> None:
    if (
        not isinstance(url, str) or not url or url != url.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in url)
    ):
        raise AttachmentDownloadError("invalid_url")
    try:
        parsed = urlsplit(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname == "www.nowon.kr"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and (
                (
                    parsed.path == _FILE_PATH
                    and len(query.get("q_fileSn", [])) == 1
                    and query["q_fileSn"][0].isdigit()
                    and len(query.get("q_fileId", [])) == 1
                    and bool(query["q_fileId"][0].strip())
                )
                or (image and parsed.path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")))
            )
        )
    except ValueError:
        valid = False
    if not valid:
        raise AttachmentDownloadError("invalid_url")


def _expected_format(file_name: str) -> Literal["pdf", "hwp"]:
    if not isinstance(file_name, str) or not file_name.strip():
        raise AttachmentDownloadError("invalid_name")
    suffix = PurePath(file_name).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".hwp":
        return "hwp"
    raise AttachmentDownloadError("unsupported_type")


def _download(
    client: httpx.Client,
    url: str,
    file_name: str,
    expected_format: Literal["pdf", "hwp", "image"],
    max_bytes: int,
) -> DownloadedAttachment:
    try:
        with client.stream(
            "GET", url, follow_redirects=False, timeout=httpx.Timeout(20.0, connect=5.0)
        ) as response:
            if response.is_redirect:
                raise AttachmentDownloadError("redirect")
            if response.status_code == 429:
                raise AttachmentDownloadError("rate_limited", status_code=429)
            if response.status_code != 200:
                raise AttachmentDownloadError("http_error", status_code=response.status_code)

            content_length = response.headers.get("content-length", "")
            if content_length.isdigit() and int(content_length) > max_bytes:
                raise AttachmentDownloadError("too_large")

            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type not in _GENERIC_CONTENT_TYPES | _CONTENT_TYPES[expected_format]:
                raise AttachmentDownloadError("type_mismatch")

            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise AttachmentDownloadError("too_large")
                chunks.append(chunk)
    except httpx.TimeoutException:
        raise AttachmentDownloadError("timeout") from None
    except httpx.RequestError:
        raise AttachmentDownloadError("request_failed") from None

    data = b"".join(chunks)
    if not data:
        raise AttachmentDownloadError("empty_file")
    if expected_format == "pdf" and data.startswith(b"%PDF-"):
        return DownloadedAttachment(file_name, "application/pdf", data)
    if expected_format == "hwp" and data.startswith(_OLE_SIGNATURE):
        return DownloadedAttachment(file_name, "application/x-hwp", data)
    if expected_format == "image":
        media_type = _image_media_type(data)
        if media_type is not None and content_type in _GENERIC_CONTENT_TYPES | {media_type}:
            extension = media_type.split("/", 1)[1]
            return DownloadedAttachment(file_name or f"image.{extension}", media_type, data)
    raise AttachmentDownloadError("type_mismatch")


def _image_media_type(data: bytes) -> ImageMediaType | None:
    """Check the file signature; complete image decoding is left to the consumer."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def download_attachment(
    url: str,
    file_name: str,
    *,
    client: httpx.Client | None = None,
    max_bytes: int = MAX_ATTACHMENT_BYTES,
) -> DownloadedAttachment:
    """Fetch one PDF/HWP into bounded memory without following redirects.

    The caller must hand the returned bytes to an extractor; this function does
    not access the DB, Gemini, or the local filesystem.
    """
    _validate_source_url(url)
    expected_format = _expected_format(file_name)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    if client is not None:
        return _download(client, url, file_name, expected_format, max_bytes)
    with httpx.Client() as owned_client:
        return _download(owned_client, url, file_name, expected_format, max_bytes)


def download_image(
    url: str, *, client: httpx.Client | None = None, max_bytes: int = MAX_IMAGE_BYTES
) -> DownloadedAttachment:
    """Fetch an official PNG/JPEG/WebP, including images without a filename."""
    _validate_source_url(url, image=True)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    if client is not None:
        return _download(client, url, "", "image", max_bytes)
    with httpx.Client() as owned_client:
        return _download(owned_client, url, "", "image", max_bytes)
