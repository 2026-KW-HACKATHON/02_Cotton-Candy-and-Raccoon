from dataclasses import replace
from datetime import date

import pytest

from pipeline.models import RawNotice
from pipeline.transform.dong import DongTransformError, transform_dong_notice


@pytest.fixture
def notice() -> RawNotice:
    return RawNotice(
        source_board="1042",
        category="dong", dong_group="wolgye1", is_pinned=False,
        post_sn="001234", title="공지", department="월계1동 행정민원팀",
        registered_on="2026-09-28",
        url=("https://www.nowon.kr/dong/user/bbs/BD_selectBbs.do"
             "?q_bbsCode=1042&q_bbscttSn=001234&q_deptCode=1047"),
        body_html='<img src="/component/file/ND_fileDownload.do?q_fileSn=1&q_fileId=2">',
        license_type="KOGL-1",
    )


def test_image_only_body_and_leading_zeros_survive(notice: RawNotice) -> None:
    record = transform_dong_notice(notice)
    assert record.category == "dong"
    assert record.dong_group == "wolgye1"
    assert record.post_sn == "001234"
    assert record.registered_on == date(2026, 9, 28)
    assert record.license_type == "KOGL-1"
    assert record.body_html == notice.body_html


def test_empty_body_becomes_none(notice: RawNotice) -> None:
    assert transform_dong_notice(replace(notice, body_html="<p> </p>")).body_html is None


def test_other_dong_pinned_notice_is_allowed(notice: RawNotice) -> None:
    source = replace(notice, dong_group="other", is_pinned=True)
    record = transform_dong_notice(source)
    assert (record.dong_group, record.is_pinned) == ("other", True)


@pytest.mark.parametrize("changes", [
    {"registered_on": "2026-02-30"},
    {"registered_on": "2026/09/28"},
    {"dong_group": "other"},
    {"url": "https://example.com/dong/user/bbs/BD_selectBbs.do"},
    {"url": "https://www.nowon.kr/dong/user/bbs/BD_selectBbs.do"
            "?q_bbsCode=1042&q_bbscttSn=999&q_deptCode=1047"},
])
def test_invalid_db_shape_or_source_url_is_rejected(
    notice: RawNotice, changes: dict[str, object],
) -> None:
    with pytest.raises(DongTransformError):
        transform_dong_notice(replace(notice, **changes))
