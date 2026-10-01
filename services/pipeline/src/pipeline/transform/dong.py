"""Convert an HTML dong-board notice to the existing DB notice shape."""

import re
from datetime import date
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from pipeline.models import NoticeRecord, RawNotice

DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


class DongTransformError(ValueError):
    """The parsed dong notice does not satisfy the DB contract."""


def transform_dong_notice(notice: RawNotice) -> NoticeRecord:
    if notice.category != "dong" or notice.dong_group not in ("wolgye1", "other"):
        raise DongTransformError("동주민센터 공지의 출처 분류가 올바르지 않습니다.")
    if notice.dong_group == "other" and not notice.is_pinned:
        raise DongTransformError("다른 동의 일반 공지는 월계1동 수집 대상이 아닙니다.")
    if not DATE_PATTERN.fullmatch(notice.registered_on):
        raise DongTransformError("동주민센터 공지의 등록일 형식이 올바르지 않습니다.")
    try:
        registered_on = date.fromisoformat(notice.registered_on)
        parsed = urlsplit(notice.url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        valid_url = (
            parsed.scheme == "https" and parsed.netloc == "www.nowon.kr"
            and parsed.path == "/dong/user/bbs/BD_selectBbs.do"
            and query.get("q_bbsCode") == ["1042"]
            and query.get("q_bbscttSn") == [notice.post_sn]
            and query.get("q_deptCode") == ["1047"]
            and not parsed.fragment
        )
    except ValueError:
        raise DongTransformError("동주민센터 공지의 날짜 또는 URL이 유효하지 않습니다.") from None
    if not valid_url:
        raise DongTransformError("동주민센터 공지의 URL이 올바르지 않습니다.")

    body_html = notice.body_html
    if body_html is not None:
        soup = BeautifulSoup(body_html, "html.parser")
        if not soup.get_text(strip=True) and not soup.select_one("img[src], a[href]"):
            body_html = None
    return NoticeRecord(
        category="dong", dong_group=notice.dong_group, is_pinned=notice.is_pinned,
        post_sn=notice.post_sn, title=notice.title, department=notice.department,
        registered_on=registered_on, url=notice.url,
        body_html=body_html, license_type=notice.license_type,
    )
