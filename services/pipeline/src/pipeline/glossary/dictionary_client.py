"""Bounded standard-dictionary search followed by complete entry/sense retrieval."""

import json
import time
import unicodedata
import xml.etree.ElementTree as ET
from contextlib import nullcontext
from typing import Any

import httpx
from pydantic import SecretStr

from pipeline.glossary.dictionary import (
    DictionaryEntry,
    DictionaryError,
    DictionaryQuery,
    DictionaryResult,
    DictionarySense,
)
from pipeline.transform.gemini_logging import private_gemini_logging

BASE_URL = "https://stdict.korean.go.kr/api/"
PAGE_SIZE = 100
MAX_ENTRIES = 1000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_LOOKUP_BYTES = 16 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_LOOKUP_SECONDS = 120.0
RETRY_DELAY_SECONDS = 0.25


def _invalid() -> DictionaryError:
    return DictionaryError("dictionary_invalid_response")


def _mapping(value: Any) -> dict:
    if not isinstance(value, dict):
        raise _invalid()
    return value


def _sequence(value: Any) -> list[dict]:
    # The provider uses an object for a singleton in some view responses.
    if isinstance(value, dict):
        return [value]
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise _invalid()
    return value


def _number(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise _invalid()
    if isinstance(value, str) and (not value.isascii() or not value.isdigit()):
        raise _invalid()
    number = int(value)
    if number < 0:
        raise _invalid()
    return number


def _code(value: Any) -> str:
    return str(_number(value))


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid()
    return value.strip()


def _spelling(value: str) -> str:
    # '-' marks morpheme boundaries and '^' marks optional spacing in headwords.
    return unicodedata.normalize("NFC", value).translate(str.maketrans("", "", "-^ "))


def _provider_error(payload: dict) -> None:
    if "error" not in payload:
        return
    code = _number(_mapping(payload["error"]).get("error_code"))
    if code in (20, 21):
        raise DictionaryError("dictionary_authentication_failed")
    if code == 0:
        raise DictionaryError("dictionary_upstream_error", retryable=True)
    raise DictionaryError("dictionary_invalid_request")


def _verified_empty_xml(body: bytearray) -> dict:
    """JSON has no channel for zero matches; verify the explicit XML total instead."""
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise _invalid()
    root = ET.fromstring(body)
    if root.tag == "error":
        _provider_error({"error": {"error_code": root.findtext("error_code")}})
    if root.tag != "channel" or root.findall("item"):
        raise _invalid()
    values = {}
    for field in ("total", "start", "num"):
        nodes = root.findall(field)
        if len(nodes) != 1 or len(nodes[0]):
            raise _invalid()
        values[field] = _number(nodes[0].text)
    if values["total"] != 0:
        raise _invalid()
    return values


class DictionaryClient:
    """Keep all homonyms and senses; never choose a context-dependent meaning.

    An injected HTTP client remains owned by its caller. HTTP diagnostics are muted
    on this request's thread using the existing shared SDK/HTTP privacy guard:
    this provider requires its secret in a query parameter.
    """

    def __init__(
        self, api_key: str, *, client: httpx.Client | None = None,
        timeout_seconds: float = MAX_LOOKUP_SECONDS,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise DictionaryError("dictionary_missing_api_key")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (float, int))
            or not 0 < timeout_seconds <= MAX_LOOKUP_SECONDS
        ):
            raise DictionaryError("dictionary_invalid_request")
        self._key = SecretStr(api_key.strip())
        self._client = client
        self._timeout_seconds = timeout_seconds

    def lookup(self, query: DictionaryQuery) -> DictionaryResult:
        if not isinstance(query, DictionaryQuery):
            raise DictionaryError("invalid_dictionary_query")
        deadline = time.monotonic() + self._timeout_seconds
        consumed = [0]
        try:
            # Reuse a caller's connection pool, or close the client we create.
            context = nullcontext(self._client) if self._client else httpx.Client(
                follow_redirects=False, trust_env=False,
            )
            with private_gemini_logging(), context as client:
                entries = self._search(client, query, deadline, consumed)
                result = DictionaryResult(
                    query_word=query.word, status="found" if entries else "not_found",
                    entries=tuple(entries),
                )
                self._remaining(deadline)
                return result
        except DictionaryError:
            raise
        except Exception:
            # Never propagate request URLs, response bodies, event-hook errors or keys.
            raise _invalid() from None

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DictionaryError("dictionary_timeout", retryable=True)
        return remaining

    def _request(
        self, client: httpx.Client, endpoint: str, params: dict,
        deadline: float, consumed: list[int],
    ) -> dict:
        for attempt in range(2):
            try:
                timeout = min(REQUEST_TIMEOUT_SECONDS, self._remaining(deadline))
                with client.stream(
                    "GET", BASE_URL + endpoint,
                    params={"key": self._key.get_secret_value(), "req_type": "json", **params},
                    follow_redirects=False, timeout=timeout,
                ) as response:
                    if response.status_code in (401, 403):
                        raise DictionaryError("dictionary_authentication_failed")
                    if response.status_code == 429:
                        raise DictionaryError("dictionary_rate_limited", retryable=True)
                    if 500 <= response.status_code <= 599:
                        raise DictionaryError("dictionary_upstream_error", retryable=True)
                    if 300 <= response.status_code <= 399:
                        raise _invalid()
                    if response.status_code != 200:
                        raise DictionaryError("dictionary_invalid_request")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        self._remaining(deadline)
                        consumed[0] += len(chunk)
                        if (
                            len(body) + len(chunk) > MAX_RESPONSE_BYTES
                            or consumed[0] > MAX_LOOKUP_BYTES
                        ):
                            raise DictionaryError("dictionary_result_limit")
                        body.extend(chunk)
                    self._remaining(deadline)
                    if params.get("req_type") == "xml":
                        return _verified_empty_xml(body)
                    payload = _mapping(json.loads(body))
                    _provider_error(payload)
                    if endpoint == "search.do" and payload == {}:
                        return {}  # The caller must verify this with explicit XML total=0.
                    return _mapping(payload.get("channel"))
            except httpx.TimeoutException:
                error = DictionaryError("dictionary_timeout", retryable=True)
            except httpx.RequestError:
                error = DictionaryError("dictionary_connection_error", retryable=True)
            except DictionaryError as exc:
                error = exc
            except Exception:
                raise _invalid() from None
            if not error.retryable or error.code == "dictionary_rate_limited" or attempt == 1:
                raise error from None
            if self._remaining(deadline) <= RETRY_DELAY_SECONDS:
                raise DictionaryError("dictionary_timeout", retryable=True) from None
            time.sleep(RETRY_DELAY_SECONDS)
        raise AssertionError("unreachable")

    def _search(
        self, client: httpx.Client, query: DictionaryQuery,
        deadline: float, consumed: list[int],
    ) -> list[DictionaryEntry]:
        found: list[dict] = []
        total: int | None = None
        seen: set[str] = set()
        start = 1
        while total is None or start <= total:
            params = {
                "q": query.word, "advanced": "y", "target": 1,
                "method": "exact", "pos": 0, "start": start, "num": PAGE_SIZE,
            }
            channel = self._request(client, "search.do", params, deadline, consumed)
            if channel == {}:
                channel = self._request(
                    client, "search.do", {**params, "req_type": "xml"}, deadline, consumed,
                )
            page_total = _number(channel.get("total"))
            if page_total > MAX_ENTRIES:
                raise DictionaryError("dictionary_result_limit")
            if total is not None and total != page_total:
                raise _invalid()
            total = page_total
            if _number(channel.get("start")) != start or _number(channel.get("num")) != PAGE_SIZE:
                raise _invalid()
            items = _sequence(channel.get("item", []))
            if len(items) != min(PAGE_SIZE, total - start + 1):
                raise _invalid()
            for item in items:
                target = _code(item.get("target_code"))
                if target in seen or _spelling(_text(item.get("word"))) != _spelling(query.word):
                    raise _invalid()
                seen.add(target)
                found.append(item)
            start += PAGE_SIZE
        return [self._entry(client, item, deadline, consumed) for item in found]

    def _entry(
        self, client: httpx.Client, search_item: dict,
        deadline: float, consumed: list[int],
    ) -> DictionaryEntry:
        target = _code(search_item["target_code"])
        channel = self._request(client, "view.do", {
            "q": target, "method": "target_code", "type_search": "view",
        }, deadline, consumed)
        # type_search=view is required by the live API (omitting it returns an
        # empty HTTP 200 body), though it is absent from the parameter table.
        if _number(channel.get("total")) != 1:
            raise _invalid()
        items = _sequence(channel.get("item"))
        if len(items) != 1 or _code(items[0].get("target_code")) != target:
            raise _invalid()
        info = _mapping(items[0].get("word_info"))
        headword = _text(info.get("word"))
        if _spelling(headword) != _spelling(_text(search_item["word"])):
            raise _invalid()
        senses: list[DictionarySense] = []
        for pos in _sequence(info.get("pos_info")):
            groups = _sequence(pos.get("comm_pattern_info"))
            if not groups:
                raise _invalid()
            for group in groups:
                meanings = _sequence(group.get("sense_info"))
                if not meanings:
                    raise _invalid()
                for meaning in meanings:
                    senses.append(DictionarySense(
                        sense_code=_code(meaning.get("sense_code")),
                        pos_code=_code(pos.get("pos_code")),
                        part_of_speech=_text(pos.get("pos")),
                        definition=_text(meaning.get("definition")),
                    ))
                    if len(senses) > 1000:
                        raise DictionaryError("dictionary_result_limit")
        sup_no = search_item.get("sup_no")
        return DictionaryEntry(
            target_code=target, headword=headword,
            homonym_number=_code(sup_no) if sup_no not in (None, "") else None,
            source_url=f"https://stdict.korean.go.kr/search/searchView.do?word_no={target}",
            senses=tuple(senses),
        )
