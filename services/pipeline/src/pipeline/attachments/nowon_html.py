"""Read Nowon file references from API HTML and the original notice page."""

from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from pipeline.models import FileRecord, RawNotice


class AttachmentError(ValueError):
    """A file reference is ambiguous; do not silently drop or merge it."""


def _file_identity(url: str) -> tuple[str, str] | None:
    query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    sn_values = query.get("q_fileSn")
    id_values = query.get("q_fileId")
    if sn_values is None and id_values is None:
        return None
    if (
        sn_values is None or id_values is None
        or len(sn_values) != 1 or len(id_values) != 1
        or not sn_values[0].strip() or not id_values[0].strip()
    ):
        raise AttachmentError("파일 URL의 식별 정보가 불완전하거나 중복되었습니다.")
    return sn_values[0], id_values[0]


def _unique_files(records: list[FileRecord]) -> dict[tuple[str, str], FileRecord]:
    files: dict[tuple[str, str], FileRecord] = {}
    for record in records:
        key = (record.file_id, record.kind)
        previous = files.get(key)
        if previous is not None and previous.url != record.url:
            raise AttachmentError("같은 file_id와 kind에 서로 다른 URL이 있습니다.")
        files.setdefault(key, record)
    return files


def extract_files(notice: RawNotice) -> list[FileRecord]:
    """Return one file reference per file_id and kind in the API body.

    URLs without both file identifiers are not file records. No network or DB
    access occurs.
    """
    if notice.category != "nowon":
        raise ValueError("노원구 공지만 처리할 수 있습니다.")
    if notice.body_html is None:
        return []

    files: list[FileRecord] = []
    soup = BeautifulSoup(notice.body_html, "html.parser")
    for element in soup.select("a[href], img[src]"):
        is_attachment = element.name == "a"
        reference = element.get("href" if is_attachment else "src")
        if not isinstance(reference, str) or not reference.strip():
            continue
        try:
            url = urljoin(notice.url, reference.strip())
            parsed = urlsplit(url)
        except ValueError:
            raise AttachmentError("파일 URL 형식이 올바르지 않습니다.") from None
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        identity = _file_identity(url)
        if identity is None:
            continue
        file_sn, file_id = identity
        file_name = None
        if is_attachment:
            download_name = element.get("download")
            file_name = (
                download_name.strip() if isinstance(download_name, str) and download_name.strip()
                else element.get_text(" ", strip=True) or None
            )
        files.append(FileRecord(
            category=notice.category,
            post_sn=notice.post_sn,
            kind="attachment" if is_attachment else "inline_image",
            file_sn=file_sn,
            file_id=file_id,
            file_name=file_name,
            url=url,
        ))
    return list(_unique_files(files).values())


def extract_page_files(notice: RawNotice, page_html: str, page_url: str) -> list[FileRecord]:
    """Read the board's attachment row, failing if the page shape is incomplete."""
    soup = BeautifulSoup(page_html, "html.parser")
    rows = [
        row for row in soup.select("tr")
        if (heading := row.find("th")) and "첨부파일" in heading.get_text(" ", strip=True)
    ]
    if len(rows) != 1:
        raise AttachmentError("원문 페이지의 첨부파일 영역을 확인할 수 없습니다.")
    row = rows[0]
    links = row.select("ul.file-list a[href]")
    if not links:
        if "첨부파일이 없습니다" in row.get_text(" ", strip=True):
            return []
        raise AttachmentError("원문 페이지의 첨부파일 목록을 확인할 수 없습니다.")

    files: list[FileRecord] = []
    for link in links:
        reference = link.get("href")
        if not isinstance(reference, str) or not reference.strip():
            raise AttachmentError("원문 페이지 첨부파일의 URL이 비어 있습니다.")
        try:
            url = urljoin(page_url, reference.strip())
            parsed = urlsplit(url)
        except ValueError:
            raise AttachmentError("원문 페이지 첨부파일 URL 형식이 올바르지 않습니다.") from None
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise AttachmentError("원문 페이지 첨부파일 URL 형식이 올바르지 않습니다.")
        identity = _file_identity(url)
        if identity is None:
            raise AttachmentError("원문 페이지 첨부파일의 식별 정보가 없습니다.")
        file_sn, file_id = identity
        files.append(FileRecord(
            category=notice.category,
            post_sn=notice.post_sn,
            kind="attachment",
            file_sn=file_sn,
            file_id=file_id,
            file_name=link.get_text(" ", strip=True) or None,
            url=url,
        ))
    return list(_unique_files(files).values())


def merge_files(body_files: list[FileRecord], page_files: list[FileRecord]) -> list[FileRecord]:
    """Keep each file role once, favoring the page's attachment metadata."""
    merged = _unique_files(body_files)
    merged.update(_unique_files(page_files))
    return list(merged.values())
