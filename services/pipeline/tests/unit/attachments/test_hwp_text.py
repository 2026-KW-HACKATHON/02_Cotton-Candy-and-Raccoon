"""Exercise real OLE containers made from synthetic public-free document text."""

import struct
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from support.hwp_text import _deflate, _hwp, _record, _text

from pipeline.attachments.download import DownloadedAttachment
from pipeline.attachments.hwp_text import HwpExtractionError, extract_hwp_text
from pipeline.transform.notice_input import AttachmentText, NoticeInput, render_notice_input


@pytest.mark.parametrize("flags", [0, 1])
@pytest.mark.parametrize("version", [0x05000000, 0x05010001])
def test_plain_and_compressed_hwp_returns_existing_attachment_contract(flags: int, version: int):
    file = _hwp(
        [_text("신청 안내\r") + _text("접수: 2026년 10월 1일\r")], flags=flags, version=version
    )
    result = extract_hwp_text(file)
    assert isinstance(result.attachment, AttachmentText)
    assert result.attachment.name == "안내.hwp"
    assert result.attachment.text == "신청 안내\n접수: 2026년 10월 1일"
    assert "신청 안내" not in repr(result)
    assert result.section_count == 1
    assert result.warnings == ()


def test_all_sections_are_in_numeric_order_and_preview_is_not_substituted():
    result = extract_hwp_text(
        _hwp(
            [_text(f"구역 {index}\r") for index in range(12)],
            overrides={"PrvText": "잘린 미리보기".encode("utf-16le")},
        )
    )
    assert result.section_count == 12
    assert result.attachment.text.splitlines() == [f"구역 {index}" for index in range(12)]
    assert "미리보기" not in result.attachment.text


def test_table_cells_and_image_warning_are_preserved_without_claiming_layout():
    body = _text("접수 안내\r") + _record(77, bytes(22), level=2)
    body += _text("대상\r", level=4) + _text("노원구 주민\r", level=4)
    body += _record(85, bytes(8), level=3)
    result = extract_hwp_text(_hwp([body]))
    assert result.attachment.text == "접수 안내\n대상\n노원구 주민"
    assert result.warnings == ("nontext_content_not_extracted", "table_layout_not_preserved")


@pytest.mark.parametrize("flags", [0, 1])
def test_extended_record_length_and_non_bmp_unicode_are_not_truncated(flags: int) -> None:
    value = "가나다" * 1000 + "😀"
    assert extract_hwp_text(_hwp([_text(value)], flags=flags)).attachment.text == value


def test_extracted_attachment_can_be_passed_to_existing_notice_input() -> None:
    result = extract_hwp_text(_hwp([_text("접수 기간: 2026년 10월 1일")]))
    notice = NoticeInput(
        title="신청 안내",
        body_text="첨부를 확인하세요.",
        reference_datetime=datetime(2026, 10, 1, tzinfo=UTC),
        attachments=[result.attachment],
    )
    rendered = render_notice_input(notice)
    assert "접수 기간: 2026년 10월 1일" in rendered
    assert "안내.hwp" in rendered


def test_control_payload_is_not_mistaken_for_document_text():
    tab = struct.pack("<H", 9) + "가짜글자xx".encode("utf-16le") + struct.pack("<H", 9)
    payload = "신청".encode("utf-16le") + tab + "접수\n문의\x18\x1e끝\r".encode("utf-16le")
    result = extract_hwp_text(_hwp([_record(67, payload)]))
    assert result.attachment.text == "신청\t접수\n문의- 끝"
    assert "가짜" not in result.attachment.text


@pytest.mark.parametrize("flags", [2, 16, 256, 1024, 8192])
def test_protected_documents_have_specific_failure_code(flags: int):
    with pytest.raises(HwpExtractionError, match="protected_document"):
        extract_hwp_text(_hwp([_text("비밀")], flags=flags))


@pytest.mark.parametrize(
    ("flags", "reason"), [(4, "distribution_document"), (16384, "tracked_changes_document")]
)
def test_distribution_and_tracked_changes_are_not_silently_parsed(flags: int, reason: str):
    with pytest.raises(HwpExtractionError, match=reason):
        extract_hwp_text(_hwp([_text("안내")], flags=flags))


@pytest.mark.parametrize("version", [0x03000000, 0x06000000, 0x05020000])
def test_unsupported_versions_are_reported(version: int):
    with pytest.raises(HwpExtractionError, match="unsupported_version"):
        extract_hwp_text(_hwp([_text("안내")], version=version))


