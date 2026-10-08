"""Verify HTML image preparation and the Gemini media handoff without an API key."""

from base64 import b64decode

import httpx
import pytest

from pipeline.attachments.download import (
    AttachmentDownloadError,
    DownloadedAttachment,
    download_image,
)
from pipeline.attachments.inline_images import prepare_notice_body
from pipeline.transform.media_input import to_gemini_media_part

NOTICE_URL = "http://www.nowon.kr:80/www/user/bbs/BD_selectBbs.do?q_bbscttSn=00123"
FILE_PATH = "/component/file/ND_fileDownload.do?q_fileSn=001&q_fileId=poster"
FILE_URL = "https://www.nowon.kr" + FILE_PATH
PNG = b"\x89PNG\r\n\x1a\nexample"
JPEG = b"\xff\xd8\xffexample"
WEBP = b"RIFF\x04\x00\x00\x00WEBPexample"


@pytest.mark.parametrize(
    ("data", "mime"), [(PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp")]
)
def test_download_detects_image_format_without_filename(data: bytes, mime: str) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, content=data, headers={"Content-Type": mime})
    )
    with httpx.Client(transport=transport) as client:
        image = download_image(FILE_URL, client=client)
    assert image.media_type == mime
    assert image.name.startswith("image.")
    assert image.data == data
    assert "example" not in repr(image)


@pytest.mark.parametrize("source", [FILE_PATH, FILE_URL.replace("https://", "http://").replace(
    "www.nowon.kr/", "www.nowon.kr:80/"
), "//www.nowon.kr:80" + FILE_PATH])
def test_body_text_and_https_image_are_prepared_together(source: str) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=PNG)

    html = f'<p>행사 안내 &amp; 접수</p><img src="{source.replace("&", "&amp;")}">'
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        body = prepare_notice_body(html, NOTICE_URL, client=client)
    assert body.body_text == "행사 안내 & 접수"
    assert body.complete
    assert len(body.images) == 1
    assert seen == ["https://www.nowon.kr/component/file/ND_fileDownload.do"
                    "?q_fileId=poster&q_fileSn=001"]
    # The original stored HTML remains unchanged, including its original URL.
    assert source.replace("&", "&amp;") in html


def test_duplicate_urls_and_query_order_are_requested_once() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=PNG)

    html = (
        f'<img src="{FILE_PATH}"><img src="{FILE_URL}">'
        '<img src="/component/file/ND_fileDownload.do?q_fileId=poster&amp;q_fileSn=001">'
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        body = prepare_notice_body(html, NOTICE_URL, client=client)
    assert len(seen) == 1
    assert len(body.images) == 1
    assert body.complete


def test_distinct_urls_returning_same_bytes_produce_one_image() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=PNG)

    html = f'<img src="{FILE_PATH}"><img src="{FILE_PATH.replace("poster", "other")}">'
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        body = prepare_notice_body(html, NOTICE_URL, client=client)
    assert len(seen) == 2
    assert len(body.images) == 1
    assert body.complete


def test_crosseditor_image_without_db_file_identifiers_is_downloaded() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=JPEG, headers={"Content-Type": "image/jpeg"})

    html = '<img src="/webcontent/crosseditor/images/poster.jpg">'
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        body = prepare_notice_body(html, NOTICE_URL, client=client)
    assert seen == ["https://www.nowon.kr/webcontent/crosseditor/images/poster.jpg"]
    assert body.complete
    assert body.images[0].data == JPEG


def test_remaining_body_budget_is_checked_during_image_download() -> None:
    html = f'<img src="{FILE_PATH}"><img src="{FILE_PATH.replace("poster", "other")}">'
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=PNG)
    )) as client:
        body = prepare_notice_body(html, NOTICE_URL, client=client, max_total_bytes=len(PNG) + 1)
    assert len(body.images) == 1
    assert not body.complete
    assert body.failures[0].reason_code == "total_size_limit"


