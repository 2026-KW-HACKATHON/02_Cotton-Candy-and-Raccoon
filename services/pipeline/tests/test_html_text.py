"""Keep notice meaning intact while removing HTML presentation and file links."""

from pipeline.transform.html_text import html_to_notice_text


def test_inline_markup_entities_and_block_boundaries() -> None:
    html = (
        "<div>신청 <strong>기간</strong>: 10월&nbsp;1일</div><p>A &amp; B &amp;lt;안내&amp;gt;</p>"
    )

    assert html_to_notice_text(html) == "신청 기간: 10월 1일\nA & B &lt;안내&gt;"


def test_table_label_and_value_stay_on_one_line() -> None:
    html = (
        "<table><tr><th><p>신청기간</p></th>"
        "<td><p>2026.9.26<br>~2026.9.30</p></td></tr>"
        "<tr><th>대상</th><td>월계1동 주민</td></tr></table>"
    )

    assert html_to_notice_text(html) == ("신청기간: 2026.9.26 ~2026.9.30\n대상: 월계1동 주민")


def test_hidden_nested_content_and_file_links_are_excluded() -> None:
    html = (
        "<div hidden><div>숨김</div>누출되면 안 됨</div>"
        "<style>.x{display:none}</style>"
        "<p>본문<a href='/component/file/ND_fileDownload.do?"
        "q_fileSn=1&amp;q_fileId=abc'>첨부 제목</a>계속</p>"
        "<img alt='이미지 속 추측 텍스트' src='photo.png'>"
    )

    assert html_to_notice_text(html) == "본문 계속"


def test_hidden_void_tag_does_not_hide_following_text() -> None:
    assert html_to_notice_text("<img hidden alt='숨김'>본문") == "본문"


def test_javascript_action_link_keeps_its_label() -> None:
    assert html_to_notice_text("<p><a href='javascript:apply()'>온라인 신청</a>하세요.</p>") == (
        "온라인 신청하세요."
    )


def test_source_text_line_breaks_are_preserved() -> None:
    assert html_to_notice_text("변경 전: 2026.9.1\n변경 후: 2026.9.2") == (
        "변경 전: 2026.9.1\n변경 후: 2026.9.2"
    )


def test_preformatted_block_does_not_join_the_next_notice_field() -> None:
    assert html_to_notice_text("<pre>신청기간: 10월 1일</pre>접수 방법: 온라인") == (
        "신청기간: 10월 1일\n접수 방법: 온라인"
    )


def test_image_only_or_missing_html_has_no_readable_text() -> None:
    assert html_to_notice_text("<p><img src='notice.png' alt='첨부 공고'></p>") == ""
    assert html_to_notice_text(None) == ""
