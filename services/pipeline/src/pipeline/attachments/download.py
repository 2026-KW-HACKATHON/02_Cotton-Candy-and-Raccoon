"""Bounded downloads from explicitly allowed official notice file hosts."""

import asyncio
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import PurePath, PurePosixPath
from time import monotonic
from typing import Literal
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import httpx

from pipeline.attachments.seoul_html import normalize_seoul_news_url

MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_DOWNLOAD_SECONDS = 60.0
ImageMediaType = Literal["image/png", "image/jpeg", "image/webp"]
_FILE_PATH = "/component/file/ND_fileDownload.do"
SEOUL_FILE_HOSTS = frozenset({"news.seoul.go.kr", "culture.seoul.go.kr"})
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


def download_deadline(seconds: float, parent: float | None = None) -> float:
    """Use a monotonic elapsed-time budget shared by files in one notice."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not (
        math.isfinite(seconds) and seconds > 0
    ):
        raise ValueError("download seconds must be finite and positive")
    end = monotonic() + seconds
    return end if parent is None else min(end, parent)


def remaining_seconds(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise AttachmentDownloadError("time_limit")
    return remaining


@dataclass(frozen=True, slots=True)
class DownloadedAttachment:
    name: str
    media_type: Literal[
        "application/pdf", "application/x-hwp", "image/png", "image/jpeg", "image/webp"
    ]
    data: bytes = field(repr=False)


def _validate_source_url(url: str, *, image: bool = False) -> None:
    if (
        not isinstance(url, str)
        or not url
        or url != url.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in url)
    ):
        raise AttachmentDownloadError("invalid_url")
    try:
        parsed = urlsplit(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        valid = (
            parsed.scheme == "https"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and (
                (
                    parsed.hostname == "www.nowon.kr"
                    and (
                        (
                            parsed.path == _FILE_PATH
                            and len(query.get("q_fileSn", [])) == 1
                            and query["q_fileSn"][0].isdigit()
                            and len(query.get("q_fileId", [])) == 1
                            and bool(query["q_fileId"][0].strip())
                        )
                        or (
                            image
                            and parsed.path.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                        )
                    )
                )
                or (
                    parsed.hostname in SEOUL_FILE_HOSTS
                    and PurePosixPath(unquote(parsed.path)).suffix.lower()
                    in ({".png", ".jpg", ".jpeg", ".webp"} if image else {".pdf", ".hwp"})
                )
            )
        )
    except ValueError:
        valid = False
    if not valid:
        raise AttachmentDownloadError("invalid_url")


def normalize_download_url(url: str, *, image: bool = False) -> str:
    """Canonicalize Nowon and Seoul News HTTP consistently with collection."""
    if (
        not isinstance(url, str)
        or not url
        or url != url.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in url)
    ):
        raise AttachmentDownloadError("invalid_url")
    try:
        parsed = urlsplit(url)
        if (
            parsed.hostname == "www.nowon.kr"
            and parsed.scheme in {"http", "https"}
            and parsed.port in (None, 80, 443)
            and parsed.username is None
            and parsed.password is None
        ):
            url = urlunsplit(
                (
                    "https",
                    "www.nowon.kr",
                    parsed.path,
                    urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True))),
                    parsed.fragment,
                )
            )
        else:
            url = normalize_seoul_news_url(url)
    except ValueError:
        raise AttachmentDownloadError("invalid_url") from None
    _validate_source_url(url, image=image)
    return url


def _expected_format(file_name: str) -> Literal["pdf", "hwp"]:
    if not isinstance(file_name, str) or not file_name.strip():
        raise AttachmentDownloadError("invalid_name")
    suffix = PurePath(file_name).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".hwp":
        return "hwp"
    raise AttachmentDownloadError("unsupported_type")


def _response_type(response: httpx.Response, expected_format: str, max_bytes: int) -> str:
    if response.is_redirect:
        raise AttachmentDownloadError("redirect")
    if response.status_code == 429:
        raise AttachmentDownloadError("rate_limited", status_code=429)
    if 500 <= response.status_code <= 599:
        raise AttachmentDownloadError("server_error", status_code=response.status_code)
    if response.status_code != 200:
        raise AttachmentDownloadError("http_error", status_code=response.status_code)
    length = response.headers.get("content-length", "")
    if length.isdigit() and int(length) > max_bytes:
        raise AttachmentDownloadError("too_large")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in _GENERIC_CONTENT_TYPES | _CONTENT_TYPES[expected_format]:
        raise AttachmentDownloadError("type_mismatch")
    return content_type


def _download(
    client: httpx.Client,
    url: str,
    file_name: str,
    expected_format: Literal["pdf", "hwp", "image"],
    max_bytes: int,
    deadline: float,
) -> DownloadedAttachment:
    try:
        remaining = remaining_seconds(deadline)
        with client.stream(
            "GET", url, follow_redirects=False,
            timeout=httpx.Timeout(min(20.0, remaining), connect=min(5.0, remaining)),
        ) as response:
            remaining_seconds(deadline)
            content_type = _response_type(response, expected_format, max_bytes)

            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                remaining_seconds(deadline)
                total += len(chunk)
                if total > max_bytes:
                    raise AttachmentDownloadError("too_large")
                chunks.append(chunk)
            remaining_seconds(deadline)
    except httpx.TimeoutException:
        raise AttachmentDownloadError("timeout") from None
    except httpx.RequestError:
        raise AttachmentDownloadError("request_failed") from None

    return _materialize(b"".join(chunks), file_name, expected_format, content_type)


async def _download_owned(
    url: str, file_name: str, expected_format: str, max_bytes: int, deadline: float,
) -> DownloadedAttachment:
    """Cancel headers and body waits at the total deadline, closing owned resources."""
    try:
        async with asyncio.timeout(remaining_seconds(deadline)):
            async with httpx.AsyncClient() as client:
                remaining = remaining_seconds(deadline)
                async with client.stream(
                    "GET", url, follow_redirects=False,
                    timeout=httpx.Timeout(min(20.0, remaining), connect=min(5.0, remaining)),
                ) as response:
                    content_type = _response_type(response, expected_format, max_bytes)
                    chunks = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        remaining_seconds(deadline)
                        total += len(chunk)
                        if total > max_bytes:
                            raise AttachmentDownloadError("too_large")
                        chunks.append(chunk)
                    remaining_seconds(deadline)
        return _materialize(b"".join(chunks), file_name, expected_format, content_type)
    except TimeoutError:
        raise AttachmentDownloadError("time_limit") from None
    except httpx.TimeoutException:
        raise AttachmentDownloadError("timeout") from None
    except httpx.RequestError:
        raise AttachmentDownloadError("request_failed") from None


def _owned_download(
    url: str, file_name: str, expected_format: str, max_bytes: int, deadline: float,
) -> DownloadedAttachment:
    def run() -> DownloadedAttachment:
        return asyncio.run(_download_owned(url, file_name, expected_format, max_bytes, deadline))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return run()
    # Keep this public API synchronous even inside an application's event loop.
    # The child performs a cancellable bounded request and is always joined.
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(run).result()


def _materialize(
    data: bytes, file_name: str, expected_format: str, content_type: str,
) -> DownloadedAttachment:
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
    max_seconds: float = MAX_DOWNLOAD_SECONDS,
    deadline: float | None = None,
) -> DownloadedAttachment:
    """Fetch one PDF/HWP into bounded memory without following redirects.

    The caller must hand the returned bytes to an extractor; this function does
    not access the DB, Gemini, or the local filesystem.
    """
    _validate_source_url(url)
    expected_format = _expected_format(file_name)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    end = download_deadline(max_seconds, deadline)
    if client is not None:
        return _download(client, url, file_name, expected_format, max_bytes, end)
    return _owned_download(url, file_name, expected_format, max_bytes, end)


def download_image(
    url: str, *, client: httpx.Client | None = None, max_bytes: int = MAX_IMAGE_BYTES,
    max_seconds: float = MAX_DOWNLOAD_SECONDS, deadline: float | None = None,
) -> DownloadedAttachment:
    """Fetch an official PNG/JPEG/WebP, including images without a filename."""
    _validate_source_url(url, image=True)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    end = download_deadline(max_seconds, deadline)
    if client is not None:
        return _download(client, url, "", "image", max_bytes, end)
    return _owned_download(url, "", "image", max_bytes, end)
