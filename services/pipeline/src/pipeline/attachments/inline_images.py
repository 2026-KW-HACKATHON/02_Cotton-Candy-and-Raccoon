"""Prepare images in a notice body without changing its stored HTML or database."""

from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from pipeline.attachments.download import (
    MAX_IMAGE_BYTES,
    AttachmentDownloadError,
    DownloadedAttachment,
    download_deadline,
    download_image,
    normalize_download_url,
    remaining_seconds,
)
from pipeline.attachments.seoul_html import is_decorative_image_url
from pipeline.transform.html_text import html_to_notice_text

MAX_BODY_IMAGE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ImageFailure:
    image_index: int
    reason_code: str


@dataclass(frozen=True, slots=True)
class PreparedNoticeBody:
    body_text: str
    images: tuple[DownloadedAttachment, ...]
    failures: tuple[ImageFailure, ...]

    @property
    def complete(self) -> bool:
        return not self.failures


class _ImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            # A missing src is reported instead of silently treating an image-only
            # notice as having no source. Lazy loading/srcset are not inferred.
            self.sources.append(dict(attrs).get("src") or "")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def _normalize_image_url(notice_url: str, source: str) -> str:
    if not source.strip() or any(ord(char) < 32 or ord(char) == 127 for char in source):
        raise AttachmentDownloadError("invalid_url")
    try:
        return normalize_download_url(urljoin(notice_url, source.strip()), image=True)
    except ValueError:
        raise AttachmentDownloadError("invalid_url") from None


def prepare_notice_body(
    body_html: str | None,
    notice_url: str,
    *,
    client: httpx.Client | None = None,
    max_image_bytes: int = MAX_IMAGE_BYTES,
    max_total_bytes: int = MAX_BODY_IMAGE_BYTES,
    image_cache: dict[str, DownloadedAttachment | str] | None = None,
    deadline: float | None = None,
) -> PreparedNoticeBody:
    """Read img/src, download unique images, and report every failed reference.

    Identical URLs are fetched once. Identical returned bytes are included once,
    even when different references point at them. Budgets bound decoded bytes;
    the final Gemini caller must also budget PDFs, base64 and text together.
    image_cache, if supplied, is scoped to this preparation and can be reused by
    the DB file pass. Strings are safe failure codes, never URLs or provider errors.
    """
    for limit in (max_image_bytes, max_total_bytes):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("image byte limits must be positive integers")
    parser = _ImageParser()
    parser.feed(body_html or "")
    parser.close()
    images: list[DownloadedAttachment] = []
    failures: list[ImageFailure] = []
    outcomes: dict[str, str | None] = {}
    canonical: dict[bytes, DownloadedAttachment] = {}
    included: set[bytes] = set()
    total_bytes = 0
    cache = image_cache if image_cache is not None else {}
    end = download_deadline(180.0, deadline)

    def collect(active_client: httpx.Client | None) -> None:
        nonlocal total_bytes
        for index, source in enumerate(parser.sources, start=1):
            try:
                remaining_seconds(end)
                try:
                    resolved = urljoin(notice_url, source.strip())
                except ValueError:
                    raise AttachmentDownloadError("invalid_url") from None
                if is_decorative_image_url(resolved):
                    continue
                url = _normalize_image_url(notice_url, source)
                if url in outcomes:
                    if outcomes[url] is not None:
                        failures.append(ImageFailure(index, outcomes[url]))
                    continue
                remaining = max_total_bytes - total_bytes
                if remaining <= 0:
                    raise AttachmentDownloadError("total_size_limit")
                try:
                    cached = cache.get(url)
                    if isinstance(cached, str):
                        raise AttachmentDownloadError(cached)
                    image = (
                        cached
                        if cached is not None
                        else download_image(
                            url, client=active_client, max_bytes=min(max_image_bytes, remaining),
                            deadline=end,
                        )
                    )
                    if len(image.data) > min(max_image_bytes, remaining):
                        raise AttachmentDownloadError("too_large")
                    digest = sha256(image.data).digest()
                    image = canonical.setdefault(digest, image)
                    cache[url] = image
                except AttachmentDownloadError as exc:
                    reason = exc.reason_code
                    if reason == "too_large" and remaining < max_image_bytes:
                        reason = "total_size_limit"
                    outcomes[url] = reason
                    cache[url] = reason
                    raise AttachmentDownloadError(reason) from None
                outcomes[url] = None
                if digest not in included:
                    included.add(digest)
                    images.append(image)
                    total_bytes += len(image.data)
            except AttachmentDownloadError as exc:
                failures.append(ImageFailure(index, exc.reason_code))

    if parser.sources:
        collect(client)
    return PreparedNoticeBody(html_to_notice_text(body_html), tuple(images), tuple(failures))
