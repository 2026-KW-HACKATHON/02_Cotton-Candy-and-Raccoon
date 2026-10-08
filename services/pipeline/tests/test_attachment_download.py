"""The downloader is exercised only through in-memory HTTP responses."""

import httpx
import pytest

from pipeline.attachments.download import (
    AttachmentDownloadError,
    download_attachment,
)

FILE_URL = (
    "https://www.nowon.kr/component/file/ND_fileDownload.do?"
    "q_fileSn=305586&q_fileId=fb7ebb0e-816f-46df-b75b-b0a3c459d119"
)
PDF = b"%PDF-1.3\nexample"
HWP = bytes.fromhex("D0 CF 11 E0 A1 B1 1A E1") + b"example"


def _client(response: httpx.Response) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda _request: response))


@pytest.mark.parametrize(
    ("name", "body", "content_type", "expected_type"),
    [
        ("notice.pdf", PDF, "application/pdf", "application/pdf"),
        ("notice.hwp", HWP, "application/octet-stream", "application/x-hwp"),
    ],
)
def test_valid_file_returns_original_bytes_and_media_type(
    name: str, body: bytes, content_type: str, expected_type: str
) -> None:
    response = httpx.Response(200, content=body, headers={"Content-Type": content_type})
    with _client(response) as client:
        result = download_attachment(FILE_URL, name, client=client)

    assert result.name == name
    assert result.media_type == expected_type
    assert result.data == body
    assert "example" not in repr(result)


@pytest.mark.parametrize(
    "url",
    [
        FILE_URL.replace("https://", "http://"),
        FILE_URL.replace("www.nowon.kr", "evil.example"),
        FILE_URL.replace("www.nowon.kr", "www.nowon.kr.evil.example"),
        FILE_URL.replace("www.nowon.kr", "user@www.nowon.kr"),
        FILE_URL.replace("www.nowon.kr", "www.nowon.kr:8443"),
        FILE_URL.replace("q_fileSn=305586&", ""),
        FILE_URL + "&q_fileId=second",
        FILE_URL + "#fragment",
        "https://www.nowon.kr/private/file.pdf",
    ],
)
def test_unsafe_or_malformed_url_is_rejected_before_request(url: str) -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=PDF)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AttachmentDownloadError) as error:
            download_attachment(url, "notice.pdf", client=client)

    assert error.value.reason_code == "invalid_url"
    assert not called
    assert url not in str(error.value)


@pytest.mark.parametrize("name", ["", " ", "notice.exe", "notice.hwpx"])
def test_missing_or_unsupported_name_is_rejected_before_request(name: str) -> None:
    with _client(httpx.Response(200, content=PDF)) as client:
        with pytest.raises(AttachmentDownloadError) as error:
            download_attachment(FILE_URL, name, client=client)

    assert error.value.reason_code in {"invalid_name", "unsupported_type"}


def test_redirect_is_not_followed_even_when_client_follows_redirects() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://evil.example/file.pdf"})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(AttachmentDownloadError) as error:
            download_attachment(FILE_URL, "notice.pdf", client=client)

    assert error.value.reason_code == "redirect"
    assert seen == [FILE_URL]


@pytest.mark.parametrize(
    ("status", "reason"),
    [(404, "http_error"), (429, "rate_limited"), (503, "http_error")],
)
def test_http_errors_are_classified(status: int, reason: str) -> None:
    with _client(httpx.Response(status)) as client:
        with pytest.raises(AttachmentDownloadError) as error:
            download_attachment(FILE_URL, "notice.pdf", client=client)

    assert error.value.reason_code == reason
    assert error.value.status_code == status


def test_declared_size_limit_is_checked() -> None:
    response = httpx.Response(200, content=PDF, headers={"Content-Length": "999"})
    with _client(response) as client:
        with pytest.raises(AttachmentDownloadError) as error:
            download_attachment(FILE_URL, "notice.pdf", client=client, max_bytes=20)

    assert error.value.reason_code == "too_large"


def test_stream_size_limit_works_without_content_length() -> None:
    with _client(httpx.Response(200, content=PDF)) as client:
        with pytest.raises(AttachmentDownloadError) as error:
            download_attachment(FILE_URL, "notice.pdf", client=client, max_bytes=8)

    assert error.value.reason_code == "too_large"


@pytest.mark.parametrize(
    ("name", "body", "content_type", "reason"),
    [
        ("notice.pdf", b"<html>missing</html>", "text/html", "type_mismatch"),
        ("notice.pdf", HWP, "application/octet-stream", "type_mismatch"),
        ("notice.hwp", PDF, "application/octet-stream", "type_mismatch"),
        ("notice.hwp", HWP, "application/pdf", "type_mismatch"),
        ("notice.pdf", b"", "application/pdf", "empty_file"),
    ],
)
def test_response_must_match_declared_file_type(
    name: str, body: bytes, content_type: str, reason: str
) -> None:
    response = httpx.Response(200, content=body, headers={"Content-Type": content_type})
    with _client(response) as client:
        with pytest.raises(AttachmentDownloadError) as error:
            download_attachment(FILE_URL, name, client=client)

    assert error.value.reason_code == reason


@pytest.mark.parametrize(
    ("exception", "reason"),
    [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "request_failed")],
)
def test_network_failure_does_not_expose_url(
    exception: type[httpx.RequestError], reason: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exception("contains-secret-url", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AttachmentDownloadError) as error:
            download_attachment(FILE_URL, "notice.pdf", client=client)

    assert error.value.reason_code == reason
    assert "contains-secret-url" not in str(error.value)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_size_limit_is_rejected(limit: object) -> None:
    with _client(httpx.Response(200, content=PDF)) as client:
        with pytest.raises(ValueError, match="max_bytes"):
            download_attachment(FILE_URL, "notice.pdf", client=client, max_bytes=limit)
