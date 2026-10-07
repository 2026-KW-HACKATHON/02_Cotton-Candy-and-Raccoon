"""Build issue #14's input identity without downloads or database access."""

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from pipeline.storage.summary_record import AttachmentStatus, SummaryMetadata, SummaryRecordError
from pipeline.transform.gemini_prompt import SUMMARY_PROMPT_VERSION
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_files import PrivateSummaryFileManifest, manifest_snapshot


@dataclass(frozen=True, slots=True)
class SummaryAttachmentText:
    """Extracted text paired with the persisted file_key, not its display name."""

    file_key: str
    text: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.file_key, str)
            or self.file_key != self.file_key.strip()
            or not (
                (
                    self.file_key.startswith("id:")
                    and self.file_key[3:]
                    and self.file_key[3:] == self.file_key[3:].strip()
                )
                or re.fullmatch(r"url:[0-9a-f]{64}", self.file_key)
            )
        ):
            raise SummaryRecordError("invalid_summary_file_key")
        if not isinstance(self.text, str) or not self.text.strip():
            raise SummaryRecordError("invalid_attachment_text")


def compute_source_hash(
    body_text: str, attachment_texts: Sequence[SummaryAttachmentText] = ()
) -> str:
    """Hash exact body/attachment text in an unambiguous file_key-sorted envelope.

    One physical file can appear as both attachment and inline image. Identical
    duplicate texts are counted once; conflicting text for one key is rejected.
    File names and input order do not affect identity. PDF/image bytes and title
    are outside #14's current body-plus-extracted-text contract.
    """
    if not isinstance(body_text, str):
        raise SummaryRecordError("invalid_body_text")
    if not isinstance(attachment_texts, Sequence) or isinstance(attachment_texts, (str, bytes)):
        raise SummaryRecordError("invalid_attachment_text")
    by_key: dict[str, str] = {}
    for item in attachment_texts:
        if not isinstance(item, SummaryAttachmentText):
            raise SummaryRecordError("invalid_attachment_text")
        checked = SummaryAttachmentText(item.file_key, item.text)
        if checked.file_key in by_key and by_key[checked.file_key] != checked.text:
            raise SummaryRecordError("conflicting_attachment_text")
        by_key[checked.file_key] = checked.text
    payload = {
        "body_text": body_text,
        "attachments": [{"file_key": key, "text": by_key[key]} for key in sorted(by_key)],
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def resolve_attachment_status(*, total_file_count: int, read_file_count: int) -> AttachmentStatus:
    """Classify original/read file counts supplied by the preparation owner."""
    if (
        type(total_file_count) is not int
        or type(read_file_count) is not int
        or not 0 <= read_file_count <= total_file_count
    ):
        raise SummaryRecordError("invalid_attachment_counts")
    if total_file_count == 0:
        return "none"
    if read_file_count == 0:
        return "unread"
    return "all_read" if read_file_count == total_file_count else "partial"


def build_summary_metadata(
    *,
    body_text: str,
    attachment_texts: Sequence[SummaryAttachmentText] = (),
    total_file_count: int,
    read_file_count: int,
    model: str,
    prompt_version: str = SUMMARY_PROMPT_VERSION,
) -> SummaryMetadata:
    """Use the full file manifest counts, not just successfully extracted texts.

    The #13 owner must supply file_key associations and counts before integration;
    NoticeInput attachments alone cannot reveal unread files or PDF/image inputs.
    A file successfully prepared as media can count as read without extracted text.
    Public review decisions still depend on evidence verification separately.
    """
    status = resolve_attachment_status(
        total_file_count=total_file_count, read_file_count=read_file_count
    )
    source_hash = compute_source_hash(body_text, attachment_texts)
    if len({item.file_key for item in attachment_texts}) > read_file_count:
        raise SummaryRecordError("invalid_attachment_counts")
    return SummaryMetadata(
        source_hash=source_hash,
        attachment_status=status,
        model=model,
        prompt_version=prompt_version,
    )


def build_summary_metadata_from_manifest(
    *,
    notice: NoticeInput,
    file_manifest: PrivateSummaryFileManifest,
    model: str,
    prompt_version: str = SUMMARY_PROMPT_VERSION,
) -> SummaryMetadata:
    """Count original DB rows while hashing extracted text once per file_key.

    Several original rows may use the same transmitted block. Media byte hashes
    protect the provenance binding; they do not change #14's source_hash contract.
    """
    try:
        manifest = manifest_snapshot(file_manifest)
        manifest.validate_text(notice)
    except (TypeError, ValueError):
        raise SummaryRecordError("invalid_prepared_input") from None
    texts = tuple(
        SummaryAttachmentText(item.file_key, notice.attachments[item.attachment_index].text)
        for item in manifest.files if item.outcome == "text"
    )
    return build_summary_metadata(
        body_text=notice.body_text,
        attachment_texts=texts,
        total_file_count=manifest.total_file_count,
        read_file_count=manifest.read_file_count,
        model=model,
        prompt_version=prompt_version,
    )
