"""Read dictionary search and detail APIs without inventing easier expressions."""

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, quote_plus, urlsplit
from xml.etree import ElementTree as ET

import httpx
from pydantic import ValidationError

from pipeline.glossary.models import (
    GlossaryEntry,
    GlossaryLookup,
    NormInfo,
    Provider,
    canonical_headword,
    normalize_query,
)

_HOSTS = {"opendict": "opendict.korean.go.kr", "krdict": "krdict.korean.go.kr"}
_PAGE_SIZE = 100
_MAX_RESULTS = 1_000
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_TIMEOUT_SECONDS = 15.0
_QUOTED = r"[‘“\"']([^‘’“”\"']{1,80})[’”\"']"
_QUOTED_TERMS = rf"{_QUOTED}(?:\s*(?:,|·|또는|및|와|과|이나|나)\s*{_QUOTED})*"
_PURE_NORM = re.compile(
    rf"(?:[‘“\"'](?P<source>[^‘’“”\"']{{1,80}})[’”\"'](?:을|를)\s*)?"
    rf"(?P<terms>{_QUOTED_TERMS})"
    r"(?:으)?로\s*순화(?:하였다|했다|함)?[.]?"
)
_RECOMMENDED_NORM = re.compile(
    rf"[‘“\"'](?P<source>[^‘’“”\"']{{1,80}})[’”\"']\s*대신\s+"
    rf"(?:될 수 있으면\s+)?순화한 용어\s+(?P<terms>{_QUOTED_TERMS})"
    r"(?:을|를)\s+쓰라고 되어 있다[.]?"
)
_CO_USE_NORM = re.compile(
    rf"[‘“\"'](?P<source>[^‘’“”\"']{{1,80}})[’”\"']\s*(?:과|와)\s+"
    rf"(?P<terms>{_QUOTED_TERMS})(?:을|를)\s+함께 쓸 수 있다고 되어 있다[.]?"
)
# Ourmalsam uses role for these bibliographic citations as well as related clauses.
# Recognize the observed sources exactly; an unknown role may constrain the meaning.
_NORM_SOURCE_ROLES = {
    "일본어 투 생활 용어 순화 고시 자료(문화체육부 고시 제1997-19호, 1997년 2월 15일)",
    "행정 용어 순화 편람(1993년 2월 12일)",
    "생활 용어 수정 보완 고시 자료(문화체육부 고시 제1996-13호, 1996년 3월 23일)",
}


class GlossaryAPIError(RuntimeError):
    """A safe error that contains neither credentials nor remote response text."""

    def __init__(self, provider: Provider, code: str) -> None:
        self.provider = provider
        self.code = code
        super().__init__(f"Dictionary lookup failed ({provider}: {code}).")


@dataclass(frozen=True)
class _Meaning:
    sense_id: str
    definition: str
    part_of_speech: str | None = None
    original_language: str | None = None


@dataclass(frozen=True)
class _SearchItem:
    entry_id: str
    headword: str
    source_url: str
    meanings: tuple[_Meaning, ...]


class _CredentialFilter(logging.Filter):
    """Redact httpx/httpcore diagnostics while a credential-bearing request runs."""

    def __init__(self, secret: str) -> None:
        super().__init__()
        self._secrets = tuple({secret, quote(secret, safe=""), quote_plus(secret)})

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in self._secrets:
            message = message.replace(secret, "[REDACTED]")
        record.msg = message
        record.args = ()
        if record.exc_info:
            # Transport exception reprs can include the credential-bearing request URL.
            record.exc_info = None
            record.exc_text = None
        return True


@contextmanager
def _redact_transport_logs(secret: str) -> Iterator[None]:
    names = {
        "httpx",
        "httpcore",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.proxy",
        "httpcore.socks",
    }
    names.update(name for name in logging.Logger.manager.loggerDict if name.startswith("httpcore."))
    loggers = tuple(logging.getLogger(name) for name in names)
    filter_ = _CredentialFilter(secret)
    for logger in loggers:
        logger.addFilter(filter_)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeFilter(filter_)


def _text(parent: ET.Element, path: str, *, required: bool = False) -> str | None:
    element = parent.find(path)
    value = "".join(element.itertext()).strip() if element is not None else ""
    if required and not value:
        raise ValueError("Required dictionary field is missing.")
    return value or None


def _positive_id(parent: ET.Element, path: str) -> str:
    value = _text(parent, path, required=True)
    if value is None or re.fullmatch(r"[0-9]+", value) is None or int(value) < 1:
        raise ValueError("Invalid dictionary identifier.")
    return value


