"""Prepare stored material without writing to DB or invoking Gemini."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from pathlib import PurePath, PurePosixPath
from typing import Literal
from urllib.parse import unquote, urlsplit

import httpx

from pipeline.attachments.download import (
    MAX_ATTACHMENT_BYTES,
    MAX_IMAGE_BYTES,
    AttachmentDownloadError,
    DownloadedAttachment,
    download_attachment,
    download_image,
    normalize_download_url,
)
from pipeline.attachments.hwp_text import HwpExtractionError, extract_hwp_text
from pipeline.attachments.inline_images import prepare_notice_body
from pipeline.attachments.seoul_html import is_decorative_image_url
from pipeline.storage.summary_source import SummarySource
from pipeline.transform.media_input import to_gemini_media_part
from pipeline.transform.notice_input import AttachmentText, NoticeInput, render_notice_input
from pipeline.transform.summary_files import (
    PreparedMediaBinding,
    PreparedSourceFile,
    PrivateSummaryFileManifest,
)

MAX_PREPARED_INPUT_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PreparationIssue:
    stage: str
    item_id: int
    reason_code: str


@dataclass(frozen=True, slots=True)
class PreparedSummary:
    notice_id: int
    notice: NoticeInput = field(repr=False)
    media: tuple[DownloadedAttachment, ...] = field(repr=False)
    failures: tuple[PreparationIssue, ...]
    warnings: tuple[PreparationIssue, ...]
    file_manifest: PrivateSummaryFileManifest = field(repr=False)

    @property
    def complete(self) -> bool:
        """No preparation failure; warnings still require caller review."""
        return not self.failures

    def to_gemini_input(self) -> list[dict[str, str]]:
        """Build Interactions blocks, but do not send them to Gemini."""
        if not self.complete:
            raise ValueError("summary input preparation is incomplete")
        if not (self.notice.body_text.strip() or self.notice.attachments or self.media):
            raise ValueError("summary source has no content")
        return [{"type": "text", "text": render_notice_input(self.notice)}] + [
            to_gemini_media_part(item) for item in self.media
        ]


@dataclass(frozen=True, slots=True)
class _RetainedContent:
    outcome: Literal["media", "text"]
    index: int
    content_sha256: str


def prepare_summary_source(
    source: SummarySource,
    *,
    reference_datetime: datetime,
    client: httpx.Client | None = None,
    max_input_bytes: int = MAX_PREPARED_INPUT_BYTES,
) -> PreparedSummary:
    """Download files, extract HWP, deduplicate bytes, retain failure/warning codes.

    Unknown formats fail rather than disappear. Missing names can be inferred
    from explicit URL filenames, never from ambiguous endpoints. Body images absent from
    notice_files are also read. The budget includes text and base64 growth;
    it is a local safety cap, not a claim about Gemini's model-specific limits.
    Preserve the source revision and every original file row, including aliases
    of deduplicated inputs. The manifest binds the bytes/text actually retained;
    failed files remain unread. It never substitutes a freshly queried revision.
    """
    if (
        isinstance(max_input_bytes, bool)
        or not isinstance(max_input_bytes, int)
        or max_input_bytes < 1
    ):
        raise ValueError("max_input_bytes must be a positive integer")
    notice = NoticeInput(
        title=source.title,
        body_text="",
        reference_datetime=reference_datetime,
        department=source.department,
        published_on=source.registered_on,
    )
    # Check source identities before any request. Keep the exact stored URLs;
    # download normalization must not change the identities checked by storage.
    manifest = PrivateSummaryFileManifest(
        notice_id=source.notice_id,
        source_revision=source.content_revision,
        original_url=source.url,
        files=tuple(PreparedSourceFile(
            notice_file_id=item.id, file_key=item.file_key, kind=item.kind,
            url=item.url, outcome="unread",
        ) for item in source.files),
        media=(),
    )
    file_outcomes = list(manifest.files)
    bindings: list[PreparedMediaBinding] = []
    failures: list[PreparationIssue] = []
    warnings: list[PreparationIssue] = []
    media: list[DownloadedAttachment] = []
    texts: list[AttachmentText] = []
    retained: dict[bytes, _RetainedContent] = {}
    urls: dict[str, _RetainedContent | str] = {}
    image_cache: dict[str, DownloadedAttachment | str] = {}
    retained_bytes = 0
    text_bytes = 0

    def retain(file: DownloadedAttachment, item_id: int) -> _RetainedContent:
        nonlocal retained_bytes, text_bytes
        digest = sha256(file.data).digest()
        if digest in retained:
            return retained[digest]
        if retained_bytes + len(file.data) > max_input_bytes:
            raise AttachmentDownloadError("total_size_limit")
        if file.media_type == "application/x-hwp":
            result = extract_hwp_text(file)
            extracted_bytes = len(result.attachment.text.encode("utf-8"))
            if text_bytes + extracted_bytes > max_input_bytes:
                raise AttachmentDownloadError("total_size_limit")
            text_bytes += extracted_bytes
            content = _RetainedContent(
                "text", len(texts), sha256(result.attachment.text.encode("utf-8")).hexdigest(),
            )
            texts.append(result.attachment)
            warnings.extend(PreparationIssue("hwp", item_id, code) for code in result.warnings)
        else:
            content = _RetainedContent("media", len(media), digest.hex())
            bindings.append(PreparedMediaBinding(
                source_id=f"media_{len(media) + 1}",
                source_type="document" if file.media_type == "application/pdf" else "image",
                input_block_index=len(media) + 1,
                content_sha256=digest.hex(),
            ))
            media.append(file)
        retained_bytes += len(file.data)
        retained[digest] = content
        return content

    def bind(index: int, content: _RetainedContent) -> None:
        item = manifest.files[index]
        file_outcomes[index] = PreparedSourceFile(
            notice_file_id=item.notice_file_id, file_key=item.file_key,
            kind=item.kind, url=item.url, outcome=content.outcome,
            source_id=f"media_{content.index + 1}" if content.outcome == "media" else None,
            attachment_index=content.index if content.outcome == "text" else None,
            content_sha256=content.content_sha256,
        )

    def collect(active_client: httpx.Client) -> None:
        nonlocal text_bytes
        body = prepare_notice_body(
            source.body_html,
            source.url,
            client=active_client,
            image_cache=image_cache,
            max_total_bytes=min(max_input_bytes, MAX_IMAGE_BYTES),
        )
        notice.body_text = body.body_text
        text_bytes = len(body.body_text.encode("utf-8"))
        failures.extend(
            PreparationIssue("body_image", item.image_index, item.reason_code)
            for item in body.failures
        )
        for image in body.images:
            try:
                retain(image, 0)
            except AttachmentDownloadError as exc:
                failures.append(PreparationIssue("body_image", 0, exc.reason_code))
        for index, item in enumerate(source.files):
            url = item.url
            try:
                if item.kind == "inline_image" and is_decorative_image_url(url):
                    warnings.append(PreparationIssue("file", item.id, "decorative_image_ignored"))
                    continue
                try:
                    filename = item.file_name or PurePosixPath(unquote(urlsplit(url).path)).name
                except ValueError:
                    raise AttachmentDownloadError("invalid_url") from None
                suffix = PurePath(filename).suffix.lower()
                is_image = item.kind == "inline_image" or suffix in {
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp",
                }
                url = normalize_download_url(url, image=is_image)
                if url in urls:
                    previous = urls[url]
                    if isinstance(previous, str):
                        failures.append(PreparationIssue("file", item.id, previous))
                    else:
                        bind(index, previous)
                    continue
                cached = image_cache.get(url)
                if isinstance(cached, str):
                    raise AttachmentDownloadError(cached)
                if cached is not None:
                    content = retain(cached, item.id)
                    bind(index, content)
                    urls[url] = content
                    continue
                remaining = max_input_bytes - retained_bytes
                if remaining < 1:
                    raise AttachmentDownloadError("total_size_limit")
                if is_image:
                    file = download_image(
                        url, client=active_client, max_bytes=min(remaining, MAX_IMAGE_BYTES)
                    )
                else:
                    file = download_attachment(
                        url,
                        filename,
                        client=active_client,
                        max_bytes=min(remaining, MAX_ATTACHMENT_BYTES),
                    )
                content = retain(file, item.id)
                bind(index, content)
                urls[url] = content
            except (AttachmentDownloadError, HwpExtractionError) as exc:
                urls[url] = exc.reason_code
                failures.append(PreparationIssue("file", item.id, exc.reason_code))

    if client is None:
        with httpx.Client() as owned_client:
            collect(owned_client)
    else:
        collect(client)
    notice.attachments = texts
    # Match json.dumps's default escaping/separators without allocating base64.
    size = len(json.dumps([{"type": "text", "text": render_notice_input(notice)}]).encode("utf-8"))
    for file in media:
        block = {
            "type": "document" if file.media_type == "application/pdf" else "image",
            "mime_type": file.media_type,
            "data": "",
        }
        size += len(json.dumps(block).encode("utf-8")) + 4 * ((len(file.data) + 2) // 3) + 2
    if size > max_input_bytes:
        failures.append(PreparationIssue("input", 0, "total_size_limit"))
    if not (notice.body_text.strip() or texts or media):
        failures.append(PreparationIssue("input", 0, "no_content"))
    manifest = PrivateSummaryFileManifest(
        notice_id=manifest.notice_id, source_revision=manifest.source_revision,
        original_url=manifest.original_url, files=tuple(file_outcomes), media=tuple(bindings),
    )
    return PreparedSummary(
        source.notice_id, notice, tuple(media), tuple(failures), tuple(warnings), manifest,
    )
