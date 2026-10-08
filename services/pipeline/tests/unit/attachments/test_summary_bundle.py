"""DB source read and preparation handoff, without live services."""

from base64 import b64decode
from dataclasses import replace
from datetime import date
from unittest.mock import MagicMock

import httpx
import pytest
from support.hwp_text import _hwp, _record, _text
from support.summary_bundle import NOW, PNG, URL, file, source

from pipeline.attachments.summary_bundle import prepare_summary_source
from pipeline.storage.summary_source import load_summary_source


def test_read_uses_one_parameterized_select_and_never_commits() -> None:
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (
        7,
        "행사",
        "nowon",
        "1001",
        "00123",
        None,
        date(2026, 10, 1),
        "https://www.nowon.kr",
        None,
        [
            {
                "id": 2,
                "kind": "attachment",
                "file_key": "id:uuid",
                "file_id": "uuid",
                "file_sn": "1",
                "file_name": "첨부.pdf",
                "url": URL + "uuid",
            }
        ],
        4,
    )
    loaded = load_summary_source(conn, 7)
    assert loaded.notice_id == 7
    assert loaded.files[0].file_name == "첨부.pdf"
    assert loaded.source_board == "1001"
    assert loaded.post_sn == "00123"
    assert loaded.content_revision == 4
    assert loaded.files[0].file_key == "id:uuid"
    assert cursor.execute.call_count == 1
    sql, params = cursor.execute.call_args.args
    assert params == (7,)
    assert "n.is_visible = true" in sql
    assert "f.notice_id = n.id" in sql
    conn.commit.assert_not_called()
    conn.rollback.assert_not_called()
    conn.close.assert_not_called()
    cursor.fetchone.return_value = None
    assert load_summary_source(conn, 7) is None


@pytest.mark.parametrize("number", [0, -1, True, "7"])
def test_invalid_db_id_does_not_query(number: object) -> None:
    conn = MagicMock()
    with pytest.raises(ValueError):
        load_summary_source(conn, number)
    conn.cursor.assert_not_called()


def test_pdf_hwp_and_body_image_form_existing_input_blocks() -> None:
    hwp = _hwp([_text("접수는 10월 1일")]).data
    data = {"1": b"%PDF-poster", "2": hwp, "3": PNG}
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=data[request.url.params["q_fileId"]])
        )
    ) as client:
        result = prepare_summary_source(
            source(
                file(1, "포스터.pdf"),
                file(2, "안내.hwp"),
                html=f'<p>행사 안내</p><img src="{URL}3">',
            ),
            reference_datetime=NOW,
            client=client,
        )
    assert result.complete
    assert result.notice.body_text == "행사 안내"
    assert result.notice.attachments[0].text == "접수는 10월 1일"
    parts = result.to_gemini_input()
    assert [part["type"] for part in parts] == ["text", "image", "document"]
    assert b64decode(parts[1]["data"]) == PNG
    assert b64decode(parts[2]["data"]) == b"%PDF-poster"


def test_same_image_in_body_and_attachment_is_included_once() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=PNG))
    ) as client:
        result = prepare_summary_source(
            source(
                file(1, "사진.png"),
                file(2, None, "inline_image"),
                html=f'<img src="{URL}1"><img src="{URL}1">',
            ),
            reference_datetime=NOW,
            client=client,
        )
    assert result.complete
    assert len(result.media) == 1


@pytest.mark.parametrize("name", ["목록.xlsx", None])
def test_unsupported_or_nameless_attachment_is_reported(name: str | None) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("invalid name must not make a request"))
    ) as client:
        result = prepare_summary_source(
            source(file(1, name)), reference_datetime=NOW, client=client
        )
    assert result.complete
    assert result.warnings[0].reason_code in {"unsupported_type", "invalid_name"}
    assert result.file_manifest.attachment_status == "unread"
    assert result.to_gemini_input()[0]["type"] == "text"


