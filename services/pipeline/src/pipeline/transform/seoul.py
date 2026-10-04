"""Convert a verified Seoul original article to the current notice DB contract."""

import re
from datetime import datetime

from pipeline.models import NoticeRecord, RawSeoulNotice
from pipeline.sources.seoul_page import SeoulPage, notice_url
from pipeline.transform.nowon import _body_or_none


class SeoulTransformError(ValueError):
    pass


def transform_seoul_notice(notice: RawSeoulNotice, page: SeoulPage) -> NoticeRecord:
    if (page.source_board, page.post_sn, page.url) != (
        notice.source_board,
        notice.post_sn,
        notice_url(notice),
    ):
        raise SeoulTransformError("서울시 공지와 원문 식별 정보가 다릅니다.")
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", notice.registered_on
    ):
        raise SeoulTransformError("서울시 등록일 형식이 올바르지 않습니다.")
    try:
        registered_on = datetime.strptime(notice.registered_on, "%Y-%m-%d %H:%M:%S").date()
    except ValueError:
        raise SeoulTransformError("서울시 등록일이 유효하지 않습니다.") from None
    return NoticeRecord(
        category="seoul",
        source_board=notice.source_board,
        dong_group=None,
        is_pinned=False,
        post_sn=notice.post_sn,
        title=notice.title.strip(),
        department=notice.department.strip() if notice.department else None,
        registered_on=registered_on,
        url=page.url,
        body_html=_body_or_none(page.body_html),
        license_type=page.license_type,
    )
