"""Read every meaning from Standard Korean Dictionary and Ourmalsam APIs."""

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, quote_plus, urlsplit
from xml.etree import ElementTree as ET

import httpx
from pydantic import ValidationError

from pipeline.glossary.models import (
    GlossaryEntry,
    Provider,
    canonical_headword,
    normalize_query,
)

_HOSTS = {"stdict": "stdict.korean.go.kr", "opendict": "opendict.korean.go.kr"}
_PAGE_SIZE = 100
_MAX_RESULTS = 1_000
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_TIMEOUT_SECONDS = 15.0


class GlossaryAPIError(RuntimeError):
    """A safe error that contains neither credentials nor remote response text."""

    def __init__(self, provider: Provider, code: str) -> None:
        self.provider = provider
        self.code = code
        super().__init__(f"Dictionary lookup failed ({provider}: {code}).")


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


@dataclass(frozen=True)
class _StandardSearchItem:
    entry_id: str
    headword: str
    source_url: str
    part_of_speech: str | None
    original_language: str | None


class DictionaryDefinitionClient:
    """Look up all official meanings without selecting or rewriting a word."""

    def __init__(
        self, provider: Provider, api_key: str, *, client: httpx.Client | None = None
    ) -> None:
        if provider not in _HOSTS:
            raise ValueError(
                "Dictionary definitions require Standard Korean Dictionary or Ourmalsam."
            )
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

    def __enter__(self) -> "DictionaryDefinitionClient":
        if self._closed:
            raise GlossaryAPIError(self.provider, "configuration")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, endpoint: str, params: dict[str, str]) -> ET.Element:
        suffix = ".do" if self.provider == "stdict" else ""
        url = f"https://{_HOSTS[self.provider]}/api/{endpoint}{suffix}"
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
        error = root if root.tag == "error" else root.find(".//error")
        if error is not None:
            provider_code = _text(error, "error_code")
            code = {
                "010": "rate_limit",
                "020": "authentication",
                "021": "authentication",
            }.get(provider_code, "api")
            raise GlossaryAPIError(self.provider, code)
        if self.provider == "stdict" and root.tag == "xml":
            children = list(root)
            if len(children) != 1 or children[0].tag != "channel":
                raise GlossaryAPIError(self.provider, "invalid_response")
            root = children[0]
        if root.tag != "channel":
            raise GlossaryAPIError(self.provider, "invalid_response")
        return root

    def lookup(self, query: str) -> tuple[GlossaryEntry, ...]:
        """Read all official meanings, retaining actual source meaning identifiers.

        Ourmalsam supplies every meaning in search results. Standard Korean
        Dictionary search supplies a representative meaning per entry; its
        detail endpoint supplies all meanings and their ``sense_code`` values.
        """
        if self._closed:
            raise GlossaryAPIError(self.provider, "configuration")
        query = normalize_query(query)
        try:
            if self.provider == "stdict":
                return self._lookup_standard(query)
            entries = tuple(self._search_item(item, query) for item in self._search_pages(query))
            identities = [entry.identity for entry in entries]
            if len(set(identities)) != len(identities):
                raise ValueError("Duplicate search result.")
            return entries
        except (ValueError, ValidationError):
            pass
        raise GlossaryAPIError(self.provider, "invalid_response")

    def _search_pages(self, query: str) -> tuple[ET.Element, ...]:
        results: list[ET.Element] = []
        total: int | None = None
        start = 1
        while total is None or len(results) < total:
            params = {
                "q": query,
                "start": str(start),
                "num": str(_PAGE_SIZE),
                "advanced": "y",
                "target": "1",
                "method": "exact",
                "req_type": "xml",
            }
            if self.provider == "opendict":
                params.update(sort="dict", part="word")
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
            results.extend(items)
            start += len(items)
        return tuple(results)

    def _validate_source_url(self, source_url: str, entry_id: str) -> None:
        parsed = urlsplit(source_url)
        params = parse_qsl(parsed.query, keep_blank_values=True)
        if (
            parsed.scheme not in {"https", "http"}
            or parsed.hostname != _HOSTS[self.provider]
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 80, 443)
            or any(name.lower() in {"key", "api_key"} for name, _ in params)
        ):
            raise ValueError("Invalid dictionary source link.")
        id_param = "word_no" if self.provider == "stdict" else "sense_no"
        source_ids = [value for name, value in params if name.lower() == id_param]
        if (
            len(source_ids) != 1
            or re.fullmatch(r"[0-9]+", source_ids[0]) is None
            or int(source_ids[0]) != int(entry_id)
        ):
            raise ValueError("Source link does not match dictionary entry.")

    def _search_item(self, item: ET.Element, query: str) -> GlossaryEntry:
        headword = _text(item, "word", required=True) or ""
        if canonical_headword(headword) != canonical_headword(query):
            raise ValueError("Exact dictionary search returned a different word.")
        senses = item.findall("sense")
        if len(senses) != 1:
            raise ValueError("Unexpected Ourmalsam search meaning structure.")
        sense = senses[0]
        entry_id = _positive_id(sense, "target_code")
        source_url = _text(sense, "link", required=True) or ""
        entry = GlossaryEntry(
            provider=self.provider,
            entry_id=entry_id,
            sense_id=_ourmalsam_sense_id(sense),
            headword=headword,
            definition=_text(sense, "definition", required=True) or "",
            part_of_speech=_text(sense, "pos"),
            original_language=_text(sense, "origin"),
            source_url=source_url,
        )
        self._validate_source_url(source_url, entry_id)
        return entry

    def _standard_search_item(self, item: ET.Element, query: str) -> _StandardSearchItem:
        headword = _text(item, "word", required=True) or ""
        if canonical_headword(headword) != canonical_headword(query):
            raise ValueError("Exact dictionary search returned a different word.")
        entry_id = _positive_id(item, "target_code")
        senses = item.findall("sense")
        if len(senses) != 1:
            raise ValueError("Unexpected standard dictionary search structure.")
        _text(senses[0], "definition", required=True)
        source_url = _text(senses[0], "link", required=True) or ""
        self._validate_source_url(source_url, entry_id)
        return _StandardSearchItem(
            entry_id,
            headword,
            source_url,
            _text(item, "pos"),
            _text(item, "origin") or _text(senses[0], "origin"),
        )

    def _lookup_standard(self, query: str) -> tuple[GlossaryEntry, ...]:
        items = tuple(self._standard_search_item(item, query) for item in self._search_pages(query))
        identities = [item.entry_id for item in items]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate search result.")
        entries: list[GlossaryEntry] = []
        for item in items:
            entries.extend(self._standard_detail(item))
            if len(entries) > _MAX_RESULTS:
                raise GlossaryAPIError(self.provider, "too_many_results")
        identities = [entry.identity for entry in entries]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate dictionary meaning.")
        return tuple(entries)

    def _standard_detail(self, searched: _StandardSearchItem) -> tuple[GlossaryEntry, ...]:
        root = self._request(
            "view", {"q": searched.entry_id, "method": "target_code", "req_type": "xml"}
        )
        if int(_text(root, "total", required=True) or "") != 1:
            raise ValueError("Unexpected dictionary detail count.")
        items = root.findall("item")
        if len(items) != 1 or _positive_id(items[0], "target_code") != searched.entry_id:
            raise ValueError("Detail response does not match requested identifier.")
        item = items[0]
        words = item.findall("word_info")
        if len(words) != 1:
            raise ValueError("Missing dictionary word information.")
        word = words[0]
        headword = _text(word, "word", required=True) or ""
        if headword != searched.headword:
            raise ValueError("Detail response changed the headword.")
        originals = tuple(
            _text(original, "original_language", required=True) or ""
            for original in word.findall("original_language_info")
        )
        original_language = ", ".join(originals) or searched.original_language
        # The official example puts pos_info beside word_info; the field table
        # lists it under word_info. Read either documented location completely.
        positions = item.findall("pos_info") + word.findall("pos_info")
        if not positions:
            raise ValueError("Missing dictionary part-of-speech information.")
        entries: list[GlossaryEntry] = []
        for position in positions:
            part_of_speech = _text(position, "pos") or searched.part_of_speech
            patterns = position.findall("comm_pattern_info")
            if not patterns:
                raise ValueError("Missing dictionary meaning information.")
            for pattern in patterns:
                senses = pattern.findall("sense_info")
                if not senses:
                    raise ValueError("Missing dictionary meanings.")
                for sense in senses:
                    sense_id = _positive_id(sense, "sense_code")
                    source_url = _text(sense, "link") or searched.source_url
                    self._validate_source_url(source_url, searched.entry_id)
                    entries.append(
                        GlossaryEntry(
                            provider="stdict",
                            entry_id=searched.entry_id,
                            sense_id=sense_id,
                            headword=headword,
                            definition=_text(sense, "definition", required=True) or "",
                            part_of_speech=part_of_speech,
                            original_language=original_language,
                            source_url=source_url,
                        )
                    )
                    if len(entries) > _MAX_RESULTS:
                        raise GlossaryAPIError(self.provider, "too_many_results")
        return tuple(entries)
