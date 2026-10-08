"""Exercise #13 with #18's source/file identity and real extraction shapes."""

from base64 import b64decode
from dataclasses import replace
from hashlib import sha256
from unittest.mock import MagicMock

import httpx
import pytest
from test_hwp_text import _hwp, _text
from test_summary_bundle import NOW, PNG, file, source

from pipeline.attachments.download import (
    AttachmentDownloadError,
    download_attachment,
    download_image,
)
from pipeline.attachments.seoul_html import extract_files
from pipeline.attachments.summary_bundle import prepare_summary_source
from pipeline.models import NoticeRecord
from pipeline.storage.summary_source import StoredFile, load_summary_source

SEOUL_URL = "https://news.seoul.go.kr/welfare/archives/00123"
PDF_URL = "https://news.seoul.go.kr/welfare/files/notice.pdf"
HWP_URL = "https://news.seoul.go.kr/welfare/files/notice.hwp"
IMAGE_URL = "https://news.seoul.go.kr/wp-content/uploads/2026/10/poster.png"


def _url_file(number: int, url: str, kind: str = "attachment") -> StoredFile:
    return StoredFile(
        number,
        kind,
        "url:" + sha256(url.encode()).hexdigest(),
        None,
        None,
        None,
        url,
    )


@pytest.mark.parametrize("board", ["21", "22", "23", "24", "25", "26", "27", "30"])
def test_db_reader_preserves_seoul_board_post_and_nullable_identifiers(board: str) -> None:
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (
        7,
        "서울 행사",
        "seoul",
        board,
        "00123",
        None,
        source().registered_on,
        SEOUL_URL,
        "<p>본문</p>",
        [
            {
                "id": 2,
                "kind": "attachment",
                "file_key": _url_file(2, PDF_URL).file_key,
                "file_id": None,
                "file_sn": None,
                "file_name": None,
                "url": PDF_URL,
            }
        ],
        4,
    )
    loaded = load_summary_source(conn, 7)
    assert (loaded.category, loaded.source_board, loaded.post_sn) == ("seoul", board, "00123")
    assert loaded.files[0] == _url_file(2, PDF_URL)
    assert loaded.content_revision == 4
    sql, params = cursor.execute.call_args.args
    assert params == (7,)
    assert "f.notice_id = n.id" in sql
    assert "order by f.file_key, f.kind, f.id" in sql
    assert "n.is_visible = true" in sql
    conn.commit.assert_not_called()
    conn.close.assert_not_called()


def test_seoul_html_extraction_to_input_retains_text_hwp_pdf_image_once() -> None:
    html = (
        '<p>주민 행사 안내</p><a href="files/notice.pdf">포스터</a>'
        '<a href="files/notice.hwp">안내</a>'
        '<img src="/wp-content/uploads/2026/10/poster.png">'
        '<img src="/wp-content/themes/city/icon.png">'
        '<img src="https://culture.seoul.go.kr/_ui/images/main/cnl-common/nLc-top-blog.png">'
    )
    notice = NoticeRecord(
        category="seoul",
        source_board="25",
        post_sn="00123",
        dong_group=None,
        is_pinned=False,
        title="행사",
        department=None,
        registered_on=source().registered_on,
        url="https://news.seoul.go.kr/welfare/",
        body_html=html,
        license_type=None,
    )
    extracted = extract_files(notice)
    assert len(extracted) == 3
    stored_files = tuple(
        StoredFile(i, f.kind, f.file_key, f.file_id, f.file_sn, f.file_name, f.url)
        for i, f in enumerate(extracted, 1)
    )
    original = replace(
        source(),
        category="seoul",
        source_board="25",
        url=notice.url,
        body_html=html,
        files=stored_files,
    )
    data = {PDF_URL: b"%PDF-poster", HWP_URL: _hwp([_text("신청은 주민센터")]).data, IMAGE_URL: PNG}
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=data[str(request.url)])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        prepared = prepare_summary_source(original, reference_datetime=NOW, client=client)
    assert prepared.complete
    assert prepared.notice.body_text.startswith("주민 행사 안내")
    assert prepared.notice.attachments[0].text == "신청은 주민센터"
    blocks = prepared.to_gemini_input()
    assert [b["type"] for b in blocks] == ["text", "image", "document"]
    assert [b64decode(b["data"]) for b in blocks[1:]] == [PNG, b"%PDF-poster"]
    assert set(calls) == set(data)
    assert len(calls) == 3
    assert original.body_html == html  # No rewritten original or DB write.


