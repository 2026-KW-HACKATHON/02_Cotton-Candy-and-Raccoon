"""The plain body text stored for the app uses the summary input's conversion."""

import pytest

from pipeline.storage.notices import notice_body_text
from pipeline.transform.html_text import html_to_notice_text


@pytest.mark.parametrize("body_html", [None, "", "   ", "<p> </p>", "<img src='/a.png'>"])
def test_empty_body_is_stored_as_null(body_html: str | None) -> None:
    assert notice_body_text(body_html) is None


def test_body_text_matches_summary_input_text() -> None:
    body_html = (
        "<p>참가자 모집</p><p>신청 기간: 10월 1일 &amp; 2일</p>"
        "<table><tr><td>대상</td><td>주민</td></tr></table>"
    )
    assert notice_body_text(body_html) == html_to_notice_text(body_html)
    assert "&amp;" not in notice_body_text(body_html)
