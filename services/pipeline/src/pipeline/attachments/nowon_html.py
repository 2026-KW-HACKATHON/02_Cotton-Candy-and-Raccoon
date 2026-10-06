"""Read Nowon file references from API HTML and the original notice page."""

import re
from dataclasses import replace
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from uuid import UUID

from bs4 import BeautifulSoup

from pipeline.models import FileRecord, RawNotice
from pipeline.sources.nowon_page import normalize_nowon_notice_url


class AttachmentError(ValueError):
    """A file reference is ambiguous; do not silently drop or merge it."""

    def __init__(self, message: str, *, code: str = "attachment_error") -> None:
        super().__init__(message)
        self.code = code


def recover_masked_body_urls(notice: RawNotice, page_html: str) -> RawNotice:
    """Repair masked file UUIDs only from an unambiguous, verified original body.

    No network or UUID guessing. Unaffected API HTML is returned byte-for-byte;
    repaired HTML is serialized by BeautifulSoup and extracted again by callers.
    """
    body = BeautifulSoup(notice.body_html or "", "html.parser")
    masked = []
    for tag in body.select("img[src], a[href]"):
        attribute = "src" if tag.name == "img" else "href"
        reference = str(tag.get(attribute, ""))
        # Ordinary links containing stars are not file references.
        try:
            url = urljoin(notice.url, reference)
            parsed = urlsplit(url)
        except ValueError:
            raise AttachmentError(
                "파일 URL 형식이 올바르지 않습니다.", code="invalid_file_url",
            ) from None
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        identity = _file_identity(url)
        if identity is not None and "***" in identity[1]:
            masked.append((tag, attribute, reference, identity))
    if not masked:
        return notice

    base_url = normalize_nowon_notice_url(notice)
    page = BeautifulSoup(page_html, "html.parser")
    identifiers = [tag.get("value") for tag in page.select('input[name="q_bbscttSn"]')]
    original_bodies = page.select(".article-body")
    if identifiers != [notice.post_sn] or len(original_bodies) != 1:
        raise AttachmentError(
            "가려진 파일 주소를 복구할 원문 게시물을 확인하지 못했습니다.",
            code="masked_file_source_invalid",
        )

    def official_file_url(reference: str) -> str | None:
        try:
            url = _normalize_nowon_file_url(urljoin(base_url, reference))
            parsed = urlsplit(url)
            if (
                parsed.scheme == "https"
                and parsed.netloc == "www.nowon.kr"
                and parsed.path == "/component/file/ND_fileDownload.do"
                and not parsed.username
                and not parsed.password
            ):
                # Preserve every parameter; only ordering/encoding are canonicalized.
                query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
                return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))
        except ValueError:
            pass
        return None

    for tag, attribute, reference, (file_sn, masked_id) in masked:
        if official_file_url(reference) is None:
            raise AttachmentError(
                "가려진 파일 주소가 허용된 주소가 아닙니다.", code="masked_file_source_invalid"
            )
        pattern = re.escape(masked_id).replace(re.escape("***"), "[0-9a-fA-F-]+")
        candidates: set[str] = set()
        for original in original_bodies[0].select(f"{tag.name}[{attribute}]"):
            url = official_file_url(str(original.get(attribute, "")))
            if url is None:
                continue
            try:
                identity = _file_identity(url)
                if identity is None or identity[0] != file_sn:
                    continue
                original_id = identity[1]
                if str(UUID(original_id)) != original_id.lower():
                    continue
                if re.fullmatch(pattern, original_id):
                    candidates.add(url)
            except (ValueError, AttachmentError):
                continue
        if len(candidates) != 1:
            raise AttachmentError(
                "가려진 파일 주소에 대응하는 원문 주소가 없거나 여러 개입니다.",
                code="masked_file_unresolved" if not candidates else "masked_file_ambiguous",
            )
        tag[attribute] = next(iter(candidates))
    return replace(notice, body_html=body.decode_contents())


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
        raise AttachmentError(
            "파일 URL의 식별 정보가 불완전하거나 중복되었습니다.",
            code="invalid_file_identity",
        )
    return sn_values[0], id_values[0]


