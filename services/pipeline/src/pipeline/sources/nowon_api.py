"""Read one notice through the authenticated Nowon API (not RSS).

Use XML: the live JSON API rounded a 17-digit ID into a floating-point number.
XML preserves ID text. No extra HTML unescape is applied to DESCRIPTION.
"""

import logging
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from pipeline.config import NowonSettings
from pipeline.models import RawNotice

API_BASE_URL = "http://openapi.seoul.go.kr:8088"
SERVICE_NAME = "NowonNewsNoticeList"


class NowonSourceError(ValueError):
    """Safe user-facing error, with no raw response or request URL attached."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


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
    assert value is not None  # _field rejects an empty required field.
    return value


def parse_notice(content: bytes) -> RawNotice:
    """Parse one UTF-8 API response; keep IDs, HTML and dates as source text."""
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
        raise NowonSourceError("API 결과 코드가 없습니다.")
    code = _required_field(results[0], "CODE").strip()
    if code == "INFO-100":
        raise NowonSourceError("API 인증 실패: NOWON_NOTICE_API_KEY를 확인하세요.")
    if code == "INFO-200":
        raise NowonSourceError("조회된 공지가 없습니다.")
    if code in ("ERROR-500", "ERROR-600"):
        raise NowonSourceError("API 서버가 일시적인 오류를 반환했습니다.", retryable=True)
    if code != "INFO-000":
        # Do not echo an arbitrary CODE/MESSAGE that might include credentials.
        raise NowonSourceError("API가 요청을 거부했습니다. 요청 조건을 확인하세요.")
    rows = root.findall("row")
    if not rows:
        raise NowonSourceError("조회된 공지가 없습니다.")
    if len(rows) != 1:
        raise NowonSourceError("한 건 요청에 여러 공지가 반환되었습니다.")
    row = rows[0]
    return RawNotice(
        category="nowon", dong_group=None, is_pinned=False,
        post_sn=_required_field(row, "ID"),
        title=_required_field(row, "TITLE"),
        department=_field(row, "DEPARTMENT", optional=True),
        registered_on=_required_field(row, "PUBDATE"),
        url=_required_field(row, "LINK"),
        body_html=_field(row, "DESCRIPTION", optional=True),
        license_type="KOGL-4",
    )


def collect_one(
    settings: NowonSettings, *, transport: httpx.BaseTransport | None = None,
) -> RawNotice:
    """Fetch indices 1..1 once. Retryable failures are reported, not retried here."""
    key = quote(settings.nowon_notice_api_key, safe="")
    url = f"{API_BASE_URL}/{key}/xml/{SERVICE_NAME}/1/1/"
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
        )
    return parse_notice(response.content)
