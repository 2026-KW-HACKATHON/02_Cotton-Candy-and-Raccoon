"""Read Nowon notices through the authenticated Seoul Open API (not RSS).

XML keeps the source ID as text; the live JSON API rounded a 17-digit ID.
DESCRIPTION is parsed from XML once, without a second HTML unescape.
"""

import logging
from dataclasses import dataclass
from time import sleep
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from pipeline.config import NowonSettings
from pipeline.models import RawNotice

API_BASE_URL = "http://openapi.seoul.go.kr:8088"
SERVICE_NAME = "NowonNewsNoticeList"
MAX_PAGE_SIZE = 1000
SAMPLE_LIMIT = 5


class NowonSourceError(ValueError):
    """Safe user-facing error, with no raw response or request URL attached."""

    def __init__(
        self, message: str, *, retryable: bool = False, rate_limited: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.rate_limited = rate_limited


@dataclass(frozen=True, slots=True)
class NowonPage:
    start_index: int
    end_index: int
    total_count: int
    notices: tuple[RawNotice, ...]


@dataclass(frozen=True, slots=True)
class PageStatus:
    start_index: int
    end_index: int
    attempts: int
    succeeded: bool


@dataclass(frozen=True, slots=True)
class NowonCollection:
    """One traversal; complete means no inconsistency was detected."""

    notices: tuple[RawNotice, ...]
    pages: tuple[PageStatus, ...]
    total_count: int | None
    complete: bool
    duplicate_post_sns: tuple[str, ...]
    conflicting_post_sns: tuple[str, ...]


class _RequestLogFilter(logging.Filter):
    def __init__(self, settings: NowonSettings) -> None:
        super().__init__()
        self.settings = settings

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.settings.redact(record.getMessage())
        record.args = ()
        return True


def _field(row: ElementTree.Element, name: str, *, optional: bool = False) -> str | None:
    elements = row.findall(name)
    if len(elements) != 1 or len(elements[0]):
        raise NowonSourceError(f"API 필드 구조가 잘못되었습니다: {name}")
    value = elements[0].text
    if value is None or not value.strip():
        if optional:
            return None
        raise NowonSourceError(f"필수 API 필드가 비어 있습니다: {name}")
    return value


def _required_field(row: ElementTree.Element, name: str) -> str:
    value = _field(row, name)
    assert value is not None
    return value


def _parse_root(content: bytes) -> ElementTree.Element:
    try:
        text = content.decode("utf-8-sig")
        if "<!DOCTYPE" in text or "<!ENTITY" in text:
            raise NowonSourceError("DTD 또는 ENTITY가 포함된 API 응답은 허용하지 않습니다.")
        root = ElementTree.fromstring(text)
    except (UnicodeDecodeError, ElementTree.ParseError):
        raise NowonSourceError("API 응답이 올바른 UTF-8 XML이 아닙니다.") from None

    if root.tag not in (SERVICE_NAME, "RESULT"):
        raise NowonSourceError("예상한 API 응답 구조가 아닙니다.")
    results = [root] if root.tag == "RESULT" else root.findall("RESULT")
    if len(results) != 1:
        raise NowonSourceError("API 결과 코드가 없거나 중복되었습니다.")
    code = _required_field(results[0], "CODE").strip()
    if code == "INFO-100":
        raise NowonSourceError("API 인증 실패: NOWON_NOTICE_API_KEY를 확인하세요.")
    if code == "INFO-200":
        raise NowonSourceError("조회된 공지가 없습니다.")
    if code in ("ERROR-500", "ERROR-600"):
        raise NowonSourceError("API 서버가 일시적인 오류를 반환했습니다.", retryable=True)
    if code != "INFO-000":
        # Never echo untrusted CODE/MESSAGE; they could contain a credential.
        raise NowonSourceError("API가 요청을 거부했습니다. 요청 조건을 확인하세요.")
    return root


def _parse_row(row: ElementTree.Element) -> RawNotice:
    return RawNotice(
        category="nowon",
        dong_group=None,
        is_pinned=False,
        post_sn=_required_field(row, "ID"),
        title=_required_field(row, "TITLE"),
        department=_field(row, "DEPARTMENT", optional=True),
        registered_on=_required_field(row, "PUBDATE"),
        url=_required_field(row, "LINK"),
        body_html=_field(row, "DESCRIPTION", optional=True),
        license_type="KOGL-4",
    )


def parse_notice(content: bytes) -> RawNotice:
    """Parse exactly one row, preserving the existing collect-one contract."""
    root = _parse_root(content)
    rows = root.findall("row")
    if not rows:
        raise NowonSourceError("조회된 공지가 없습니다.")
    if len(rows) != 1:
        raise NowonSourceError("한 건 요청에 여러 공지가 반환되었습니다.")
    return _parse_row(rows[0])


def parse_page(content: bytes, *, start_index: int, end_index: int) -> NowonPage:
    """Validate total count and the expected number of rows in one page."""
    root = _parse_root(content)
    totals = root.findall("list_total_count")
    if len(totals) != 1 or totals[0].text is None:
        raise NowonSourceError("API 총건수가 없거나 중복되었습니다.")
    try:
        total_count = int(totals[0].text)
    except ValueError:
        raise NowonSourceError("API 총건수가 정수가 아닙니다.") from None
    if total_count < 0:
        raise NowonSourceError("API 총건수가 음수입니다.")
    rows = root.findall("row")
    expected = max(0, min(end_index, total_count) - start_index + 1)
    if len(rows) != expected:
        raise NowonSourceError("API 페이지의 행 수가 요청 범위와 일치하지 않습니다.")
    return NowonPage(start_index, end_index, total_count, tuple(_parse_row(row) for row in rows))


def _fetch_xml(
    settings: NowonSettings,
    *,
    start_index: int,
    end_index: int,
    transport: httpx.BaseTransport | None,
) -> bytes:
    if (
        type(start_index) is not int
        or type(end_index) is not int
        or start_index < 1
        or end_index < start_index
        or end_index - start_index + 1 > MAX_PAGE_SIZE
    ):
        raise ValueError("API 인덱스는 양의 정수이며 요청당 최대 1,000건입니다.")
    if settings.nowon_notice_api_key == "sample" and end_index > SAMPLE_LIMIT:
        raise ValueError("sample 키는 1~5번 인덱스만 조회할 수 있습니다.")
    key = quote(settings.nowon_notice_api_key, safe="")
    url = f"{API_BASE_URL}/{key}/xml/{SERVICE_NAME}/{start_index}/{end_index}/"
    timeout = httpx.Timeout(
        settings.http_read_timeout_seconds,
        connect=settings.http_connect_timeout_seconds,
    )
    # httpx's INFO request log includes the credential in the path.
    logger = logging.getLogger("httpx")
    log_filter = _RequestLogFilter(settings)
    logger.addFilter(log_filter)
    try:
        with httpx.Client(timeout=timeout, transport=transport, follow_redirects=False) as client:
            response = client.get(url)
    except httpx.TimeoutException:
        raise NowonSourceError("노원구 API 요청 시간이 초과되었습니다.", retryable=True) from None
    except httpx.RequestError:
        raise NowonSourceError("노원구 API에 연결하지 못했습니다.", retryable=True) from None
    finally:
        logger.removeFilter(log_filter)
    if response.status_code != 200:
        raise NowonSourceError(
            f"노원구 API HTTP 오류: {response.status_code}",
            retryable=response.status_code in (429, 500, 502, 503, 504),
            rate_limited=response.status_code == 429,
        )
    return response.content


def collect_page(
    settings: NowonSettings,
    *,
    start_index: int,
    end_index: int,
    transport: httpx.BaseTransport | None = None,
) -> NowonPage:
    content = _fetch_xml(
        settings, start_index=start_index, end_index=end_index, transport=transport,
    )
    return parse_page(content, start_index=start_index, end_index=end_index)


def collect_one(
    settings: NowonSettings, *, transport: httpx.BaseTransport | None = None,
) -> RawNotice:
    """Fetch indices 1..1 once. Retryable failures are reported, not retried here."""
    return parse_notice(_fetch_xml(
        settings, start_index=1, end_index=1, transport=transport,
    ))


def collect_all(
    settings: NowonSettings,
    *,
    page_size: int = MAX_PAGE_SIZE,
    overlap: int | None = None,
    transport: httpx.BaseTransport | None = None,
) -> NowonCollection:
    """Traverse all pages; retry transient failures and flag uncertain results.

    The caller may save returned notices, but must not hide existing notices
    unless a separate visibility policy accepts this collection's coverage.
    """
    if type(page_size) is not int or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError("page_size는 1~1,000 사이의 정수여야 합니다.")
    if overlap is None:
        overlap = min(10, page_size - 1)
    if type(overlap) is not int or not 0 <= overlap < page_size:
        raise ValueError("overlap은 0 이상 page_size 미만의 정수여야 합니다.")
    is_sample = settings.nowon_notice_api_key == "sample"
    if is_sample:
        page_size = min(page_size, SAMPLE_LIMIT)
    pages: list[PageStatus] = []
    by_id: dict[str, RawNotice] = {}
    duplicates: set[str] = set()
    conflicts: set[str] = set()
    complete = not is_sample

    def fetch(start: int, end: int) -> NowonPage | None:
        nonlocal complete
        for attempt in range(1, 4):
            try:
                page = collect_page(
                    settings, start_index=start, end_index=end, transport=transport,
                )
            except NowonSourceError as exc:
                if exc.retryable and not exc.rate_limited and attempt < 3:
                    sleep(0.2 * 2 ** (attempt - 1))
                    continue
                pages.append(PageStatus(start, end, attempt, False))
                complete = False
                return None
            pages.append(PageStatus(start, end, attempt, True))
            return page
        raise AssertionError("retry loop must return")

    first_page = fetch(1, page_size)
    if first_page is None:
        return NowonCollection((), tuple(pages), None, False, (), ())
    target_count = first_page.total_count
    first_id = first_page.notices[0].post_sn if first_page.notices else None

    def include(page: NowonPage) -> None:
        nonlocal complete
        if page.total_count != target_count:
            complete = False
        for notice in page.notices:
            previous = by_id.setdefault(notice.post_sn, notice)
            if previous is not notice:
                duplicates.add(notice.post_sn)
                if previous != notice:
                    conflicts.add(notice.post_sn)
                    complete = False

    include(first_page)
    end_index = page_size
    while end_index < target_count and not is_sample:
        start_index = end_index - overlap + 1
        end_index = min(start_index + page_size - 1, target_count)
        page = fetch(start_index, end_index)
        if page is not None:
            include(page)
    if not is_sample and target_count > 0:
        confirmation = fetch(1, 1)
        if (
            confirmation is None
            or confirmation.total_count != target_count
            or not confirmation.notices
            or confirmation.notices[0].post_sn != first_id
        ):
            complete = False
    if target_count == 0 or len(by_id) != target_count:
        complete = False
    return NowonCollection(
        tuple(by_id.values()),
        tuple(pages),
        target_count,
        complete,
        tuple(sorted(duplicates)),
        tuple(sorted(conflicts)),
    )