def _ourmalsam_sense_id(parent: ET.Element) -> str:
    value = _positive_id(parent, "sense_no")
    if re.fullmatch(r"[0-9]{3}", value) is None:
        raise ValueError("Invalid Ourmalsam sense number.")
    return value


def _canonical_word(value: str) -> str:
    return canonical_headword(value)


def _easy_terms(headword: str, records: tuple[NormInfo, ...]) -> tuple[str, ...]:
    """Extract quoted targets of complete, explicit official refinement relationships."""
    result: list[str] = []
    for record in records:
        if (
            record.type not in ("순화", "순화 정보", "순화어")
            or not record.description
            or (record.role and record.role.strip() not in _NORM_SOURCE_ROLES)
        ):
            # Related clauses can impose conditions. Keep the complete record instead
            # of presenting a replacement without understanding those clauses.
            continue
        description = record.description.strip()
        match = (
            _PURE_NORM.fullmatch(description)
            or _RECOMMENDED_NORM.fullmatch(description)
            or _CO_USE_NORM.fullmatch(description)
        )
        if match is None:
            continue
        source = match.group("source")
        if source and _canonical_word(source) != _canonical_word(headword):
            continue
        for quoted in re.finditer(_QUOTED, match.group("terms")):
            term = quoted.group(1).strip()
            if term and _canonical_word(term) != _canonical_word(headword) and term not in result:
                result.append(term)
    return tuple(result)


def reparse_refinements(lookup: GlossaryLookup) -> GlossaryLookup:
    """Recompute Ourmalsam targets from saved notes without another dictionary request."""
    entries = tuple(
        entry.model_copy(update={"easy_terms": _easy_terms(entry.headword, entry.norm_info)})
        if entry.provider == "opendict" and entry.norm_info
        else entry
        for entry in lookup.entries
    )
    return lookup.model_copy(update={"entries": entries})


