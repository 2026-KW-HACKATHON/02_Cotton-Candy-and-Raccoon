"""Fetch the public Nowon notice page linked by the authenticated API."""

from urllib.parse import parse_qs, urlsplit, urlunsplit

import httpx

from pipeline.config import NowonSettings
from pipeline.models import RawNotice


class NowonPageError(ValueError):
    """A safe, user-facing failure while retrieving an original notice page."""

    def __init__(
        self, message: str, *, retryable: bool = False, rate_limited: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.rate_limited = rate_limited


class NowonPageMissing(NowonPageError):
    """The board returned its explicit missing-data page with HTTP 200."""


def normalize_nowon_notice_url(notice: RawNotice) -> str:
    """Validate and normalize the public Nowon board URL for this notice."""
    try:
        parsed = urlsplit(notice.url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        valid = (
            notice.category == "nowon"
            and parsed.scheme in ("http", "https")
            and parsed.hostname == "www.nowon.kr"
            and parsed.port in (None, 80, 443)
            and parsed.username is None and parsed.password is None
            and not parsed.fragment
            and parsed.path == "/www/user/bbs/BD_selectBbs.do"
            and query.get("q_bbsCode") == ["1001"]
            and query.get("q_bbscttSn") == [notice.post_sn]
        )
    except ValueError:
        valid = False
    if not valid:
        raise NowonPageError("API 원문 URL이 예상한 노원구 공지 주소가 아닙니다.")
    return urlunsplit(("https", "www.nowon.kr", parsed.path, parsed.query, ""))


def fetch_notice_page(
    notice: RawNotice, settings: NowonSettings, *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[str, str]:
    """Return the validated HTTPS page URL and HTML, without following redirects."""
    url = normalize_nowon_notice_url(notice)
    timeout = httpx.Timeout(
        settings.http_read_timeout_seconds,
        connect=settings.http_connect_timeout_seconds,
    )
    try:
        with httpx.Client(timeout=timeout, transport=transport, follow_redirects=False) as client:
            response = client.get(url)
    except httpx.TimeoutException:
        raise NowonPageError(
            "노원구 원문 페이지 요청 시간이 초과되었습니다.", retryable=True,
        ) from None
    except httpx.RequestError:
        raise NowonPageError("노원구 원문 페이지에 연결하지 못했습니다.", retryable=True) from None
    if response.status_code != 200:
        raise NowonPageError(
            f"노원구 원문 페이지 HTTP 오류: {response.status_code}",
            retryable=response.status_code in (429, 500, 502, 503, 504),
            rate_limited=response.status_code == 429,
        )
    if not response.headers.get("content-type", "").lower().startswith("text/html"):
        raise NowonPageError("원문 페이지가 HTML을 반환하지 않았습니다.")
    if 'alert("데이터가 존재하지 않습니다.")' in response.text:
        raise NowonPageMissing("노원구 원문 페이지에 게시물이 없습니다.")
    return url, response.text