def test_failed_file_keeps_other_files_and_reports_partial_input() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["q_fileId"] == "1":
            raise httpx.ReadTimeout("private url", request=request)
        return httpx.Response(200, content=b"%PDF-poster")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = prepare_summary_source(
            source(file(1, "a.pdf"), file(2, "b.pdf")), reference_datetime=NOW, client=client
        )
    assert result.warnings[0].reason_code == "timeout"
    assert len(result.media) == 1
    assert "private url" not in repr(result)
    assert result.file_manifest.attachment_status == "partial"
    assert result.to_gemini_input()[1]["type"] == "document"


def test_hwp_warning_is_not_discarded() -> None:
    hwp = _hwp([_text("표 내용") + _record(77, bytes(22))]).data
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=hwp))
    ) as client:
        result = prepare_summary_source(
            source(file(1, "표.hwp")), reference_datetime=NOW, client=client
        )
    assert result.complete
    assert result.warnings[0].reason_code == "table_layout_not_preserved"


def test_broken_hwp_is_failure_not_blank_success() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=bytes.fromhex("D0 CF 11 E0 A1 B1 1A E1"))
        )
    ) as client:
        result = prepare_summary_source(
            source(file(1, "broken.hwp")), reference_datetime=NOW, client=client
        )
    assert result.complete
    assert result.file_manifest.omissions[0].reason_code == "extraction_failed"
    assert not result.notice.attachments


def test_base64_and_text_budget_blocks_oversized_input() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"%PDF-" + b"x" * 1100))
    ) as client:
        result = prepare_summary_source(
            source(file(1, "large.pdf")),
            reference_datetime=NOW,
            client=client,
            max_input_bytes=1400,
        )
    assert not result.complete
    assert result.failures[-1].reason_code == "total_size_limit"


def test_missing_body_and_files_is_no_content() -> None:
    result = prepare_summary_source(source(html=None), reference_datetime=NOW)
    assert not result.complete
    assert result.failures[0].reason_code == "no_content"


def test_invalid_reference_time_is_rejected_before_download() -> None:
    with pytest.raises(ValueError):
        prepare_summary_source(replace(source(), title=""), reference_datetime=NOW)


def test_duplicate_file_url_downloads_once() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=b"%PDF-source")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = prepare_summary_source(
            source(file(1, "one.pdf"), replace(file(1, "one.pdf"), id=2)),
            reference_datetime=NOW,
            client=client,
        )
    assert result.complete
    assert len(calls) == 1
    assert len(result.media) == 1


def test_external_body_image_is_omitted_without_network_request() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("external URL must not be fetched"))
    ) as client:
        result = prepare_summary_source(
            source(html='<p>本文</p><img src="https://example.org/private.png">'),
            reference_datetime=NOW,
            client=client,
        )
    assert result.complete
    assert result.warnings[0].stage == "body_image"
    assert result.warnings[0].reason_code == "invalid_url"
    assert result.file_manifest.omissions[0].url == source().url
    assert result.to_gemini_input()


@pytest.mark.parametrize("budget", [True, 0, -1, 1.5])
def test_invalid_input_budget_is_rejected(budget: object) -> None:
    with pytest.raises(ValueError):
        prepare_summary_source(source(), reference_datetime=NOW, max_input_bytes=budget)


def test_serialized_input_size_boundary() -> None:
    import json

    baseline = prepare_summary_source(source(), reference_datetime=NOW)
    size = len(json.dumps(baseline.to_gemini_input()).encode("utf-8"))
    assert prepare_summary_source(source(), reference_datetime=NOW, max_input_bytes=size).complete
    assert not prepare_summary_source(
        source(), reference_datetime=NOW, max_input_bytes=size - 1
    ).complete


def test_multiple_small_compressed_hwp_files_have_a_combined_text_limit() -> None:
    # Small compressed files can expand into a much larger combined text input.
    data = {
        key: _hwp([_text(character * 1800)]).data for key, character in [("1", "가"), ("2", "나")]
    }
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=data[request.url.params["q_fileId"]])
        )
    ) as client:
        result = prepare_summary_source(
            source(file(1, "a.hwp"), file(2, "b.hwp"), html=None),
            reference_datetime=NOW,
            client=client,
            max_input_bytes=9000,
        )
    assert len(result.notice.attachments) == 1
    assert not result.complete
    assert any(item.reason_code == "total_size_limit" for item in result.failures)
