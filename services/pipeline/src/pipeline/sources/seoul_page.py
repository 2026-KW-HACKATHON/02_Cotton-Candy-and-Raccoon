"""Read and verify Seoul's official WordPress article, excluding page furniture."""

import re
from dataclasses import dataclass, field
from typing import cast
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from pipeline.config import SeoulNewsSettings
from pipeline.models import LicenseType, RawSeoulNotice

BOARD_SLUGS = {
    "21": "traffic",
    "22": "safe",
    "23": "citybuild",
    "24": "economy",
    "25": "env",
    "26": "culture",
    "27": "welfare",
    "30": "gov",
}
BODY_PATTERN = re.compile(r"<!--\s*wp본문시작\s*-->(.*?)<!--\s*wp본문끝\s*-->", re.S)


class SeoulPageError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        rate_limited: bool = False,
        reason_code: str = "page_unavailable",
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.rate_limited = rate_limited
        self.reason_code = "rate_limited" if rate_limited else reason_code


@dataclass(frozen=True, slots=True)
class SeoulPage:
    source_board: str
    post_sn: str
    url: str
    body_html: str = field(repr=False)
    license_type: LicenseType | None


def notice_url(notice: RawSeoulNotice) -> str:
    """Only construct URLs from the checked API board and ASCII text ID."""
    return f"https://news.seoul.go.kr/{BOARD_SLUGS[notice.source_board]}/archives/{notice.post_sn}"


def parse_page(notice: RawSeoulNotice, html: str) -> SeoulPage:
    url = notice_url(notice)
    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.select('link[rel="canonical"]')
    if len(canonical) != 1 or canonical[0].get("href") != url:
        raise SeoulPageError(
            "서울시 원문의 canonical 주소가 요청한 공지와 다릅니다.",
            reason_code="page_identity_mismatch",
        )
    rating_forms = soup.select("#frmRating")
    if rating_forms:
        if len(rating_forms) != 1:
            raise SeoulPageError(
                "서울시 식별 폼이 중복됩니다.", reason_code="page_identity_mismatch"
            )
        for name, value in (("blog_id", notice.source_board), ("post_id", notice.post_sn)):
            nodes = rating_forms[0].select(f'input[name="{name}"]')
            if len(nodes) != 1 or nodes[0].get("value") != value:
                raise SeoulPageError(
                    "서울시 원문의 게시판·게시물 번호를 확인하지 못했습니다.",
                    reason_code="page_identity_mismatch",
                )
    else:
        # Official articles can omit ratings. Require two exact article URLs;
        # never use a fallback to override a contradictory existing form.
        og_urls = soup.select('meta[property="og:url"]')
        if len(og_urls) != 1 or og_urls[0].get("content") != url:
            raise SeoulPageError(
                "서울시 원문의 대체 식별 정보를 확인하지 못했습니다.",
                reason_code="page_identity_mismatch",
            )
    bodies = soup.select("#post_content")
    matches = BODY_PATTERN.findall(html)
    if len(bodies) != 1 or len(matches) != 1:
        raise SeoulPageError(
            "서울시 원문의 실제 본문 경계를 확인하지 못했습니다.", reason_code="page_body_invalid"
        )
    # Require markers in the article, not a script or another page area.
    if len(BODY_PATTERN.findall(str(bodies[0]))) != 1:
        raise SeoulPageError(
            "서울시 본문 경계가 공지 영역 밖에 있습니다.", reason_code="page_body_invalid"
        )
    licenses: set[str] = set()
    for anchor in soup.select("#content_manager_info a[href]"):
        parsed = urlsplit(urljoin(url, anchor["href"]))
        if parsed.hostname not in ("www.kogl.or.kr", "kogl.or.kr"):
            continue
        match = re.fullmatch(r"/info/licenseType([1-4])\.do", parsed.path)
        if not match:
            raise SeoulPageError(
                "서울시 공공누리 유형을 해석하지 못했습니다.", reason_code="page_license_invalid"
            )
        licenses.add("KOGL-" + match[1])
    if len(licenses) > 1:
        raise SeoulPageError(
            "서울시 공공누리 표시가 서로 충돌합니다.", reason_code="page_license_invalid"
        )
    license_type = cast(LicenseType | None, next(iter(licenses), None))
    return SeoulPage(notice.source_board, notice.post_sn, url, matches[0], license_type)


def fetch_notice_page(
    notice: RawSeoulNotice,
    settings: SeoulNewsSettings,
    *,
    transport: httpx.BaseTransport | None = None,
) -> SeoulPage:
    timeout = httpx.Timeout(
        settings.http_read_timeout_seconds,
        connect=settings.http_connect_timeout_seconds,
    )
    try:
        with httpx.Client(timeout=timeout, transport=transport, follow_redirects=False) as client:
            response = client.get(notice_url(notice))
    except httpx.TimeoutException:
        raise SeoulPageError(
            "서울시 원문 요청 시간이 초과되었습니다.", retryable=True, reason_code="page_timeout"
        ) from None
    except httpx.RequestError:
        raise SeoulPageError(
            "서울시 원문에 연결하지 못했습니다.",
            retryable=True,
            reason_code="page_connection_failed",
        ) from None
    if response.status_code != 200:
        raise SeoulPageError(
            f"서울시 원문 HTTP 오류: {response.status_code}",
            retryable=response.status_code in (429, 500, 502, 503, 504),
            rate_limited=response.status_code == 429,
            reason_code=(
                "page_redirect"
                if 300 <= response.status_code < 400
                else "page_not_found"
                if response.status_code in (404, 410)
                else f"page_http_{response.status_code}"
            ),
        )
    if not response.headers.get("content-type", "").lower().startswith("text/html"):
        raise SeoulPageError(
            "서울시 원문이 HTML을 반환하지 않았습니다.", reason_code="page_content_type_invalid"
        )
    return parse_page(notice, response.text)
