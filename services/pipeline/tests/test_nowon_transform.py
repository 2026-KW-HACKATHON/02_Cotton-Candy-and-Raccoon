from dataclasses import replace
from datetime import date

import pytest

from pipeline.models import RawNotice
from pipeline.transform.nowon import TransformError, transform_nowon_notice

SOURCE_URL = (
    "http://www.nowon.kr:80/www/user/bbs/BD_selectBbs.do"
    "?q_bbsCode=1001&q_estnColumn1=11&q_bbscttSn=001234"
)
NORMALIZED_URL = SOURCE_URL.replace("http://www.nowon.kr:80", "https://www.nowon.kr")


@pytest.fixture
def raw_notice() -> RawNotice:
    return RawNotice(
        source_board="1001",
        category="nowon", dong_group=None, is_pinned=False, post_sn="001234",
        title="안내", department="교육지원과", registered_on="2026-09-25",
        url=SOURCE_URL, body_html="<p>본문 &amp; 안내</p>", license_type="KOGL-4",
    )


def test_valid_notice_is_normalized_without_losing_source_identity(raw_notice: RawNotice) -> None:
    record = transform_nowon_notice(raw_notice)
    assert record.category == "nowon"
    assert record.dong_group is None
    assert record.is_pinned is False
    assert record.license_type == "KOGL-4"
    assert record.post_sn == "001234"
    assert record.title == "안내"
    assert record.department == "교육지원과"
    assert record.registered_on == date(2026, 9, 25)
    assert record.url == NORMALIZED_URL
    assert record.body_html == "<p>본문 &amp; 안내</p>"
    assert raw_notice.url == SOURCE_URL
    assert raw_notice.registered_on == "2026-09-25"


@pytest.mark.parametrize("bad_date", [
    "2026-02-30", "2026-9-25", "20260925", "2026-09-25T12:00:00", "invalid",
])
def test_invalid_dates_are_rejected(raw_notice: RawNotice, bad_date: str) -> None:
    with pytest.raises(TransformError, match="등록일"):
        transform_nowon_notice(replace(raw_notice, registered_on=bad_date))


@pytest.mark.parametrize("bad_url", [
    SOURCE_URL.replace("www.nowon.kr", "other.example"),
    SOURCE_URL.replace("q_bbscttSn=001234", "q_bbscttSn=1234"),
    SOURCE_URL.replace("BD_selectBbs.do", "other.do"),
    SOURCE_URL.replace("http://", "http://user:secret@"),
    "http://[invalid",
])
def test_invalid_urls_are_rejected_without_echoing_them(
    raw_notice: RawNotice, bad_url: str,
) -> None:
    with pytest.raises(TransformError, match="URL") as caught:
        transform_nowon_notice(replace(raw_notice, url=bad_url))
    assert bad_url not in str(caught.value)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("changes", [
    {"category": "dong", "source_board": "1042"},
    {"dong_group": "wolgye1"}, {"is_pinned": True},
    {"license_type": "KOGL-2"}, {"license_type": None},
])
def test_wrong_nowon_shape_or_license_is_rejected(
    raw_notice: RawNotice, changes: dict[str, object],
) -> None:
    with pytest.raises(TransformError, match="분류 또는 공공누리"):
        transform_nowon_notice(replace(raw_notice, **changes))


@pytest.mark.parametrize("field", ["title", "post_sn"])
@pytest.mark.parametrize("blank", ["", " \t"])
def test_required_blank_values_are_rejected_at_raw_boundary(
    raw_notice: RawNotice, field: str, blank: str,
) -> None:
    with pytest.raises(ValueError, match=field):
        transform_nowon_notice(replace(raw_notice, **{field: blank}))


@pytest.mark.parametrize("html", [None, "<p>  </p>", "<p>&nbsp;<br></p>"])
def test_empty_body_becomes_none(raw_notice: RawNotice, html: str | None) -> None:
    record = transform_nowon_notice(replace(raw_notice, body_html=html))
    assert record.body_html is None


@pytest.mark.parametrize("html", [
    '<p><img src="/file?q_fileSn=1&amp;q_fileId=abc"></p>',
    '<p><a href="/file?q_fileSn=1&amp;q_fileId=abc"></a></p>',
])
def test_image_or_link_only_body_is_preserved(raw_notice: RawNotice, html: str) -> None:
    record = transform_nowon_notice(replace(raw_notice, body_html=html))
    assert record.body_html == html
