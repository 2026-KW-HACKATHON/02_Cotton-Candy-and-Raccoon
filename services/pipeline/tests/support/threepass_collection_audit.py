"""Helpers shared from test_threepass_collection_audit.py."""

from pipeline.models import RawNotice

__all__ = [
    "EMPTY_ATTACHMENTS",
    "_nowon",
]


EMPTY_ATTACHMENTS = "<table><tr><th>첨부파일</th><td>첨부파일이 없습니다</td></tr></table>"


def _nowon(post_sn: str) -> RawNotice:
    return RawNotice(
        source_board="1001", category="nowon", dong_group=None, is_pinned=False,
        post_sn=post_sn, title="audit", department=None, registered_on="2026-10-07",
        url="https://www.nowon.kr/www/user/bbs/BD_selectBbs.do"
            f"?q_bbsCode=1001&q_bbscttSn={post_sn}",
        body_html="<p>Body</p>", license_type="KOGL-4",
    )
