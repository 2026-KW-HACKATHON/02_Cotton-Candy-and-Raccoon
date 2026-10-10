"""HTML fixtures preserve the observed list/detail structure without network calls."""

from dataclasses import replace

import httpx
import pytest

from pipeline.config import WolgyeSettings
from pipeline.sources.wolgye1_board import (
    BoardEntry,
    WolgyeSourceError,
    fetch_detail_page,
    fetch_list_page,
    parse_board_page,
    parse_detail_page,
    parse_list_page,
)

POST_SN = "00123456789012345"
IMAGE_URL = "/component/file/ND_fileDownload.do?q_fileSn=1&amp;q_fileId=image-a"
FILE_URL = "/component/file/ND_fileDownload.do?q_fileSn=2&amp;q_fileId=file-b"


def _row(number: str, post_sn: str, department: str = "월계1동") -> str:
    return (
        f'<tr><td class="cell-no">{number}</td>'
        '<td class="cell-subject"><a href="BD_selectBbs.do?q_bbsCode=1042&amp;'
        f'q_bbscttSn={post_sn}">제목</a></td>'
        f'<td class="cell-part">{department}</td>'
        '<td class="cell-date">2026-09-28</td></tr>'
    )


def _list_html(*rows: str) -> str:
    return '<table class="table table-list"><tbody>' + "".join(rows) + "</tbody></table>"


def _board_html(page: int, total: int, page_size: int, *rows: str) -> str:
    total_pages = (total + page_size - 1) // page_size
    return (
        f'<p class="total">총 <strong>{total:,}</strong>건 '
        f'({page}/{total_pages}page)</p>'
        f'<input name="q_currPage" value="{page}">'
        f'<input name="q_rowPerPage" value="{page_size}">'
        + _list_html(*rows)
    )


def _detail_html(
    *, department: str = "월계1동 행정민원팀", body: str | None = None,
    file_row: str | None = None, license_type: int | None = 1,
    post_sn: str = POST_SN,
) -> str:
    if body is None:
        body = f'<div class="txt"><img src="{IMAGE_URL}"></div>'
    if file_row is None:
        file_row = (
            '<ul class="file-list"><li>'
            f'<a href="{FILE_URL}"><span>안내.pdf</span></a>'
            "</li></ul>"
        )
    license_html = (
        f'<a href="http://www.kogl.or.kr/info/licenseType{license_type}.do">공공누리</a>'
        if license_type is not None else ""
    )
    return (
        f'<input name="q_bbscttSn" value="{post_sn}">'
        '<div class="article-view"><h1 class="article-subject">상세 제목</h1>'
        '<table class="table table-article">'
        f'<tr><th>부서/팀</th><td>{department}</td></tr>'
        '<tr><th>등록일</th><td>2026-09-28</td></tr>'
        f'<tr><th>첨부파일</th><td>{file_row}</td></tr></table>'
        f'<div class="article-body">{body}</div></div>{license_html}'
    )


def test_list_deduplicates_pinned_and_numbered_row_and_keeps_other_dong() -> None:
    entries = parse_list_page(_list_html(
        _row("공지", "123", "하계2동"),
        _row("공지", POST_SN),
        _row("2300", POST_SN),
    ))
    assert [(entry.post_sn, entry.department, entry.is_pinned) for entry in entries] == [
        ("123", "하계2동", True), (POST_SN, "월계1동", True),
    ]
    assert entries[1].post_sn.startswith("00")
    assert f"q_bbscttSn={POST_SN}" in entries[1].url


def test_board_page_checks_total_and_numbered_rows() -> None:
    page = parse_board_page(_board_html(
        1, 3, 2, _row("공지", "999", "하계2동"),
        _row("3", POST_SN), _row("2", "222"),
    ), expected_page=1)
    assert (page.total_count, page.total_pages, page.page_size) == (3, 2, 2)
    assert page.regular_post_sns == (POST_SN, "222")
    assert len(page.entries) == 3


@pytest.mark.parametrize("html", [
    _board_html(2, 3, 2, _row("1", "111")),
    _board_html(1, 3, 2, _row("3", "333")),
    _board_html(1, 3, 2, _row("3", "333"), _row("2", "222")).replace(
        'value="2"', 'value="5"',
    ),
])
def test_board_page_rejects_wrong_page_or_truncated_rows(html: str) -> None:
    with pytest.raises(WolgyeSourceError):
        parse_board_page(html, expected_page=1)