class DictionaryClient:
    """Look up all meanings in one official dictionary, with bounded requests."""

    def __init__(
        self, provider: Provider, api_key: str, *, client: httpx.Client | None = None
    ) -> None:
        if provider not in _HOSTS:
            raise ValueError("Unsupported dictionary provider.")
        if not isinstance(api_key, str) or not api_key.strip():
            raise GlossaryAPIError(provider, "configuration")
        self.provider = provider
        self._api_key = api_key.strip()
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=False)
        self._closed = False

    def close(self) -> None:
        if not self._closed and self._owns_client:
            self._client.close()
        self._closed = True

    def __enter__(self) -> "DictionaryClient":
        if self._closed:
            raise GlossaryAPIError(self.provider, "configuration")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, endpoint: str, params: dict[str, str]) -> ET.Element:
        url = f"https://{_HOSTS[self.provider]}/api/{endpoint}"
        params = {**params, "key": self._api_key}
        failure: str | None = None
        try:
            with (
                _redact_transport_logs(self._api_key),
                self._client.stream(
                    "GET",
                    url,
                    params=params,
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
                    if int(declared_length) < 0:
                        raise ValueError("Invalid response size.")
                    if int(declared_length) > _MAX_RESPONSE_BYTES:
                        raise GlossaryAPIError(self.provider, "response_too_large")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > _MAX_RESPONSE_BYTES:
                        raise GlossaryAPIError(self.provider, "response_too_large")
                    chunks.append(chunk)
                data = b"".join(chunks)
                text = data.decode("utf-8-sig", errors="strict")
                encoding = re.search(r"<\?xml\b[^>]*\bencoding=['\"]([^'\"]+)['\"]", text)
                if (
                    "\x00" in text
                    or self._api_key in text
                    or re.search(r"<!DOCTYPE|<!ENTITY", text, re.I)
                    or (encoding and encoding.group(1).lower() not in ("utf-8", "utf8"))
                ):
                    raise GlossaryAPIError(self.provider, "invalid_response")
                root = ET.fromstring(text)
                # XML character references become plain text only after parsing.
                # Reject decoded credential echoes before any field reaches a result.
                if self._api_key in "".join(root.itertext()) or any(
                    self._api_key in value
                    for element in root.iter()
                    for value in element.attrib.values()
                ):
                    raise GlossaryAPIError(self.provider, "invalid_response")
        except httpx.TimeoutException:
            failure = "timeout"
        except httpx.HTTPError:
            failure = "transport"
        except (ValueError, ET.ParseError):
            failure = "invalid_response"
        # Raise outside the handler, so even __context__ contains no original URL or body.
        if failure is not None:
            raise GlossaryAPIError(self.provider, failure)
        error = root if root.tag == "error" else root.find("error")
        if error is not None:
            provider_code = _text(error, "error_code")
            code = {
                "010": "rate_limit",
                "020": "authentication",
                "021": "authentication",
            }.get(provider_code, "api")
            raise GlossaryAPIError(self.provider, code)
        if root.tag != "channel":
            raise GlossaryAPIError(self.provider, "invalid_response")
        return root

    def lookup(self, query: str) -> tuple[GlossaryEntry, ...]:
        if self._closed:
            raise GlossaryAPIError(self.provider, "configuration")
        query = normalize_query(query)
        try:
            found = self._search(query)
            result: list[GlossaryEntry] = []
            for item in found:
                result.extend(self._detail(item))
            identities = [entry.identity for entry in result]
            if len(set(identities)) != len(identities):
                raise ValueError("Duplicate dictionary meaning.")
            return tuple(result)
        except (ValueError, ValidationError):
            pass
        raise GlossaryAPIError(self.provider, "invalid_response")

    def lookup_definitions(self, query: str) -> tuple[GlossaryEntry, ...]:
        """Read all Ourmalsam meanings directly from validated search responses.

        Each search page is one request. Definitions, identifiers and source
        links are already supplied by that API, so this operation needs no
        detail requests. Missing optional metadata remains empty.
        """
        if self.provider != "opendict":
            raise ValueError("Definitions-only lookups require Ourmalsam.")
        if self._closed:
            raise GlossaryAPIError(self.provider, "configuration")
        query = normalize_query(query)
        try:
            found = self._search(query)
            result = tuple(
                GlossaryEntry(
                    provider="opendict",
                    entry_id=item.entry_id,
                    sense_id=meaning.sense_id,
                    headword=item.headword,
                    definition=meaning.definition,
                    part_of_speech=meaning.part_of_speech,
                    original_language=meaning.original_language,
                    source_url=item.source_url,
                )
                for item in found
                for meaning in item.meanings
            )
            identities = [entry.identity for entry in result]
            if len(set(identities)) != len(identities):
                raise ValueError("Duplicate dictionary meaning.")
            return result
        except (ValueError, ValidationError):
            pass
        raise GlossaryAPIError(self.provider, "invalid_response")

    def _search(self, query: str) -> tuple[_SearchItem, ...]:
        results: list[_SearchItem] = []
        total: int | None = None
        start = 1
        while total is None or len(results) < total:
            params = {
                "q": query,
                "start": str(start),
                "num": str(_PAGE_SIZE),
                "sort": "dict",
                "part": "word",
                "advanced": "y",
                "target": "1",
                "method": "exact",
            }
            if self.provider == "opendict":
                params["req_type"] = "xml"
            root = self._request("search", params)
            page_total = int(_text(root, "total", required=True) or "")
            page_start = int(_text(root, "start", required=True) or "")
            page_num = int(_text(root, "num", required=True) or "")
            if page_total < 0 or page_start != start or not 0 <= page_num <= _PAGE_SIZE:
                raise ValueError("Invalid pagination metadata.")
            if page_total > _MAX_RESULTS:
                raise GlossaryAPIError(self.provider, "too_many_results")
            if total is not None and page_total != total:
                raise ValueError("Dictionary changed during pagination.")
            total = page_total
            items = root.findall("item")
            if len(items) > page_num or len(results) + len(items) > total:
                raise ValueError("Invalid pagination result count.")
            if not items and len(results) < total:
                raise ValueError("Missing dictionary search page.")
            for element in items:
                results.append(self._search_item(element, query))
            start += len(items)
        identities = [(item.entry_id, tuple(m.sense_id for m in item.meanings)) for item in results]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate search result.")
        return tuple(results)

    def _search_item(self, item: ET.Element, query: str) -> _SearchItem:
        headword = _text(item, "word", required=True) or ""
        if _canonical_word(headword) != _canonical_word(query):
            raise ValueError("Exact dictionary search returned a different word.")
        senses = item.findall("sense")
        if not senses:
            raise ValueError("Missing dictionary meanings.")
        if self.provider == "krdict":
            entry_id = _positive_id(item, "target_code")
            source_url = _text(item, "link", required=True) or ""
            meanings = tuple(
                _Meaning(
                    _positive_id(sense, "sense_order"),
                    _text(sense, "definition", required=True) or "",
                )
                for sense in senses
            )
        else:
            if len(senses) != 1:
                raise ValueError("Unexpected Ourmalsam search meaning structure.")
            sense = senses[0]
            entry_id = _positive_id(sense, "target_code")
            source_url = _text(sense, "link", required=True) or ""
            meanings = (
                _Meaning(
                    _ourmalsam_sense_id(sense),
                    _text(sense, "definition", required=True) or "",
                    _text(sense, "pos"),
                    _text(sense, "origin"),
                ),
            )
        # Validate source metadata before issuing another authenticated request.
        GlossaryEntry(
            provider=self.provider,
            entry_id=entry_id,
            sense_id=meanings[0].sense_id,
            headword=headword,
            definition=meanings[0].definition,
            source_url=source_url,
        )
        source_params = {
            key.lower(): values for key, values in parse_qs(urlsplit(source_url).query).items()
        }
        id_param = "sense_no" if self.provider == "opendict" else "parawordno"
        source_ids = source_params.get(id_param, [])
        if (
            len(source_ids) != 1
            or re.fullmatch(r"[0-9]+", source_ids[0]) is None
            or int(source_ids[0]) != int(entry_id)
        ):
            raise ValueError("Source link does not match dictionary entry.")
        return _SearchItem(entry_id, headword, source_url, meanings)

    def _detail(self, item: _SearchItem) -> tuple[GlossaryEntry, ...]:
        params = {"q": item.entry_id, "method": "target_code"}
        if self.provider == "opendict":
            params["req_type"] = "xml"
        root = self._request("view", params)
        total = _text(root, "total", required=self.provider == "opendict")
        if total is not None and int(total) != 1:
            raise ValueError("Unexpected detail result count.")
        items = root.findall("item")
        if len(items) != 1 or _positive_id(items[0], "target_code") != item.entry_id:
            raise ValueError("Detail response does not match requested identifier.")
        words = items[0].findall("word_info")
        if self.provider == "opendict":
            # The official field table uses snake_case; its XML example uses camelCase.
            words += items[0].findall("wordInfo")
        if len(words) != 1:
            raise ValueError("Missing detail word information.")
        word_info = words[0]
        headword = _text(word_info, "word", required=True) or ""
        if headword != item.headword:
            raise ValueError("Detail response changed the headword.")
        senses = (
            items[0].findall("sense_info") + items[0].findall("senseInfo")
            if self.provider == "opendict"
            else word_info.findall("sense_info")
        )
        if not senses:
            raise ValueError("Missing detail meanings.")
        for sense in senses:
            _text(sense, "definition", required=True)
        originals = tuple(
            _text(element, "original_language", required=True) or ""
            for element in word_info.findall("original_language_info")
        )
        original_language = ", ".join(originals) or None
        entries: list[GlossaryEntry] = []
        for meaning in item.meanings:
            norm_info: tuple[NormInfo, ...] = ()
            definition = meaning.definition
            pos = _text(word_info, "pos")
            if self.provider == "opendict":
                if len(senses) != 1:
                    raise ValueError("Unexpected Ourmalsam detail meaning structure.")
                sense = senses[0]
                detailed_id = _ourmalsam_sense_id(sense)
                if detailed_id != meaning.sense_id:
                    raise ValueError("Detail response changed the sense identifier.")
                definition = _text(sense, "definition", required=True) or ""
                pos = _text(sense, "pos") or pos
                norm_info = tuple(
                    NormInfo(
                        type=_text(norm, "type", required=True) or "",
                        role=_text(norm, "role"),
                        description=_text(norm, "desc"),
                    )
                    for norm in sense.findall("norm_info")
                )
            # krdict detail documents no sense_order. Keep the search's IDs/definitions;
            # never attach a detail meaning by position or overwrite its meaning text.
            entries.append(
                GlossaryEntry(
                    provider=self.provider,
                    entry_id=item.entry_id,
                    sense_id=meaning.sense_id,
                    headword=headword,
                    definition=definition,
                    part_of_speech=pos,
                    original_language=original_language,
                    norm_info=norm_info,
                    easy_terms=_easy_terms(headword, norm_info),
                    source_url=item.source_url,
                )
            )
        return tuple(entries)


class DictionaryDefinitionClient(DictionaryClient):
    """Expose search-only Ourmalsam definitions through the shared lookup interface."""

    def __init__(
        self, provider: Provider, api_key: str, *, client: httpx.Client | None = None
    ) -> None:
        if provider != "opendict":
            raise ValueError("Definitions-only lookups require Ourmalsam.")
        super().__init__(provider, api_key, client=client)

    def lookup(self, query: str) -> tuple[GlossaryEntry, ...]:
        return self.lookup_definitions(query)
