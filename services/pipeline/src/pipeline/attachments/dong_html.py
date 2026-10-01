"""Extract file metadata from a fully fetched dong notice page."""

from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from pipeline.attachments.nowon_html import (
    AttachmentError,
    _file_identity,
    _normalize_nowon_file_url,
    extract_page_files,
    merge_files,
)
from pipeline.models import FileRecord, RawNotice


def extract_dong_files(notice: RawNotice, page_html: str) -> list[FileRecord]:
    """Read body images/links and the separate attachment row; never download files.

    Both Nowon boards use the same official q_fileSn/q_fileId download endpoint.
    Missing or malformed attachment rows must fail before any DB write.
    """
    if notice.category != "dong":
        raise ValueError("동주민센터 공지만 처리할 수 있습니다.")
    body_files: list[FileRecord] = []
    if notice.body_html is not None:
        soup = BeautifulSoup(notice.body_html, "html.parser")
        for element in soup.select("a[href], img[src]"):
            is_attachment = element.name == "a"
            reference = element.get("href" if is_attachment else "src")
            if not isinstance(reference, str) or not reference.strip():
                continue
            try:
                url = _normalize_nowon_file_url(urljoin(notice.url, reference.strip()))
                parsed = urlsplit(url)
            except ValueError:
                raise AttachmentError(
                    "본문 파일 URL 형식이 올바르지 않습니다.", code="invalid_file_url",
                ) from None
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                continue
            identity = _file_identity(url)
            if identity is None:
                continue
            file_sn, file_id = identity
            body_files.append(FileRecord(
                category="dong", post_sn=notice.post_sn,
                kind="attachment" if is_attachment else "inline_image",
                file_sn=file_sn, file_id=file_id,
                file_name=(element.get_text(" ", strip=True) or None) if is_attachment else None,
                url=url,
            ))
    page_files = extract_page_files(notice, page_html, notice.url)
    return merge_files(body_files, page_files)
