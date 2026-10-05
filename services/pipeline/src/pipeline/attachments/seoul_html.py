"""Extract file references only; never download files or invent UUIDs."""

from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from pipeline.models import FileRecord, NoticeRecord

FILE_EXTENSIONS = {
    ".pdf",
    ".hwp",
    ".hwpx",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
}

# Exact paths observed in API HTML; never exclude the whole image directory.
CULTURE_DECORATIVE_IMAGE_PATHS = frozenset(
    {
        "/_ui/images/main/cnl-common/nLc-logo-culture.png",
        "/_ui/images/main/cnl-common/nLc-top-facebook.png",
        "/_ui/images/main/cnl-common/nLc-top-instargram.png",
        "/_ui/images/main/cnl-common/nLc-top-blog.png",
    }
)


class SeoulAttachmentError(ValueError):
    pass


def normalize_file_url(base: str, reference: str) -> str:
    try:
        parsed = urlsplit(urljoin(base, reference.strip()))
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 80, 443)
        ):
            raise ValueError
        if parsed.hostname == "news.seoul.go.kr":
            return urlunsplit(("https", "news.seoul.go.kr", parsed.path, parsed.query, ""))
        # Do not assume external hosts support HTTPS.
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
    except ValueError:
        raise SeoulAttachmentError("서울시 파일 URL이 올바르지 않습니다.") from None


def extract_files(notice: NoticeRecord) -> tuple[FileRecord, ...]:
    if notice.category != "seoul":
        raise SeoulAttachmentError("서울시 공지만 처리할 수 있습니다.")
    soup = BeautifulSoup(notice.body_html or "", "html.parser")
    files: dict[tuple[str, str], FileRecord] = {}
    for element in soup.select("a[href], img[src]"):
        is_image = element.name == "img"
        reference = element.get("src" if is_image else "href", "").strip()
        if not reference or reference.startswith("#"):
            continue
        if not is_image:
            try:
                suffix = PurePosixPath(unquote(urlsplit(reference).path)).suffix.lower()
            except ValueError:
                raise SeoulAttachmentError("서울시 링크 URL을 해석하지 못했습니다.") from None
            if suffix not in FILE_EXTENSIONS:
                if element.has_attr("download"):
                    raise SeoulAttachmentError("확장자가 없는 다운로드 링크는 확인이 필요합니다.")
                continue  # Forms, ordinary pages and contact links are not files.
        url = normalize_file_url(notice.url, reference)
        parsed_url = urlsplit(url)
        if (
            is_image
            and parsed_url.hostname == "news.seoul.go.kr"
            and (
                parsed_url.path.startswith("/wp-content/themes/")
                or "/wp-content/themes/" in parsed_url.path
            )
        ):
            continue  # Observed tag icons/theme furniture are not article images.
        if (
            is_image
            and parsed_url.hostname == "culture.seoul.go.kr"
            and parsed_url.path in CULTURE_DECORATIVE_IMAGE_PATHS
        ):
            continue  # Known logo/SNS furniture only; preserve unknown banners/posters.
        filename = PurePosixPath(unquote(urlsplit(url).path)).name or None
        item = FileRecord(
            category="seoul",
            source_board=notice.source_board,
            post_sn=notice.post_sn,
            kind="inline_image" if is_image else "attachment",
            file_sn=None,
            file_id=None,
            file_name=filename,
            url=url,
        )
        files[(item.file_key, item.kind)] = item
    return tuple(files.values())
