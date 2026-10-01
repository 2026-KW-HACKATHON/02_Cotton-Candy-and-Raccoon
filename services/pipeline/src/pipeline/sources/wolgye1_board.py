"""Read Wolgye 1-dong notices from the official HTML board, without RSS."""

import re
from dataclasses import dataclass
from math import ceil
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag

from pipeline.config import WolgyeSettings
from pipeline.models import RawNotice

BOARD_ORIGIN = "https://www.nowon.kr"
LIST_PATH = "/dong/user/bbs/BD_selectBbsList.do"
DETAIL_PATH = "/dong/user/bbs/BD_selectBbs.do"
LIST_URL = f"{BOARD_ORIGIN}{LIST_PATH}?q_bbsCode=1042&q_deptCode=1047"
DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
LICENSE_PATTERN = re.compile(r"/licenseType([1-4])\.do\Z")
PAGE_PATTERN = re.compile(r"\(([0-9]+)/([0-9]+)page\)")


class WolgyeSourceError(ValueError):
    """A safe error when the public board is unavailable or structurally invalid."""

    def __init__(
        self, message: str, *, retryable: bool = False, rate_limited: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.rate_limited = rate_limited


@dataclass(frozen=True, slots=True)
class BoardEntry:
    post_sn: str
    title: str
    department: str
    registered_on: str
    is_pinned: bool

    @property
    def url(self) -> str:
        query = urlencode({
            "q_bbsCode": "1042", "q_bbscttSn": self.post_sn, "q_deptCode": "1047",
        })
        return f"{BOARD_ORIGIN}{DETAIL_PATH}?{query}"


@dataclass(frozen=True, slots=True)
class BoardPage:
    number: int
    total_count: int
    total_pages: int
    page_size: int
    entries: tuple[BoardEntry, ...]
    regular_post_sns: tuple[str, ...]


def _request_html(
    url: str, settings: WolgyeSettings, *, transport: httpx.BaseTransport | None = None,
) -> str:
    timeout = httpx.Timeout(
        settings.http_read_timeout_seconds,
        connect=settings.http_connect_timeout_seconds,
    )
    try:
        with httpx.Client(timeout=timeout, transport=transport, follow_redirects=False) as client:
            response = client.get(url)
    except httpx.TimeoutException:
        raise WolgyeSourceError(
            "월계1동 게시판 요청 시간이 초과되었습니다.", retryable=True,
        ) from None
    except httpx.RequestError:
        raise WolgyeSourceError("월계1동 게시판에 연결하지 못했습니다.", retryable=True) from None
    if response.status_code != 200:
        raise WolgyeSourceError(
            f"월계1동 게시판 HTTP 오류: {response.status_code}",
            retryable=response.status_code in (500, 502, 503, 504),
            rate_limited=response.status_code == 429,
        )
    if not response.headers.get("content-type", "").lower().startswith("text/html"):
        raise WolgyeSourceError("월계1동 게시판이 HTML을 반환하지 않았습니다.")
    if 'alert("데이터가 존재하지 않습니다.")' in response.text:
        raise WolgyeSourceError("월계1동 게시판에 게시물이 없습니다.")
    return response.text


def fetch_list_page(
    settings: WolgyeSettings, *, page: int = 1,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Fetch one numbered page of the official board."""
    if type(page) is not int or page < 1:
        raise ValueError("page는 1 이상의 정수여야 합니다.")
    url = LIST_URL if page == 1 else f"{LIST_URL}&q_currPage={page}"
    return _request_html(url, settings, transport=transport)


def _post_sn(reference: str) -> str:
    try:
        url = urlsplit(urljoin(f"{BOARD_ORIGIN}{LIST_PATH}", reference))
        query = parse_qs(url.query, keep_blank_values=True)
        values = query.get("q_bbscttSn")
        valid = (
            url.scheme == "https" and url.netloc == "www.nowon.kr"
            and url.path == DETAIL_PATH and query.get("q_bbsCode") == ["1042"]
            and values is not None and len(values) == 1
            and re.fullmatch(r"[0-9]+", values[0]) is not None
        )
    except ValueError:
        valid = False
    if not valid:
        raise WolgyeSourceError("공지 목록의 게시물 URL 또는 번호가 올바르지 않습니다.")
    return values[0]


def _required_cell(row: Tag, selector: str) -> str:
    cell = row.select_one(selector)
    value = cell.get_text(" ", strip=True) if cell is not None else ""
    if not value:
        raise WolgyeSourceError("공지 목록의 필수 항목이 없습니다.")
    return value


def parse_list_page(html: str) -> tuple[BoardEntry, ...]:
    """Deduplicate pinned and numbered rows; never trust deptCode for pinned rows."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.table-list")
    if table is None:
        raise WolgyeSourceError("공지 목록 표를 확인할 수 없습니다.")
    rows = table.select("tbody tr")
    if not rows:
        raise WolgyeSourceError("공지 목록 행을 확인할 수 없습니다.")

    entries: dict[str, BoardEntry] = {}
    for row in rows:
        number = _required_cell(row, ".cell-no")
        is_pinned = number == "공지"
        if not is_pinned and re.fullmatch(r"[0-9]+", number) is None:
            raise WolgyeSourceError("공지 목록의 행 구분을 확인할 수 없습니다.")
        link = row.select_one(".cell-subject a[href]")
        if link is None:
            raise WolgyeSourceError("공지 목록의 제목 링크가 없습니다.")
        post_sn = _post_sn(link["href"])
        department = _required_cell(row, ".cell-part")
        if not is_pinned and not department.startswith("월계1동"):
            raise WolgyeSourceError("월계1동 일반 목록에 다른 동 공지가 있습니다.")
        registered_on = _required_cell(row, ".cell-date")
        if not DATE_PATTERN.fullmatch(registered_on):
            raise WolgyeSourceError("공지 목록의 등록일 형식이 올바르지 않습니다.")
        title = link.get_text(" ", strip=True)
        if not title:
            raise WolgyeSourceError("공지 목록의 제목이 비어 있습니다.")
        entry = BoardEntry(post_sn, title, department, registered_on, is_pinned)
        previous = entries.get(post_sn)
        if previous is not None:
            if (
                previous.title, previous.department, previous.registered_on
            ) != (entry.title, entry.department, entry.registered_on):
                raise WolgyeSourceError("같은 게시물 번호의 목록 정보가 충돌합니다.")
            entry = BoardEntry(
                post_sn, title, department, registered_on, previous.is_pinned or is_pinned,
            )
        entries[post_sn] = entry
    return tuple(entries.values())


def parse_board_page(html: str, *, expected_page: int) -> BoardPage:
    """Check the displayed range and numbered rows before trusting a list page."""
    soup = BeautifulSoup(html, "html.parser")
    total = soup.select_one("p.total strong")
    total_label = soup.select_one("p.total")
    current_input = soup.select_one('input[name="q_currPage"]')
    size_input = soup.select_one('input[name="q_rowPerPage"]')
    if any(element is None for element in (total, total_label, current_input, size_input)):
        raise WolgyeSourceError("공지 목록의 페이지 정보를 확인할 수 없습니다.")
    match = PAGE_PATTERN.search(total_label.get_text(" ", strip=True))
    try:
        total_count = int(total.get_text(strip=True).replace(",", ""))
        current_page = int(current_input.get("value", ""))
        page_size = int(size_input.get("value", ""))
        shown_page, total_pages = map(int, match.groups()) if match else (0, 0)
    except (TypeError, ValueError):
        raise WolgyeSourceError("공지 목록의 페이지 숫자가 올바르지 않습니다.") from None
    if (
        type(expected_page) is not int or expected_page < 1
        or total_count < 1 or page_size < 1 or total_pages != ceil(total_count / page_size)
        or current_page != expected_page or shown_page != expected_page
        or not 1 <= expected_page <= total_pages
    ):
        raise WolgyeSourceError("공지 목록의 페이지 범위가 요청과 일치하지 않습니다.")

    entries = parse_list_page(html)
    regular_post_sns: list[str] = []
    for row in soup.select("table.table-list tbody tr"):
        number = _required_cell(row, ".cell-no")
        if number == "공지":
            continue
        link = row.select_one(".cell-subject a[href]")
        if link is None:
            raise WolgyeSourceError("공지 목록의 제목 링크가 없습니다.")
        regular_post_sns.append(_post_sn(link["href"]))
    expected_rows = min(page_size, total_count - (expected_page - 1) * page_size)
    if len(regular_post_sns) != expected_rows:
        raise WolgyeSourceError("공지 목록의 일반 공지 행 수가 예상 범위와 다릅니다.")
    return BoardPage(
        expected_page, total_count, total_pages, page_size,
        entries, tuple(regular_post_sns),
    )


def fetch_detail_page(
    entry: BoardEntry, settings: WolgyeSettings, *,
    transport: httpx.BaseTransport | None = None,
) -> str:
    return _request_html(entry.url, settings, transport=transport)


def _detail_value(table: Tag, label: str) -> str:
    for heading in table.select("th"):
        if heading.get_text(" ", strip=True) == label:
            cell = heading.find_next_sibling("td")
            value = cell.get_text(" ", strip=True) if cell is not None else ""
            if value:
                return value
    raise WolgyeSourceError(f"공지 상세의 {label}을(를) 확인할 수 없습니다.")


def _license_type(soup: BeautifulSoup) -> str | None:
    types: set[str] = set()
    for link in soup.select('a[href*="licenseType"]'):
        href = link.get("href")
        if not isinstance(href, str):
            continue
        match = LICENSE_PATTERN.search(urlsplit(href).path)
        if match is None:
            raise WolgyeSourceError("공지 상세의 공공누리 유형을 확인할 수 없습니다.")
        types.add(f"KOGL-{match.group(1)}")
    if len(types) > 1:
        raise WolgyeSourceError("공지 상세에 서로 다른 공공누리 유형이 표시됩니다.")
    return next(iter(types), None)


def parse_detail_page(entry: BoardEntry, html: str) -> RawNotice:
    """Keep the entire original body HTML, including image-only notices."""
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one(".article-view")
    if article is None:
        raise WolgyeSourceError("공지 상세 본문을 확인할 수 없습니다.")
    identifiers = [field.get("value") for field in soup.select('input[name="q_bbscttSn"]')]
    if identifiers != [entry.post_sn]:
        raise WolgyeSourceError("공지 상세의 게시물 번호가 목록과 일치하지 않습니다.")
    title_element = article.select_one("h1.article-subject")
    table = article.select_one("table.table-article")
    body = article.select_one(".article-body")
    if title_element is None or table is None or body is None:
        raise WolgyeSourceError("공지 상세의 제목·정보·본문 영역이 불완전합니다.")
    title = title_element.get_text(" ", strip=True)
    if not title:
        raise WolgyeSourceError("공지 상세의 제목이 비어 있습니다.")
    department = _detail_value(table, "부서/팀")
    registered_on = _detail_value(table, "등록일")
    if not DATE_PATTERN.fullmatch(registered_on):
        raise WolgyeSourceError("공지 상세의 등록일 형식이 올바르지 않습니다.")
    if not entry.is_pinned and not department.startswith("월계1동"):
        raise WolgyeSourceError("월계1동 일반 공지의 담당 동이 일치하지 않습니다.")
    return RawNotice(
        category="dong",
        dong_group="wolgye1" if department.startswith("월계1동") else "other",
        is_pinned=entry.is_pinned,
        post_sn=entry.post_sn,
        title=title,
        department=department,
        registered_on=registered_on,
        url=entry.url,
        body_html=body.decode_contents().strip() or None,
        license_type=_license_type(soup),
    )