@pytest.mark.parametrize("html", [
    "<html></html>",
    _list_html(),
    _list_html(_row("2300", "123", "하계2동")),
    _list_html(_row("알 수 없음", "123")),
    _list_html(_row("2300", "")),
])
def test_invalid_or_incomplete_list_is_rejected(html: str) -> None:
    with pytest.raises(WolgyeSourceError):
        parse_list_page(html)


def test_detail_keeps_image_only_body_and_reads_license() -> None:
    entry = BoardEntry(POST_SN, "제목", "월계1동", "2026-09-28", False)
    notice = parse_detail_page(entry, _detail_html())
    assert notice.post_sn == POST_SN
    assert notice.title == "상세 제목"
    assert notice.department == "월계1동 행정민원팀"
    assert notice.dong_group == "wolgye1"
    assert notice.is_pinned is False
    assert notice.license_type == "KOGL-1"
    assert notice.body_html is not None and "q_fileId=image-a" in notice.body_html


def test_other_dong_is_allowed_only_for_pinned_entry() -> None:
    pinned = BoardEntry("123", "제목", "하계2동", "2026-09-28", True)
    assert parse_detail_page(pinned, _detail_html(
        department="하계2동 복지팀", post_sn="123",
    )).dong_group == (
        "other"
    )
    with pytest.raises(WolgyeSourceError, match="담당 동"):
        parse_detail_page(replace(pinned, is_pinned=False), _detail_html(
            department="하계2동 복지팀", post_sn="123",
        ))


def test_detail_rejects_missing_or_mismatched_post_sn() -> None:
    entry = BoardEntry(POST_SN, "제목", "월계1동", "2026-09-28", False)
    for html in (
        _detail_html(post_sn="999"),
        _detail_html().replace('<input name="q_bbscttSn" value="00123456789012345">', ""),
    ):
        with pytest.raises(WolgyeSourceError, match="게시물 번호"):
            parse_detail_page(entry, html)


def test_missing_body_or_attachment_area_is_not_a_complete_notice() -> None:
    entry = BoardEntry(POST_SN, "제목", "월계1동", "2026-09-28", False)
    with pytest.raises(WolgyeSourceError, match="불완전"):
        parse_detail_page(entry, _detail_html().replace('class="article-body"', 'class="other"'))
    assert parse_detail_page(entry, _detail_html(file_row="첨부파일이 없습니다.")).body_html


def test_unknown_license_does_not_become_a_made_up_type() -> None:
    entry = BoardEntry(POST_SN, "제목", "월계1동", "2026-09-28", False)
    assert parse_detail_page(entry, _detail_html(license_type=None)).license_type is None
    with pytest.raises(WolgyeSourceError, match="공공누리"):
        parse_detail_page(entry, _detail_html(license_type=7))


def test_fetch_uses_page_number_and_validated_detail_url() -> None:
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html;charset=UTF-8"},
                              text="<html>ok</html>")

    settings = WolgyeSettings(1, 2)
    transport = httpx.MockTransport(respond)
    assert "ok" in fetch_list_page(settings, page=2, transport=transport)
    entry = BoardEntry(POST_SN, "제목", "월계1동", "2026-09-28", False)
    assert "ok" in fetch_detail_page(entry, settings, transport=transport)
    assert "q_currPage=2" in seen[0]
    assert f"q_bbscttSn={POST_SN}" in seen[1]


@pytest.mark.parametrize("status,content_type", [(404, "text/html"), (200, "text/plain")])
def test_fetch_rejects_http_or_content_type_error(status: int, content_type: str) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(
        status, headers={"content-type": content_type}, text="bad",
    ))
    with pytest.raises(WolgyeSourceError):
        fetch_list_page(WolgyeSettings(1, 2), transport=transport)


def test_fetch_rejects_explicit_missing_page() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(
        200, headers={"content-type": "text/html"},
        text='<script>alert("데이터가 존재하지 않습니다.")</script>',
    ))
    with pytest.raises(WolgyeSourceError, match="게시물이 없습니다"):
        fetch_list_page(WolgyeSettings(1, 2), transport=transport)


def test_fetch_timeout_is_retryable() -> None:
    transport = httpx.MockTransport(lambda request: (_ for _ in ()).throw(
        httpx.ReadTimeout("timeout", request=request),
    ))
    with pytest.raises(WolgyeSourceError) as error:
        fetch_list_page(WolgyeSettings(1, 2), transport=transport)
    assert error.value.retryable is True