@pytest.mark.parametrize("source", [
    "https://evil.example/poster.png", "http://127.0.0.1/poster.png",
    "data:image/png;base64,AAAA", "javascript:alert(1)",
    "https://www.nowon.kr.evil.example/poster.png", "", FILE_PATH + "&#10;secret",
])
def test_untrusted_sources_are_reported_without_network_request(source: str) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=PNG)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        body = prepare_notice_body(f'<img src="{source}">', NOTICE_URL, client=client)
    assert not seen
    assert not body.complete
    assert body.images == ()
    assert body.failures[0].reason_code == "invalid_url"
    assert source not in repr(body.failures[0]) or not source


def test_failed_image_does_not_stop_other_images_or_silently_complete() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("q_fileId") == "poster":
            return httpx.Response(404)
        return httpx.Response(200, content=PNG)

    html = f'<img src="{FILE_PATH}"><img src="{FILE_PATH.replace("poster", "other")}">'
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        body = prepare_notice_body(html, NOTICE_URL, client=client)
    assert len(body.images) == 1
    assert not body.complete
    assert [(error.image_index, error.reason_code) for error in body.failures] == [
        (1, "http_error")
    ]


def test_duplicate_failed_image_is_not_retried_and_each_reference_is_reported() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(429)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        body = prepare_notice_body(f'<img src="{FILE_PATH}">' * 2, NOTICE_URL, client=client)
    assert len(seen) == 1
    assert [error.reason_code for error in body.failures] == ["rate_limited", "rate_limited"]


@pytest.mark.parametrize(("data", "mime"), [
    (b"<html>error</html>", "text/html"), (PNG, "image/jpeg"),
    (b"GIF89aexample", "image/gif"), (b"not-an-image", "image/png"),
])
def test_bad_or_unsupported_image_data_is_rejected(data: bytes, mime: str) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, content=data, headers={"Content-Type": mime})
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(AttachmentDownloadError, match="type_mismatch"):
            download_image(FILE_URL, client=client)


def test_image_budget_is_enforced_before_next_request_when_exhausted() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=PNG)

    html = f'<img src="{FILE_PATH}"><img src="{FILE_PATH.replace("poster", "other")}">'
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        body = prepare_notice_body(html, NOTICE_URL, client=client, max_total_bytes=len(PNG))
    assert len(seen) == 1
    assert len(body.images) == 1
    assert body.failures[0].reason_code == "total_size_limit"


def test_single_image_size_limit_and_redirect_are_preserved() -> None:
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=PNG)
    )) as client:
        with pytest.raises(AttachmentDownloadError, match="too_large"):
            download_image(FILE_URL, client=client, max_bytes=8)
    with httpx.Client(follow_redirects=True, transport=httpx.MockTransport(
        lambda _: httpx.Response(302, headers={"Location": "https://evil.example/a.png"})
    )) as client:
        with pytest.raises(AttachmentDownloadError, match="redirect"):
            download_image(FILE_URL, client=client)


@pytest.mark.parametrize("html", [None, "", "<p>글만 있는 공지</p>"])
def test_text_only_notice_does_not_request_images(html: str | None) -> None:
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: pytest.fail("text-only notice should not request files")
    )) as client:
        body = prepare_notice_body(html, NOTICE_URL, client=client)
    assert body.images == ()
    assert body.complete


def test_image_only_poster_is_retained_when_body_text_is_empty() -> None:
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=PNG)
    )) as client:
        body = prepare_notice_body(f'<img src="{FILE_PATH}" />', NOTICE_URL, client=client)
    assert body.body_text == ""
    assert len(body.images) == 1
    part = to_gemini_media_part(body.images[0])
    assert part["type"] == "image"
    assert part["mime_type"] == "image/png"
    assert b64decode(part["data"]) == PNG


def test_pdf_becomes_document_and_hwp_requires_text_extraction() -> None:
    part = to_gemini_media_part(DownloadedAttachment("notice.pdf", "application/pdf", b"%PDF-1.3"))
    assert part["type"] == "document"
    assert b64decode(part["data"]) == b"%PDF-1.3"
    with pytest.raises(ValueError, match="HWP"):
        to_gemini_media_part(DownloadedAttachment("notice.hwp", "application/x-hwp", b"hwp"))


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_budget_is_rejected(limit: object) -> None:
    with pytest.raises(ValueError, match="limits"):
        prepare_notice_body(None, NOTICE_URL, max_total_bytes=limit)
