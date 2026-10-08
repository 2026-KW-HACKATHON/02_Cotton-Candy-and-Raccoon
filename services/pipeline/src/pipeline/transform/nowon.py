"""Convert a Nowon API notice into a DB-ready notice record."""

import re
from datetime import date

from bs4 import BeautifulSoup

from pipeline.models import NoticeRecord, RawNotice
from pipeline.sources.nowon_page import NowonPageError, normalize_nowon_notice_url

DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


class TransformError(ValueError):
    """The source notice cannot be converted to the expected DB shape."""


def _body_or_none(html: str | None) -> str | None:
    if html is None:
        return None
    soup = BeautifulSoup(html, "html.parser")
    if soup.get_text(strip=True):
        return html
    for element in soup.select("img[src], a[href]"):
        attribute = "src" if element.name == "img" else "href"
        if element.get(attribute, "").strip():
            return html
    return None


def transform_nowon_notice(notice: RawNotice) -> NoticeRecord:
    """Validate source-specific fields and normalize a Nowon notice without I/O."""
    if (
        notice.category != "nowon" or notice.dong_group is not None
        or notice.is_pinned or notice.license_type != "KOGL-4"
    ):
        raise TransformError("노원구 공지의 분류 또는 공공누리 유형이 올바르지 않습니다.")
    if not DATE_PATTERN.fullmatch(notice.registered_on):
        raise TransformError("노원구 공지 등록일 형식이 올바르지 않습니다.")
    try:
        registered_on = date.fromisoformat(notice.registered_on)
    except ValueError:
        raise TransformError("노원구 공지 등록일이 유효하지 않습니다.") from None
    try:
        url = normalize_nowon_notice_url(notice)
    except NowonPageError:
        raise TransformError("노원구 공지 원문 URL이 유효하지 않습니다.") from None
    return NoticeRecord(
        source_board=notice.source_board,
        category=notice.category,
        dong_group=notice.dong_group,
        is_pinned=notice.is_pinned,
        post_sn=notice.post_sn,
        title=notice.title,
        department=notice.department,
        registered_on=registered_on,
        url=url,
        body_html=_body_or_none(notice.body_html),
        license_type=notice.license_type,
    )
