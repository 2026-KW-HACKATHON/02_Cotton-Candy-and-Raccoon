"""Read explicitly related, licensed polished terms from the official OnTerm API."""

import hashlib
import html
import json
import re
from typing import Any
from urllib.parse import unquote

import httpx
from bs4 import BeautifulSoup
from pydantic import ValidationError

from pipeline.glossary.client import GlossaryAPIError, _canonical_word, _redact_transport_logs
from pipeline.glossary.models import GlossaryEntry, NormInfo, normalize_query

_URL = "https://kli.korean.go.kr/term/api/search.do"
_SOURCE_URL = "https://kli.korean.go.kr/term/"
_LICENSE_URL = "https://www.kogl.or.kr/info/licenseType1.do"
_TIMEOUT_SECONDS = 15.0
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_RESULTS = 3_000
_PAGE_SIZE = 100
_NO_RESULTS = "검색 결과가 없습니다."
_RELATION = re.compile(r"\(([^(),]+),\s*([^(),]+)\)")


def _plain_text(value: object, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError("Required terminology field is missing.")
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid terminology field type.")
    value = html.unescape(value)
    if "<" in value:
        value = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    value = html.unescape(value)
    value = " ".join(value.split())
    if not value:
        if required:
            raise ValueError("Required terminology field is empty.")
        return None
    if any(ord(char) < 32 for char in value):
        raise ValueError("Invalid terminology field text.")
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("Invalid terminology response number.")
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError("Invalid terminology response number.")
    return int(value)


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field.")
        result[key] = value
    return result


def _has_credential(value: object, secret: str) -> bool:
    if isinstance(value, str):
        decoded = html.unescape(unquote(value))
        if secret in value or secret in decoded:
            return True
        return "<" in decoded and secret in BeautifulSoup(decoded, "html.parser").get_text()
    if isinstance(value, dict):
        return any(
            _has_credential(key, secret) or _has_credential(item, secret)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_credential(item, secret) for item in value)
    return False


def _polished_terms(relation: str) -> tuple[str, ...]:
    """Use only complete '(다듬은 말, target)' relations, preserving other relation types."""
    result: list[str] = []
    position = 0
    matches = list(_RELATION.finditer(relation))
    if not matches:
        return ()
    for match in matches:
        if relation[position : match.start()].strip(" ,;·"):
            return ()
        kind = match.group(1).strip()
        term = match.group(2).strip()
        if kind == "다듬은 말":
            term = normalize_query(term)
            if term not in result:
                result.append(term)
        position = match.end()
    if relation[position:].strip(" ,;·"):
        return ()
    return tuple(result)


class OnTermClient:
    """Return all exact, explicit KOGL type 1 polished-word records for a query."""

    provider = "onterm"

    def __init__(self, api_key: str, *, client: httpx.Client | None = None) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise GlossaryAPIError(self.provider, "configuration")
        self._api_key = api_key.strip()
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=False)
        self._closed = False
        self._rate_limited = False

    def close(self) -> None:
        if not self._closed and self._owns_client:
            self._client.close()
        self._closed = True

    def __enter__(self) -> "OnTermClient":
        if self._closed:
            raise GlossaryAPIError(self.provider, "configuration")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, query: str, start: int) -> dict[str, Any]:
        failure: str | None = None
        try:
            with (
                _redact_transport_logs(self._api_key),
                self._client.stream(
                    "GET",
                    _URL,
                    params={
                        "key": self._api_key,
                        "apiSearchWord": query,
                        "start": str(start),
                        "num": str(_PAGE_SIZE),
                        "sort": "wt",
                    },
                    timeout=_TIMEOUT_SECONDS,
                    follow_redirects=False,
                ) as response,
            ):
                if response.status_code in (401, 403):
                    raise GlossaryAPIError(self.provider, "authentication")
                if response.status_code == 429:
                    raise GlossaryAPIError(self.provider, "rate_limit")
                if response.status_code != 200:
                    raise GlossaryAPIError(self.provider, "http")
                declared_length = response.headers.get("content-length")
                if declared_length:
                    length = int(declared_length)
                    if length < 0:
                        raise ValueError("Invalid response size.")
                    if length > _MAX_RESPONSE_BYTES:
                        raise GlossaryAPIError(self.provider, "response_too_large")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > _MAX_RESPONSE_BYTES:
                        raise GlossaryAPIError(self.provider, "response_too_large")
                    chunks.append(chunk)
                text = b"".join(chunks).decode("utf-8-sig", errors="strict")
                if self._api_key in text:
                    raise GlossaryAPIError(self.provider, "invalid_response")
                parsed = json.loads(text, object_pairs_hook=_json_object)
                if not isinstance(parsed, dict) or _has_credential(parsed, self._api_key):
                    raise GlossaryAPIError(self.provider, "invalid_response")
                channel = parsed.get("channel")
                if not isinstance(channel, dict):
                    raise ValueError("Missing terminology response channel.")
        except httpx.TimeoutException:
            failure = "timeout"
        except httpx.HTTPError:
            failure = "transport"
        except (ValueError, RecursionError):
            failure = "invalid_response"
        if failure is not None:
            raise GlossaryAPIError(self.provider, failure)
        return channel

    def _check_code(self, code: object) -> None:
        if (isinstance(code, str) and code == "1") or (type(code) is int and code == 1):
            return
        failure = {
            "010": "rate_limit",
            "020": "authentication",
            "021": "authentication",
            "022": "rate_limit",
        }.get(str(code).zfill(3) if type(code) is int else str(code), "api")
        raise GlossaryAPIError(self.provider, failure)

    def lookup(self, query: str) -> tuple[GlossaryEntry, ...]:
        if self._closed:
            raise GlossaryAPIError(self.provider, "configuration")
        if self._rate_limited:
            raise GlossaryAPIError(self.provider, "rate_limit")
        query = normalize_query(query)
        try:
            entries = self._search(query)
            # Identical content may appear more than once. Distinct source/meaning records
            # use different content hashes and must remain separate.
            unique = {entry.identity: entry for entry in entries}
            return tuple(unique.values())
        except GlossaryAPIError as error:
            # Keep a shared batch client from repeating requests against a known
            # limit. Recreate the client for a later attempt; reset times are not
            # documented, so do not guess a sleep duration or cache this failure.
            if error.code == "rate_limit":
                self._rate_limited = True
            raise
        except (ValueError, ValidationError, RecursionError):
            pass
        raise GlossaryAPIError(self.provider, "invalid_response")

    def _search(self, query: str) -> tuple[GlossaryEntry, ...]:
        """Filter complete pages, while checking pagination against raw record counts.

        Exact matches may occur on any page, including after unrelated results.
        Only return after the full bounded search completes; an incomplete or
        oversized search must never become a successful partial or empty lookup.
        """
        entries: list[GlossaryEntry] = []
        prior_page_records: set[str] = set()
        total: int | None = None
        raw_count = 0
        start = 1
        while total is None or raw_count < total:
            channel = self._request(query, start)
            if "returnCode" in channel:
                self._check_code(channel["returnCode"])
            returned = channel.get("return_object")
            if isinstance(returned, str):
                self._check_code(channel.get("returnCode"))
                if returned != _NO_RESULTS or raw_count:
                    raise ValueError("Unexpected terminology empty-result response.")
                if "total" in channel and _integer(channel["total"]) != 0:
                    raise ValueError("Inconsistent terminology empty-result count.")
                return ()
            if not isinstance(returned, list) or not returned:
                raise ValueError("Missing terminology result groups.")
            page_total = _integer(channel.get("total"))
            page_start = _integer(channel.get("start"))
            page_num = _integer(channel.get("num"))
            if page_total < 0 or page_start != start or page_num != _PAGE_SIZE:
                raise ValueError("Invalid terminology pagination metadata.")
            if page_total > _MAX_RESULTS:
                raise GlossaryAPIError(self.provider, "too_many_results")
            if total is not None and page_total != total:
                raise ValueError("Terminology results changed during pagination.")
            total = page_total
            page: list[dict[str, Any]] = []
            for group in returned:
                if not isinstance(group, dict):
                    raise ValueError("Invalid terminology result group.")
                self._check_code(group.get("returnCode"))
                records = group.get("resultlist")
                if not isinstance(records, list) or any(
                    not isinstance(item, dict) for item in records
                ):
                    raise ValueError("Invalid terminology result records.")
                page.extend(records)
            expected_count = min(page_num, total - raw_count)
            if len(page) != expected_count:
                raise ValueError("Inconsistent terminology page count.")
            if not page and raw_count < total:
                raise ValueError("Missing terminology result page.")
            page_keys = {
                hashlib.sha256(
                    json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest()
                for record in page
            }
            if prior_page_records.intersection(page_keys):
                raise ValueError("Repeated terminology records across search pages.")
            prior_page_records.update(page_keys)
            entries.extend(
                entry for record in page if (entry := self._entry(record, query)) is not None
            )
            raw_count += len(page)
            # OnTerm echoes the page number in start, not the first record offset.
            start += 1
        return tuple(entries)

    def _entry(self, record: dict[str, Any], query: str) -> GlossaryEntry | None:
        headword = _plain_text(record.get("word"), required=True) or ""
        if _canonical_word(headword) != _canonical_word(query):
            return None
        license_type = record.get("kr_gvrn_lcns_ty")
        if not ((type(license_type) is int and license_type == 1) or license_type == "1"):
            return None
        relation = _plain_text(record.get("relate_word"))
        if relation is None:
            return None
        easy_terms = tuple(
            term
            for term in _polished_terms(relation)
            if _canonical_word(term) != _canonical_word(headword)
        )
        if not easy_terms:
            return None
        definition = _plain_text(record.get("definition"))
        institution = _plain_text(record.get("source"), required=True) or ""
        glossary = _plain_text(record.get("glossary"), required=True) or ""
        original_language = _plain_text(record.get("origin"))
        categories = tuple(
            text
            for field in ("category_main", "category_sub")
            if (text := _plain_text(record.get(field))) is not None
        )
        field = " > ".join(categories) or None
        norms = [NormInfo(type="다듬은 말", role=field, description=relation)]
        if field:
            norms.append(NormInfo(type="전문 분야", description=field))
        example = _plain_text(record.get("use_ex"))
        if example:
            norms.append(NormInfo(type="용례", description=example))
        content = {
            "headword": headword,
            "definition": definition,
            "source_institution": institution,
            "source_glossary": glossary,
            "original_language": original_language,
            "easy_terms": easy_terms,
            "norm_info": [norm.model_dump() for norm in norms],
        }
        if _has_credential(content, self._api_key):
            raise ValueError("Terminology data contains credential echo.")
        digest = hashlib.sha256(
            json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        return GlossaryEntry(
            provider="onterm",
            entry_id=f"sha256:{digest}",
            entry_id_kind="content_hash",
            sense_id="content",
            headword=headword,
            definition=definition,
            original_language=original_language,
            easy_terms=easy_terms,
            norm_info=tuple(norms),
            source_institution=institution,
            source_glossary=glossary,
            source_url=_SOURCE_URL,
            license="KOGL 1",
            license_url=_LICENSE_URL,
        )
