"""Extract stored HWP 5.x paragraph text, including nested table paragraphs.

본 제품은 한글과컴퓨터의 한글 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.
Specification: https://cdn.hancom.com/link/docs/한글문서파일형식_5.0_revision1.3.pdf
"""

import re
import struct
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from io import BytesIO

import olefile

from pipeline.attachments.download import MAX_ATTACHMENT_BYTES, DownloadedAttachment
from pipeline.transform.notice_input import AttachmentText

MAX_EXPANDED_HWP_BYTES = 20 * 1024 * 1024
_LONG_CONTROLS = frozenset(range(1, 10)) | {11, 12} | frozenset(range(14, 24))
_NON_TEXT_TAGS = {84, 85, 88, 90, 95, 98, 115}


class HwpExtractionError(ValueError):
    """A safe failure code; never includes document text or a local path."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"HWP text extraction failed: {reason_code}")


@dataclass(frozen=True, slots=True)
class HwpTextResult:
    attachment: AttachmentText = field(repr=False)
    section_count: int
    warnings: tuple[str, ...]


def _records(data: bytes) -> Iterator[tuple[int, bytes]]:
    offset = 0
    while offset < len(data):
        if len(data) - offset < 4:
            raise HwpExtractionError("invalid_record")
        header = struct.unpack_from("<I", data, offset)[0]
        tag = header & 0x3FF
        size = header >> 20
        offset += 4
        if size == 0xFFF:
            if len(data) - offset < 4:
                raise HwpExtractionError("invalid_record")
            size = struct.unpack_from("<I", data, offset)[0]
            offset += 4
        if offset + size > len(data):
            raise HwpExtractionError("invalid_record")
        yield tag, data[offset : offset + size]
        offset += size


def _expand(data: bytes, compressed: bool, limit: int) -> bytes:
    if not compressed:
        if len(data) > limit:
            raise HwpExtractionError("expanded_size_limit")
        return data
    try:
        inflater = zlib.decompressobj(-15)
        expanded = inflater.decompress(data, limit + 1)
        if len(expanded) > limit or inflater.unconsumed_tail:
            raise HwpExtractionError("expanded_size_limit")
        if not inflater.eof:
            raise HwpExtractionError("invalid_compression")
        # The real Hancom 5.1 sample has a CRC32/size trailer after raw deflate.
        # Accept it only when both values match, never arbitrary trailing bytes.
        if inflater.unused_data and (
            len(inflater.unused_data) != 8
            or struct.unpack("<II", inflater.unused_data) != (zlib.crc32(expanded), len(expanded))
        ):
            raise HwpExtractionError("invalid_compression")
        return expanded
    except zlib.error:
        raise HwpExtractionError("invalid_compression") from None


def _paragraph_text(data: bytes) -> str:
    if len(data) % 2:
        raise HwpExtractionError("invalid_text_encoding")
    pieces: list[str] = []
    start = offset = 0
    while offset < len(data):
        code = struct.unpack_from("<H", data, offset)[0]
        if code >= 32:
            offset += 2
            continue
        try:
            pieces.append(data[start:offset].decode("utf-16le"))
        except UnicodeDecodeError:
            raise HwpExtractionError("invalid_text_encoding") from None
        width = 16 if code in _LONG_CONTROLS else 2
        if offset + width > len(data):
            raise HwpExtractionError("invalid_control")
        if width == 16 and struct.unpack_from("<H", data, offset + 14)[0] != code:
            raise HwpExtractionError("invalid_control")
        if code == 9:
            pieces.append("\t")
        elif code in (10, 13):
            pieces.append("\n")
        elif code == 24:
            pieces.append("-")
        elif code in (30, 31) or width == 16:
            pieces.append(" ")
        offset += width
        start = offset
    try:
        pieces.append(data[start:].decode("utf-16le"))
    except UnicodeDecodeError:
        raise HwpExtractionError("invalid_text_encoding") from None
    return "".join(pieces).strip()


def _header_properties(header: bytes) -> int:
    if len(header) != 256 or header[:32].rstrip(b"\0") != b"HWP Document File":
        raise HwpExtractionError("invalid_hwp_header")
    if header[35] != 5 or header[34] not in (0, 1):
        raise HwpExtractionError("unsupported_version")
    flags = struct.unpack_from("<I", header, 36)[0]
    if flags & ((1 << 1) | (1 << 4) | (1 << 8) | (1 << 10) | (1 << 13)):
        raise HwpExtractionError("protected_document")
    if flags & (1 << 2):
        raise HwpExtractionError("distribution_document")
    if flags & (1 << 14):
        raise HwpExtractionError("tracked_changes_document")
    return flags


def _extract(ole: olefile.OleFileIO, max_expanded_bytes: int) -> tuple[str, int, tuple[str, ...]]:
    if not ole.exists("FileHeader") or not ole.exists("DocInfo"):
        raise HwpExtractionError("invalid_hwp_header")
    if ole.get_size("FileHeader") != 256:
        raise HwpExtractionError("invalid_hwp_header")
    flags = _header_properties(ole.openstream("FileHeader").read())
    compressed = bool(flags & 1)
    remaining = max_expanded_bytes

    def read_stream(path: str | list[str]) -> bytes:
        nonlocal remaining
        # olefile opens an entire stream in memory. Inspect its advertised size
        # first, and separately bound expansion across DocInfo and all sections.
        if ole.get_size(path) > MAX_ATTACHMENT_BYTES:
            raise HwpExtractionError("input_size_limit")
        data = _expand(ole.openstream(path).read(), compressed, remaining)
        remaining -= len(data)
        return data

    doc_info = read_stream("DocInfo")
    counts = [
        struct.unpack_from("<H", payload)[0]
        for tag, payload in _records(doc_info)
        if tag == 16 and len(payload) >= 2
    ]
    if len(counts) != 1 or counts[0] < 1:
        raise HwpExtractionError("invalid_section_count")
    sections: dict[int, list[str]] = {}
    for path in ole.listdir():
        if len(path) == 2 and path[0] == "BodyText":
            match = re.fullmatch(r"Section(0|[1-9][0-9]*)", path[1])
            if not match:
                raise HwpExtractionError("invalid_section_count")
            sections[int(match[1])] = path
    if sorted(sections) != list(range(counts[0])):
        raise HwpExtractionError("missing_body_section")

    paragraphs: list[str] = []
    warnings: set[str] = set()
    for index in sorted(sections):
        for tag, payload in _records(read_stream(sections[index])):
            if tag == 67:
                text = _paragraph_text(payload)
                if text:
                    paragraphs.append(text)
            elif tag == 77:
                warnings.add("table_layout_not_preserved")
            elif tag in _NON_TEXT_TAGS:
                warnings.add("nontext_content_not_extracted")
    text = "\n".join(paragraphs)
    if not text.strip():
        raise HwpExtractionError("no_text")
    if any("\ue000" <= char <= "\uf8ff" for char in text):
        warnings.add("private_use_characters")
    return text, len(sections), tuple(sorted(warnings))


def extract_hwp_text(
    file: DownloadedAttachment, *, max_expanded_bytes: int = MAX_EXPANDED_HWP_BYTES
) -> HwpTextResult:
    """Return the existing summarizer's AttachmentText plus extraction warnings.

    Only ordinary HWP 5.0/5.1 documents are supported. Preview text is never used
    as a fallback. Tables lose layout; images/equations need separate processing.
    No network, filesystem, database or Gemini call occurs.
    """
    if file.media_type != "application/x-hwp":
        raise HwpExtractionError("unsupported_type")
    if (
        isinstance(max_expanded_bytes, bool)
        or not isinstance(max_expanded_bytes, int)
        or max_expanded_bytes < 1
    ):
        raise ValueError("max_expanded_bytes must be a positive integer")
    if not file.name.strip():
        raise HwpExtractionError("invalid_name")
    if not isinstance(file.data, bytes) or not file.data:
        raise HwpExtractionError("empty_file")
    if len(file.data) > MAX_ATTACHMENT_BYTES:
        raise HwpExtractionError("input_size_limit")
    if not file.data.startswith(olefile.MAGIC):
        raise HwpExtractionError("unsupported_format")
    try:
        with olefile.OleFileIO(BytesIO(file.data), raise_defects=olefile.DEFECT_INCORRECT) as ole:
            text, count, warnings = _extract(ole, max_expanded_bytes)
    except HwpExtractionError:
        raise
    except (OSError, ValueError, EOFError, struct.error, IndexError):
        raise HwpExtractionError("invalid_container") from None
    return HwpTextResult(AttachmentText(name=file.name, text=text), count, warnings)
