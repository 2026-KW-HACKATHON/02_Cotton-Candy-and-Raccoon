"""Observable page traversal behavior without external API or database calls."""

from xml.sax.saxutils import escape

import httpx
import pytest

from pipeline.config import NowonSettings
from pipeline.sources.nowon_api import (
    NowonSourceError,
    collect_all,
    collect_page,
    parse_page,
)


def _xml(ids: list[str], total: int | str) -> bytes:
    rows = "".join(
        "<row>"
        f"<ID>{escape(post_sn)}</ID><TITLE>Title {escape(post_sn)}</TITLE>"
        f"<LINK>https://example.com/{escape(post_sn)}</LINK>"
        "<PUBDATE>2026-09-23</PUBDATE><DEPARTMENT>Team</DEPARTMENT>"
        "<DESCRIPTION>&lt;p&gt;Body&lt;/p&gt;</DESCRIPTION>"
        "</row>"
        for post_sn in ids
    )
    return (
        f"<NowonNewsNoticeList><list_total_count>{total}</list_total_count>"
        "<RESULT><CODE>INFO-000</CODE><MESSAGE>OK</MESSAGE></RESULT>"
        f"{rows}</NowonNewsNoticeList>"
    ).encode()


def _settings(key: str = "test-key") -> NowonSettings:
    return NowonSettings(key, 2.0, 7.0)


def test_multiple_pages_with_overlap_and_confirmation() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requests.append(path)
        if path.endswith("/1/2/"):
            return httpx.Response(200, content=_xml(["001", "002"], 5))
        if path.endswith("/2/3/"):
            return httpx.Response(200, content=_xml(["002", "003"], 5))
        if path.endswith("/3/4/"):
            return httpx.Response(200, content=_xml(["003", "004"], 5))
        if path.endswith("/4/5/"):
            return httpx.Response(200, content=_xml(["004", "005"], 5))
        if path.endswith("/1/1/"):
            return httpx.Response(200, content=_xml(["001"], 5))
        pytest.fail(f"unexpected page: {path}")

    result = collect_all(
        _settings(), page_size=2, overlap=1, transport=httpx.MockTransport(handler),
    )
    assert result.complete is True
    assert result.total_count == 5
    assert [notice.post_sn for notice in result.notices] == [
        "001", "002", "003", "004", "005",
    ]
    assert result.duplicate_post_sns == ("002", "003", "004")
    assert result.conflicting_post_sns == ()
    assert all(page.succeeded for page in result.pages)
    assert len(requests) == 5


def test_middle_page_timeout_returns_partial_notices_and_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pipeline.sources.nowon_api.sleep", lambda _: None)
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        path = request.url.path
        if path.endswith("/2/3/"):
            attempts += 1
            raise httpx.ReadTimeout("timeout", request=request)
        if path.endswith("/1/2/"):
            return httpx.Response(200, content=_xml(["001", "002"], 4))
        if path.endswith("/3/4/"):
            return httpx.Response(200, content=_xml(["003", "004"], 4))
        if path.endswith("/1/1/"):
            return httpx.Response(200, content=_xml(["001"], 4))
        pytest.fail(f"unexpected page: {path}")

    result = collect_all(
        _settings(), page_size=2, overlap=1, transport=httpx.MockTransport(handler),
    )
    assert attempts == 3
    assert [notice.post_sn for notice in result.notices] == ["001", "002", "003", "004"]
    assert result.complete is False
    assert [(page.start_index, page.end_index, page.attempts) for page in result.pages
            if not page.succeeded] == [(2, 3, 3)]


def test_retryable_page_succeeds_on_second_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pipeline.sources.nowon_api.sleep", lambda _: None)
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith("/1/2/"):
            attempts += 1
            if attempts == 1:
                return httpx.Response(503)
            return httpx.Response(200, content=_xml(["001", "002"], 2))
        return httpx.Response(200, content=_xml(["001"], 2))

    result = collect_all(_settings(), page_size=2, transport=httpx.MockTransport(handler))
    assert result.complete is True
    assert result.pages[0].attempts == 2


def test_repeated_page_ids_cannot_look_complete() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/1/1/"):
            return httpx.Response(200, content=_xml(["001"], 4))
        return httpx.Response(200, content=_xml(["001", "002"], 4))

    result = collect_all(
        _settings(), page_size=2, overlap=1, transport=httpx.MockTransport(handler),
    )
    assert result.complete is False
    assert len(result.notices) == 2
    assert result.total_count == 4


def test_total_change_and_first_id_change_are_incomplete() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/1/2/"):
            return httpx.Response(200, content=_xml(["001", "002"], 3))
        if path.endswith("/2/3/"):
            return httpx.Response(200, content=_xml(["002", "003"], 4))
        return httpx.Response(200, content=_xml(["NEW"], 4))

    result = collect_all(
        _settings(), page_size=2, overlap=1, transport=httpx.MockTransport(handler),
    )
    assert result.complete is False
    assert result.total_count == 3


def test_first_id_change_with_unchanged_total_is_incomplete() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/1/1/"):
            return httpx.Response(200, content=_xml(["NEW"], 2))
        return httpx.Response(200, content=_xml(["001", "002"], 2))

    result = collect_all(_settings(), page_size=2, transport=httpx.MockTransport(handler))
    assert result.complete is False


def test_sample_key_never_claims_full_coverage() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, content=_xml(["001", "002", "003", "004", "005"], 100))

    result = collect_all(_settings("sample"), transport=httpx.MockTransport(handler))
    assert result.complete is False
    assert len(result.notices) == 5
    assert len(requests) == 1
    assert requests[0].endswith("/1/5/")


@pytest.mark.parametrize("start,end", [(0, 1), (2, 1), (1, 1001), (True, 2)])
def test_invalid_page_range_is_rejected_without_request(start: int, end: int) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"should not request {request.url.path}")

    with pytest.raises(ValueError):
        collect_page(
            _settings(), start_index=start, end_index=end,
            transport=httpx.MockTransport(unexpected),
        )


def test_sample_index_limit_is_rejected_without_request() -> None:
    with pytest.raises(ValueError, match="sample"):
        collect_page(_settings("sample"), start_index=5, end_index=6)


@pytest.mark.parametrize("content", [
    _xml(["001"], "invalid"),
    _xml(["001"], -1),
    _xml(["001"], 2),
    _xml(["001", "002"], 1),
    _xml(["001"], 1).replace(b"<list_total_count>1</list_total_count>", b""),
])
def test_bad_total_or_row_count_is_rejected(content: bytes) -> None:
    with pytest.raises(NowonSourceError):
        parse_page(content, start_index=1, end_index=2)


def test_conflicting_duplicate_is_reported_without_silent_overwrite() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/1/2/"):
            return httpx.Response(200, content=_xml(["001", "002"], 3))
        if path.endswith("/2/3/"):
            changed = _xml(["002", "003"], 3).replace(b"Title 002", b"Changed 002")
            return httpx.Response(200, content=changed)
        return httpx.Response(200, content=_xml(["001"], 3))

    result = collect_all(
        _settings(), page_size=2, overlap=1, transport=httpx.MockTransport(handler),
    )
    assert result.complete is False
    assert result.conflicting_post_sns == ("002",)
    assert result.notices[1].title == "Title 002"