@pytest.mark.parametrize(
    "overrides",
    [
        {"FileHeader": None},
        {"FileHeader": bytes(256)},
        {"FileHeader": bytes(40)},
        {"DocInfo": None},
    ],
)
def test_non_hwp_or_missing_header_streams_are_rejected(overrides):
    with pytest.raises(HwpExtractionError, match="invalid_hwp_header"):
        extract_hwp_text(_hwp([_text("안내")], overrides=overrides))


@pytest.mark.parametrize(
    "data", [b"", b"PK\x03\x04hwpx", b"not hwp", bytes.fromhex("D0 CF 11 E0 A1 B1 1A E1")]
)
def test_empty_wrong_format_and_broken_ole_containers_are_rejected(data: bytes):
    with pytest.raises(HwpExtractionError) as error:
        extract_hwp_text(DownloadedAttachment("sample.hwp", "application/x-hwp", data))
    assert error.value.reason_code in {"empty_file", "unsupported_format", "invalid_container"}
    assert "sample.hwp" not in str(error.value)


@pytest.mark.parametrize("body", [b"", _text(" \r")])
def test_no_text_does_not_return_a_blank_success(body: bytes):
    with pytest.raises(HwpExtractionError, match="no_text"):
        extract_hwp_text(_hwp([body]))


def test_missing_section_is_not_replaced_by_preview_text():
    with pytest.raises(HwpExtractionError, match="missing_body_section"):
        extract_hwp_text(
            _hwp(
                [_text("첫 구역")],
                declared_count=2,
                overrides={"PrvText": "미리보기".encode("utf-16le")},
            )
        )


@pytest.mark.parametrize(
    "body",
    [b"\x01", struct.pack("<I", 67 | (20 << 20)) + b"xx", struct.pack("<I", 67 | (0xFFF << 20))],
)
def test_truncated_record_headers_and_payloads_are_not_ignored(body: bytes):
    with pytest.raises(HwpExtractionError, match="invalid_record"):
        extract_hwp_text(_hwp([body]))


@pytest.mark.parametrize(
    "payload", [b"\xff", b"\x00\xd8", struct.pack("<H", 9), struct.pack("<H", 9) + bytes(14)]
)
def test_invalid_text_or_control_sequence_is_reported(payload: bytes):
    with pytest.raises(HwpExtractionError) as error:
        extract_hwp_text(_hwp([_record(67, payload)]))
    assert error.value.reason_code in {"invalid_text_encoding", "invalid_control"}


def test_crc_size_trailer_seen_in_real_hancom_sample_is_validated():
    assert extract_hwp_text(_hwp([_text("안내")], trailer=True)).attachment.text == "안내"
    bad = _deflate(_text("안내"), trailer=True)[:-1] + b"\xff"
    with pytest.raises(HwpExtractionError, match="invalid_compression"):
        extract_hwp_text(_hwp([_text("안내")], overrides={"BodyText/Section0": bad}))


@pytest.mark.parametrize(
    "compressed",
    [b"garbage", _deflate(_text("안내"))[:-1], _deflate(_text("안내")) + b"unexpected"],
)
def test_bad_or_truncated_compressed_stream_is_not_accepted(compressed: bytes):
    with pytest.raises(HwpExtractionError, match="invalid_compression"):
        extract_hwp_text(_hwp([_text("안내")], overrides={"BodyText/Section0": compressed}))


@pytest.mark.parametrize("flags", [0, 1])
def test_expanded_document_budget_applies_across_sections(flags: int):
    file = _hwp([_text("가" * 100), _text("나" * 100)], flags=flags)
    with pytest.raises(HwpExtractionError, match="expanded_size_limit"):
        extract_hwp_text(file, max_expanded_bytes=300)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_budget_is_rejected(limit):
    with pytest.raises(ValueError, match="max_expanded_bytes"):
        extract_hwp_text(_hwp([_text("안내")]), max_expanded_bytes=limit)


def test_wrong_media_type_and_blank_name_are_rejected():
    file = _hwp([_text("안내")])
    with pytest.raises(HwpExtractionError, match="unsupported_type"):
        extract_hwp_text(replace(file, media_type="application/pdf"))
    with pytest.raises(HwpExtractionError, match="invalid_name"):
        extract_hwp_text(replace(file, name=" "))


def test_legacy_private_use_characters_are_retained_but_flagged():
    result = extract_hwp_text(_hwp([_text("옛글자 \ue000")]))
    assert result.attachment.text == "옛글자 \ue000"
    assert result.warnings == ("private_use_characters",)