def _unique_files(records: list[FileRecord]) -> dict[tuple[str, ...], FileRecord]:
    files: dict[tuple[str, ...], FileRecord] = {}
    for record in records:
        key = (record.category, record.source_board, record.post_sn,
               record.file_key, record.kind)
        previous = files.get(key)
        if previous is not None and previous.url != record.url:
            raise AttachmentError(
                "같은 file_id와 kind에 서로 다른 URL이 있습니다.",
                code="file_reference_conflict",
            )
        files.setdefault(key, record)
    return files


def _normalize_nowon_file_url(url: str) -> str:
    """Use the HTTPS origin for file links on the known Nowon public host."""
    parsed = urlsplit(url)
    if (
        parsed.scheme in ("http", "https")
        and parsed.hostname == "www.nowon.kr"
        and parsed.port in (None, 80, 443)
        and parsed.username is None and parsed.password is None
    ):
        return urlunsplit(("https", "www.nowon.kr", parsed.path, parsed.query, ""))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _is_editor_image(url: str) -> bool:
    """Only accept observed official editor-upload paths without an ID."""
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https" and parsed.netloc == "www.nowon.kr"
        and parsed.path.startswith("/webcontent/crosseditor/images/")
        and parsed.path.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    )


def extract_files(notice: RawNotice) -> list[FileRecord]:
    """Return one file reference per source key, file_key and kind in the API body.

    Without identifiers, only official editor-upload images are recognized.
    Ordinary navigation links and decorative images are excluded. No I/O occurs.
    """
    if notice.category != "nowon":
        raise ValueError("노원구 공지만 처리할 수 있습니다.")
    if notice.body_html is None:
        return []

    files: list[FileRecord] = []
    base_url = normalize_nowon_notice_url(notice)
    soup = BeautifulSoup(notice.body_html, "html.parser")
    for element in soup.select("a[href], img[src]"):
        is_attachment = element.name == "a"
        reference = element.get("href" if is_attachment else "src")
        if not isinstance(reference, str) or not reference.strip():
            continue
        try:
            url = _normalize_nowon_file_url(urljoin(base_url, reference.strip()))
            parsed = urlsplit(url)
        except ValueError:
            raise AttachmentError(
                "파일 URL 형식이 올바르지 않습니다.", code="invalid_file_url",
            ) from None
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        identity = _file_identity(url)
        if identity is None and (is_attachment or not _is_editor_image(url)):
            continue
        file_sn, file_id = identity if identity is not None else (None, None)
        file_name = None
        if is_attachment:
            download_name = element.get("download")
            file_name = (
                download_name.strip() if isinstance(download_name, str) and download_name.strip()
                else element.get_text(" ", strip=True) or None
            )
        files.append(FileRecord(
            category=notice.category,
            source_board=notice.source_board,
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
        raise AttachmentError(
            "원문 페이지의 첨부파일 영역을 확인할 수 없습니다.",
            code="attachment_section_missing",
        )
    row = rows[0]
    links = row.select("ul.file-list a[href]")
    if not links:
        if "첨부파일이 없습니다" in row.get_text(" ", strip=True):
            return []
        raise AttachmentError(
            "원문 페이지의 첨부파일 목록을 확인할 수 없습니다.",
            code="attachment_list_missing",
        )

    files: list[FileRecord] = []
    for link in links:
        reference = link.get("href")
        if not isinstance(reference, str) or not reference.strip():
            raise AttachmentError(
                "원문 페이지 첨부파일의 URL이 비어 있습니다.",
                code="empty_file_url",
            )
        try:
            url = _normalize_nowon_file_url(urljoin(page_url, reference.strip()))
            parsed = urlsplit(url)
        except ValueError:
            raise AttachmentError(
                "원문 페이지 첨부파일 URL 형식이 올바르지 않습니다.",
                code="invalid_file_url",
            ) from None
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise AttachmentError(
                "원문 페이지 첨부파일 URL 형식이 올바르지 않습니다.",
                code="invalid_file_url",
            )
        identity = _file_identity(url)
        # A direct file path is valid without a UUID; an unknown endpoint is not.
        if identity is None and not parsed.path.lower().endswith(
            (".pdf", ".hwp", ".hwpx", ".png", ".jpg", ".jpeg", ".webp",
             ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip")
        ):
            raise AttachmentError(
                "원문 페이지 첨부파일의 식별 정보가 없습니다.",
                code="missing_file_identity",
            )
        file_sn, file_id = identity if identity is not None else (None, None)
        files.append(FileRecord(
            category=notice.category,
            source_board=notice.source_board,
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