def test_fetch_rate_limit_is_not_immediately_retried() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(429))
    with pytest.raises(WolgyeSourceError, match="429") as error:
        fetch_list_page(WolgyeSettings(1, 2), transport=transport)
    assert error.value.rate_limited is True
    assert error.value.retryable is False


@pytest.mark.parametrize("args", [
    ["--end-page", "0"], ["--start-page", "3", "--end-page", "2"],
    ["--end-page", "1", "--limit", "0"],
    ["--end-page", "1", "--since", "2026-10-10", "--until", "2026-10-01"],
])
def test_backfill_rejects_invalid_bounds_before_io(args, monkeypatch):
    from pipeline import backfill_wolgye1
    from pipeline.cli import main

    def unexpected(*args):
        pytest.fail("invalid bounds must not access the DB")
    monkeypatch.setattr(backfill_wolgye1, "_counts", unexpected)
    assert main(["backfill-wolgye1", *args]) == 2


def test_backfill_reports_failed_snapshot_and_conflicting_pinned_posts(monkeypatch, capsys):
    import json

    from pipeline import backfill_wolgye1
    from pipeline.cli import main
    from pipeline.sources.wolgye1_board import BoardPage

    entry = BoardEntry("123", "공지", "월계1동", "2026-10-01", True)
    first = BoardPage(1, 2, 2, 1, (entry,), ("100",))
    second = BoardPage(2, 2, 2, 1, (replace(entry, title="수정"),), ("101",))
    pages = iter([first, second, WolgyeSourceError("snapshot unavailable")])
    def fetch(*args):
        value = next(pages)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setattr(backfill_wolgye1, "_fetch_list_with_retry", fetch)
    monkeypatch.setattr(backfill_wolgye1, "_counts", lambda _: {"stored": 0, "public": 0})
    monkeypatch.setattr(backfill_wolgye1, "sleep", lambda _: None)
    assert main(["backfill-wolgye1", "--end-page", "2", "--dry-run"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["failed_pages"] == [1]
    assert report["conflicting_post_sns"] == ["123"]
    assert report["duplicate_count"] == 1
    assert report["saved_count"] == 0 and report["snapshot_stable"] is False


def test_backfill_invalid_calendar_date_does_not_discard_other_candidates(monkeypatch, capsys):
    import json

    from pipeline import backfill_wolgye1
    from pipeline.cli import main
    from pipeline.sources.wolgye1_board import BoardPage

    good = BoardEntry("123", "공지", "월계1동", "2026-10-01", False)
    bad = replace(good, post_sn="456", registered_on="2026-02-31")
    page = BoardPage(1, 2, 1, 10, (bad, good), ("456", "123"))
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setattr(backfill_wolgye1, "_fetch_list_with_retry", lambda *_: page)
    monkeypatch.setattr(backfill_wolgye1, "_counts", lambda _: {"stored": 0, "public": 0})
    monkeypatch.setattr(backfill_wolgye1, "sleep", lambda _: None)
    assert main(["backfill-wolgye1", "--end-page", "1", "--dry-run"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["selected_post_sns"] == ["123"]
    assert report["failures"] == [
        {"post_sn": "456", "stage": "listing", "reason_code": "invalid_registered_on"},
    ]


def test_backfill_repeated_regular_rows_cannot_report_complete(monkeypatch, capsys):
    import json

    from pipeline import backfill_wolgye1
    from pipeline.cli import main
    from pipeline.sources.wolgye1_board import BoardPage

    entry = BoardEntry("123", "공지", "월계1동", "2026-10-01", False)
    first = BoardPage(1, 2, 2, 1, (entry,), ("123",))
    second = replace(first, number=2)
    pages = iter([first, second, first])
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setattr(backfill_wolgye1, "_fetch_list_with_retry", lambda *_: next(pages))
    monkeypatch.setattr(backfill_wolgye1, "_counts", lambda _: {"stored": 0, "public": 0})
    monkeypatch.setattr(backfill_wolgye1, "sleep", lambda _: None)
    assert main(["backfill-wolgye1", "--end-page", "2", "--dry-run"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["failed_pages"] == [2]
    assert report["snapshot_stable"] is True
    assert report["scope_complete"] is False
