"""Shared, context-independent standard dictionary query and result contracts."""

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Literal, Self
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "stdict-v1"
# Pagination is transport, not a different search. Every accepted result is complete.
SEARCH_CONDITIONS = {"provider": "stdict", "target": 1, "method": "exact", "pos": 0}


class DictionaryError(RuntimeError):
    """Only fixed safe codes escape this boundary, never request URLs or API keys."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class DictionaryBusy(DictionaryError):
    """Another worker owns the query; callers may retry after its lease expires."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("dictionary_lookup_busy", retryable=True)


def normalize_query_word(value: str) -> str:
    """Normalize Unicode and outer spaces, without stemming or changing internal spelling."""
    if not isinstance(value, str):
        raise DictionaryError("invalid_dictionary_query")
    word = unicodedata.normalize("NFC", value).strip()
    if not 1 <= len(word) <= 100 or any(unicodedata.category(c).startswith("C") for c in word):
        raise DictionaryError("invalid_dictionary_query")
    return word


@dataclass(frozen=True, slots=True)
class DictionaryQuery:
    word: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "word", normalize_query_word(self.word))

    @property
    def cache_key(self) -> str:
        identity = {"word": self.word, "conditions": SEARCH_CONDITIONS, "version": CONTRACT_VERSION}
        return hashlib.sha256(json.dumps(
            identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()


class _DictionaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DictionarySense(_DictionaryModel):
    sense_code: str = Field(pattern=r"^[0-9]+$")
    pos_code: str = Field(pattern=r"^[0-9]+$")
    part_of_speech: str = Field(min_length=1, max_length=100)
    definition: str = Field(min_length=1, max_length=20000)


class DictionaryEntry(_DictionaryModel):
    target_code: str = Field(pattern=r"^[0-9]+$")
    headword: str = Field(min_length=1, max_length=200)
    homonym_number: str | None = Field(default=None, pattern=r"^[0-9]+$")
    source_url: str = Field(max_length=2048)
    senses: tuple[DictionarySense, ...] = Field(min_length=1, max_length=1000)

    @field_validator("source_url")
    @classmethod
    def official_source(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https" or parsed.netloc != "stdict.korean.go.kr"
            or parsed.path != "/search/searchView.do" or parsed.fragment
        ):
            raise ValueError("invalid_dictionary_source")
        return value

    @model_validator(mode="after")
    def check_identity(self) -> Self:
        if parse_qs(urlsplit(self.source_url).query).get("word_no") != [self.target_code]:
            raise ValueError("dictionary_source_mismatch")
        codes = [sense.sense_code for sense in self.senses]
        if len(set(codes)) != len(codes):
            raise ValueError("duplicate_dictionary_sense")
        return self


class DictionaryResult(_DictionaryModel):
    query_word: str
    contract_version: Literal["stdict-v1"] = CONTRACT_VERSION
    status: Literal["found", "not_found"]
    entries: tuple[DictionaryEntry, ...] = Field(max_length=1000)

    @field_validator("query_word")
    @classmethod
    def normalized_word(cls, value: str) -> str:
        if normalize_query_word(value) != value:
            raise ValueError("dictionary_query_not_normalized")
        return value

    @model_validator(mode="after")
    def consistent_result(self) -> Self:
        if (self.status == "found") != bool(self.entries):
            raise ValueError("invalid_dictionary_result_status")
        codes = [entry.target_code for entry in self.entries]
        if len(set(codes)) != len(codes):
            raise ValueError("duplicate_dictionary_entry")
        return self


@dataclass(frozen=True, slots=True)
class DictionaryLookup:
    result: DictionaryResult
    cache_hit: bool
