"""Replay recorded HTTP responses for e2e cases; any other request fails the case."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

API_KEY = "test-only-key"
KEY_PLACEHOLDER = "<key>"
SEOUL_OPENAPI = "http://openapi.seoul.go.kr:8088"
NOWON_ORIGIN = "https://www.nowon.kr"

CONTENT_TYPES = {
    ".xml": "text/xml; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".hwp": "application/x-hwp",
    ".hwpx": "application/haansofthwpx",
    ".txt": "text/plain; charset=utf-8",
}


class UnexpectedRequest(AssertionError):
    """A request that the case did not register."""


class CaseDefinitionError(ValueError):
    """A case.json entry that the harness cannot interpret."""


def redact(url: str) -> str:
    return url.replace(API_KEY, KEY_PLACEHOLDER)


def _route_url(spec: dict[str, Any]) -> tuple[str, str]:
    """Return (expected URL, short label) for one http entry of case.json."""
    route = spec.get("route")
    try:
        if route == "nowon_api":
            start, end = int(spec["start"]), int(spec["end"])
            url = f"{SEOUL_OPENAPI}/{API_KEY}/xml/NowonNewsNoticeList/{start}/{end}/"
            return url, f"nowon_api {start}-{end}"
        if route == "nowon_page":
            post_sn = str(spec["post_sn"])
            url = (
                f"{NOWON_ORIGIN}/www/user/bbs/BD_selectBbs.do"
                f"?q_bbsCode=1001&q_bbscttSn={post_sn}"
            )
            return url, f"nowon_page {post_sn}"
        if route == "wolgye1_list":
            page = int(spec.get("page", 1))
            url = f"{NOWON_ORIGIN}/dong/user/bbs/BD_selectBbsList.do?q_bbsCode=1042&q_deptCode=1047"
            if page != 1:
                url += f"&q_currPage={page}"
            return url, f"wolgye1_list {page}"
        if route == "wolgye1_detail":
            post_sn = str(spec["post_sn"])
            url = (
                f"{NOWON_ORIGIN}/dong/user/bbs/BD_selectBbs.do"
                f"?q_bbsCode=1042&q_bbscttSn={post_sn}&q_deptCode=1047"
            )
            return url, f"wolgye1_detail {post_sn}"
        if route == "seoul_api":
            start, end = int(spec["start"]), int(spec["end"])
            board = spec.get("board")
            url = f"{SEOUL_OPENAPI}/{API_KEY}/xml/SeoulNewsList/{start}/{end}/"
            if board is not None:
                url += f"{board}/"
            return url, f"seoul_api {board or 'all'} {start}-{end}"
        if route == "file":
            url = str(spec["url"])
            return url, f"file {url}"
    except (KeyError, TypeError, ValueError):
        raise CaseDefinitionError(f"http route {route!r} is missing a required field") from None
    raise CaseDefinitionError(f"unknown http route {route!r}")


def _canonical(url: str) -> tuple[object, ...]:
    parts = urlsplit(url)
    port = parts.port or {"http": 80, "https": 443}.get(parts.scheme)
    query = tuple(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return parts.scheme, (parts.hostname or "").lower(), port, parts.path, query


@dataclass
class Route:
    label: str
    method: str
    url: str
    status: int
    headers: dict[str, str]
    body: bytes
    calls: int = 0

    @classmethod
    def from_spec(cls, spec: dict[str, Any], case_dir: Path) -> "Route":
        if not isinstance(spec, dict):
            raise CaseDefinitionError("each http entry must be an object")
        url, label = _route_url(spec)
        headers = {str(k).lower(): str(v) for k, v in spec.get("headers", {}).items()}
        if "body" in spec:
            path = (case_dir / spec["body"]).resolve()
            if case_dir.resolve() not in path.parents:
                raise CaseDefinitionError(f"http body must stay inside the case: {spec['body']}")
            if not path.is_file():
                raise CaseDefinitionError(f"http body file not found: {spec['body']}")
            body = path.read_bytes()
            headers.setdefault(
                "content-type", CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
            )
        else:
            body = str(spec.get("text", "")).encode("utf-8")
            headers.setdefault("content-type", "text/plain; charset=utf-8")
        return cls(
            label=label,
            method=str(spec.get("method", "GET")).upper(),
            url=url,
            status=int(spec.get("status", 200)),
            headers=headers,
            body=body,
        )


@dataclass
class HttpReplay:
    routes: list[Route]
    calls: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    def respond(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        key = _canonical(url)
        for route in self.routes:
            if route.method == request.method and _canonical(route.url) == key:
                route.calls += 1
                self.calls.append(route.label)
                return httpx.Response(
                    route.status, headers=route.headers, content=route.body, request=request
                )
        message = f"Unexpected external URL: {request.method} {redact(url)}"
        self.unexpected.append(message)
        # Raised inside httpx; pipeline code may convert it into its own error, so the
        # harness also fails the step from self.unexpected afterwards.
        raise UnexpectedRequest(message)

    def unused(self) -> list[str]:
        return [route.label for route in self.routes if route.calls == 0]


def install(monkeypatch: pytest.MonkeyPatch, replay: HttpReplay) -> None:
    """Route every sync and async httpx default transport through the replay."""

    def handle_request(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        return replay.respond(request)

    async def handle_async_request(
        self: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        return replay.respond(request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle_async_request)
