"""Read exactly one SeoulNewsList XML row; no DB, files or Gemini writes."""

import logging
from dataclasses import dataclass
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from pipeline.config import SeoulNewsSettings
from pipeline.models import RawSeoulNotice

API_BASE_URL = "http://openapi.seoul.go.kr:8088"
SERVICE_NAME = "SeoulNewsList"


class SeoulSourceError(ValueError):
    """Safe error; never include raw response, key or credential-bearing URL."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        rate_limited: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.rate_limited = rate_limited


class _RequestLogFilter(logging.Filter):
    def __init__(self, settings: SeoulNewsSettings) -> None:
        super().__init__()
        self.settings = settings

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.settings.redact(record.getMessage())
        record.args = ()
        return True


def _field(row: ElementTree.Element, name: str, *, optional: bool = False) -> str | None:
    nodes = row.findall(name)
    if len(nodes) != 1 or len(nodes[0]):
        raise SeoulSourceError(f"서울시 API 필드 구조가 잘못되었습니다: {name}")
    value = nodes[0].text
    if value is None or not value.strip():
        if optional:
            return None
        raise SeoulSourceError(f"서울시 API 필수 필드가 비어 있습니다: {name}")
    return value


def _required(row: ElementTree.Element, name: str) -> str:
    value = _field(row, name)
    assert value is not None
    return value


def _response_root(content: bytes, *, allow_empty: bool = False) -> ElementTree.Element:
    try:
        text = content.decode("utf-8-sig")
        if "<!DOCTYPE" in text or "<!ENTITY" in text:
            raise SeoulSourceError("서울시 API의 DTD·ENTITY 응답은 허용하지 않습니다.")
        root = ElementTree.fromstring(text)
    except (UnicodeDecodeError, ElementTree.ParseError):
        raise SeoulSourceError("서울시 API 응답이 올바른 UTF-8 XML이 아닙니다.") from None
    if root.tag not in (SERVICE_NAME, "RESULT"):
        raise SeoulSourceError("예상한 서울시 API 응답 구조가 아닙니다.")
    results = [root] if root.tag == "RESULT" else root.findall("RESULT")
    if len(results) != 1:
        raise SeoulSourceError("서울시 API 결과 코드가 없거나 중복되었습니다.")
    code = _required(results[0], "CODE").strip()
    if code == "INFO-100":
        raise SeoulSourceError("서울시 API 인증 실패: SEOUL_NEWS_API_KEY를 확인하세요.")
    if code == "INFO-200":
        if allow_empty and not root.findall("row"):
            return root
        raise SeoulSourceError("조회된 서울시 공지가 없습니다.")
    if code in ("ERROR-500", "ERROR-600"):
        raise SeoulSourceError("서울시 API 서버의 일시적인 오류입니다.", retryable=True)
    if code != "INFO-000":
        raise SeoulSourceError("서울시 API가 요청을 거부했습니다.")
    return root


def _total_count(root: ElementTree.Element) -> int:
    totals = root.findall("list_total_count")
    if len(totals) != 1 or len(totals[0]):
        raise SeoulSourceError("서울시 API 총건수 구조가 올바르지 않습니다.")
    total = totals[0].text or ""
    try:
        if not total.isascii() or not total.isdecimal() or int(total) < 1:
            raise ValueError
        return int(total)
    except ValueError:
        raise SeoulSourceError("서울시 API 총건수가 올바르지 않습니다.") from None


def _parse_row(row: ElementTree.Element) -> RawSeoulNotice:
    try:
        return RawSeoulNotice(
            source_board=_required(row, "BLOG_ID"),
            post_sn=_required(row, "POST_ID"),
            blog_name=_required(row, "BLOG_NAME"),
            title=_required(row, "POST_TITLE"),
            registered_on=_required(row, "PUBLISH_DATE"),
            modified_on=_field(row, "MODIFY_DATE", optional=True),
            post_status=_required(row, "POST_STATUS"),
            department=_field(row, "MANAGER_DEPT", optional=True),
            body_html=_field(row, "POST_CONTENT", optional=True),
            excerpt_html=_field(row, "POST_EXCERPT", optional=True),
            thumbnail_url=_field(row, "THUMB_URI", optional=True),
        )
    except (TypeError, ValueError) as error:
        if isinstance(error, SeoulSourceError):
            raise
        raise SeoulSourceError(
            "서울시 API의 게시판·게시물 번호 또는 공개 상태가 유효하지 않습니다.",
        ) from None


def parse_notice(content: bytes) -> RawSeoulNotice:
    """Preserve text IDs and HTML without a second HTML unescape."""
    root = _response_root(content)
    _total_count(root)
    rows = root.findall("row")
    if len(rows) != 1:
        raise SeoulSourceError("서울시 한 건 요청의 공지 개수가 올바르지 않습니다.")
    return _parse_row(rows[0])


@dataclass(frozen=True, slots=True)
class SeoulApiPage:
    start_index: int
    end_index: int
    total_count: int
    notices: tuple[RawSeoulNotice, ...]


def parse_page(content: bytes, start: int, end: int, board: str) -> SeoulApiPage:
    root = _response_root(content, allow_empty=True)
    code = root.findtext("CODE") if root.tag == "RESULT" else root.findtext("RESULT/CODE")
    if code and code.strip() == "INFO-200":
        return SeoulApiPage(start, end, 0, ())
    total = _total_count(root)
    rows = root.findall("row")
    expected = max(0, min(end, total) - start + 1)
    if len(rows) != expected:
        raise SeoulSourceError("서울시 API 페이지의 행 개수가 요청 범위와 다릅니다.")
    notices = tuple(_parse_row(row) for row in rows)
    if any(n.source_board != board for n in notices):
        raise SeoulSourceError("서울시 API 응답의 게시판 번호가 요청과 다릅니다.")
    return SeoulApiPage(start, end, total, notices)


def collect_one(
    settings: SeoulNewsSettings,
    *,
    transport: httpx.BaseTransport | None = None,
    source_board: str | None = None,
    index: int = 1,
) -> RawSeoulNotice:
    """Request exactly one index, optionally within a verified BLOG_ID."""
    content = _request(settings, index, index, source_board, transport=transport)
    notice = parse_notice(content)
    if source_board is not None and notice.source_board != source_board:
        raise SeoulSourceError("서울시 API 응답의 게시판 번호가 요청과 다릅니다.")
    return notice


def _request(
    settings: SeoulNewsSettings,
    start: int,
    end: int,
    board: str | None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> bytes:
    if type(start) is not int or type(end) is not int or start < 1 or end < start:
        raise SeoulSourceError("서울시 API 순번은 1 이상의 정수여야 합니다.")
    if end - start + 1 > 1000:
        raise SeoulSourceError("서울시 API는 요청당 최대 1000건만 조회할 수 있습니다.")
    if settings.seoul_news_api_key == "sample" and end > 5:
        raise SeoulSourceError("sample 키는 1~5 순번만 조회할 수 있습니다.")
    if board is not None and board not in (
        "21",
        "22",
        "23",
        "24",
        "25",
        "26",
        "27",
        "30",
    ):
        raise SeoulSourceError("지원하지 않는 서울시 게시판 번호입니다.")
    key = quote(settings.seoul_news_api_key, safe="")
    url = f"{API_BASE_URL}/{key}/xml/{SERVICE_NAME}/{start}/{end}/"
    if board is not None:
        url += board + "/"
    timeout = httpx.Timeout(
        settings.http_read_timeout_seconds,
        connect=settings.http_connect_timeout_seconds,
    )
    logger = logging.getLogger("httpx")
    log_filter = _RequestLogFilter(settings)
    logger.addFilter(log_filter)
    try:
        with httpx.Client(timeout=timeout, transport=transport, follow_redirects=False) as client:
            response = client.get(url)
    except httpx.TimeoutException:
        raise SeoulSourceError("서울시 API 요청 시간이 초과되었습니다.", retryable=True) from None
    except httpx.RequestError:
        raise SeoulSourceError("서울시 API에 연결하지 못했습니다.", retryable=True) from None
    finally:
        logger.removeFilter(log_filter)
    if response.status_code != 200:
        raise SeoulSourceError(
            f"서울시 API HTTP 오류: {response.status_code}",
            retryable=response.status_code in (429, 500, 502, 503, 504),
            rate_limited=response.status_code == 429,
        )
    return response.content


def collect_page(
    settings: SeoulNewsSettings,
    *,
    source_board: str,
    start_index: int,
    end_index: int,
    transport: httpx.BaseTransport | None = None,
) -> SeoulApiPage:
    content = _request(settings, start_index, end_index, source_board, transport=transport)
    return parse_page(content, start_index, end_index, source_board)