def test_nameless_url_pdf_uses_real_path_name_not_invented_id() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"%PDF-source"))
    ) as client:
        prepared = prepare_summary_source(
            replace(
                source(),
                category="seoul",
                source_board="25",
                url=SEOUL_URL,
                files=(_url_file(1, PDF_URL),),
            ),
            reference_datetime=NOW,
            client=client,
        )
    assert prepared.complete
    assert prepared.media[0].name == "notice.pdf"
    assert prepared.media[0].data == b"%PDF-source"


def test_body_and_db_image_failure_is_requested_once_and_retained_as_omissions() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(429)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        prepared = prepare_summary_source(
            replace(
                source(),
                category="seoul",
                source_board="25",
                url=SEOUL_URL,
                body_html=f'<p>본문</p><img src="{IMAGE_URL}">',
                files=(_url_file(1, IMAGE_URL, "inline_image"),),
            ),
            reference_datetime=NOW,
            client=client,
        )
    assert calls == [IMAGE_URL]
    assert [f.reason_code for f in prepared.warnings] == ["rate_limited", "rate_limited"]
    assert len(prepared.file_manifest.omissions) == 2
    assert prepared.to_gemini_input()


def test_nowon_body_and_db_image_query_order_download_once() -> None:
    item = file(1, None, "inline_image")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=PNG)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        prepared = prepare_summary_source(
            source(item, html=f'<img src="{item.url}"><img src="{item.url}">'),
            reference_datetime=NOW,
            client=client,
        )
    assert prepared.complete and len(prepared.media) == 1 and len(calls) == 1


@pytest.mark.parametrize("host", ["news.seoul.go.kr", "culture.seoul.go.kr"])
def test_official_seoul_direct_pdf_and_image_download(host: str) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, content=PNG if r.url.path.endswith(".png") else b"%PDF-source"
            )
        )
    ) as client:
        assert (
            download_attachment(
                f"https://{host}/files/notice.pdf", "notice.pdf", client=client
            ).data
            == b"%PDF-source"
        )
        assert download_image(f"https://{host}/files/poster.png", client=client).data == PNG


@pytest.mark.parametrize(
    "url",
    [
        "http://news.seoul.go.kr/files/notice.pdf",
        "https://news.seoul.go.kr.evil.example/files/notice.pdf",
        "https://news.seoul.go.kr@127.0.0.1/files/notice.pdf",
        "https://127.0.0.1/notice.pdf",
        "https://news.seoul.go.kr:8443/files/notice.pdf",
        "https://user:pass@news.seoul.go.kr/files/notice.pdf",
        "https://news.seoul.go.kr/files/notice.pdf#fragment",
        "https://news.seoul.go.kr/download?id=123",
    ],
)
def test_unapproved_or_ambiguous_seoul_download_never_requests(url: str) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("unsafe URL must not be requested"))
    ) as client:
        with pytest.raises(AttachmentDownloadError, match="invalid_url"):
            download_attachment(url, "notice.pdf", client=client)


def test_seoul_redirect_and_html_error_never_become_pdf_input() -> None:
    for response in [
        httpx.Response(302, headers={"Location": "https://evil.example/file.pdf"}),
        httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<html>error</html>"),
    ]:
        with httpx.Client(
            transport=httpx.MockTransport(lambda _, result=response: result), follow_redirects=True
        ) as client:
            with pytest.raises(AttachmentDownloadError):
                download_attachment(PDF_URL, "notice.pdf", client=client)
