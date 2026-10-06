"""Dictionary meanings and lookup results, without inventing easier expressions."""

import unicodedata
from datetime import datetime
from typing import Literal, Self
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Provider = Literal["onterm", "opendict", "krdict"]
ACTIVE_PROVIDERS: tuple[Provider, ...] = ("onterm", "opendict")
SOURCE_NAMES = {
    "onterm": "국립국어원 온용어",
    "opendict": "국립국어원 우리말샘",
    "krdict": "국립국어원 한국어기초사전",
}
SOURCE_HOSTS = {
    "onterm": "kli.korean.go.kr",
    "opendict": "opendict.korean.go.kr",
    "krdict": "krdict.korean.go.kr",
}
LICENSE = "CC BY-SA 2.0 KR"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/2.0/kr/"
ONTERM_LICENSE = "KOGL 1"
ONTERM_LICENSE_URL = "https://www.kogl.or.kr/info/licenseType1.do"


def normalize_query(query: str) -> str:
    """Normalize Unicode and whitespace; preserve meaningful word boundaries."""
    if not isinstance(query, str):
        raise ValueError("용어는 문자열이어야 합니다.")
    result = " ".join(unicodedata.normalize("NFC", query).split())
    if not result or len(result) > 200 or any(ord(char) < 32 for char in result):
        raise ValueError("용어는 1~200자의 유효한 문자열이어야 합니다.")
    return result


def canonical_headword(value: str) -> str:
    """Compare official dictionary word markers without rewriting the saved headword.

    Dictionaries mark compounds with ^ and syllable boundaries with hyphens.
    The source may omit these editorial markers; meaning IDs stay separate.
    """
    return normalize_query(value.replace("^", " ").replace("-", "")).replace(" ", "")


class NormInfo(BaseModel):
    """Preserve the dictionary's complete normative explanation and conditions."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    type: str = Field(min_length=1)
    role: str | None = None
    description: str | None = None


class GlossaryEntry(BaseModel):
    """One meaning identified by dictionary, entry code and sense code."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    provider: Provider
    entry_id: str = Field(min_length=1)
    sense_id: str = Field(min_length=1)
    headword: str = Field(min_length=1)
    definition: str | None = Field(default=None, min_length=1)
    part_of_speech: str | None = None
    original_language: str | None = None
    easy_terms: tuple[str, ...] = ()
    norm_info: tuple[NormInfo, ...] = ()
    source_url: str
    source_name: str = ""
    source_institution: str | None = None
    source_glossary: str | None = None
    entry_id_kind: Literal["provider_id", "content_hash"] = "provider_id"
    license: Literal["CC BY-SA 2.0 KR", "KOGL 1"] = LICENSE
    license_url: Literal[
        "https://creativecommons.org/licenses/by-sa/2.0/kr/",
        "https://www.kogl.or.kr/info/licenseType1.do",
    ] = LICENSE_URL

    @model_validator(mode="before")
    @classmethod
    def set_source_name(cls, values: object) -> object:
        if isinstance(values, dict):
            values = dict(values)
            if not values.get("source_name"):
                values["source_name"] = SOURCE_NAMES.get(values.get("provider"), "")
            if values.get("provider") == "onterm":
                values.setdefault("license", ONTERM_LICENSE)
                values.setdefault("license_url", ONTERM_LICENSE_URL)
        return values

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        parsed = urlsplit(self.source_url)
        if (
            parsed.scheme not in ("https", "http")
            or parsed.hostname != SOURCE_HOSTS[self.provider]
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 80, 443)
            or any(key.lower() in ("key", "api_key") for key, _ in parse_qsl(parsed.query))
            or self.source_name != SOURCE_NAMES[self.provider]
        ):
            raise ValueError("사전 출처 URL 또는 출처 이름이 올바르지 않습니다.")
        for value in (self.entry_id, self.sense_id, self.headword):
            if not value.strip():
                raise ValueError("사전 항목의 필수 정보가 비어 있습니다.")
        if any(not value.strip() for value in self.easy_terms):
            raise ValueError("다듬은 말이 비어 있습니다.")
        if self.provider == "onterm":
            if (
                self.entry_id_kind != "content_hash"
                or not self.entry_id.startswith("sha256:")
                or len(self.entry_id) != 71
                or self.sense_id != "content"
                or any(char not in "0123456789abcdef" for char in self.entry_id[7:])
                or not self.source_institution
                or not self.source_institution.strip()
                or not self.source_glossary
                or not self.source_glossary.strip()
                or not self.easy_terms
                or self.license != ONTERM_LICENSE
                or self.license_url != ONTERM_LICENSE_URL
            ):
                raise ValueError("온용어의 다듬은 말·출처·이용 조건이 올바르지 않습니다.")
        elif (
            not self.definition
            or not self.definition.strip()
            or self.entry_id_kind != "provider_id"
            or self.license != LICENSE
            or self.license_url != LICENSE_URL
        ):
            raise ValueError("사전 뜻풀이·식별자·이용 조건이 올바르지 않습니다.")
        if self.definition is not None and not self.definition.strip():
            raise ValueError("뜻풀이가 비어 있습니다. 제공되지 않은 경우 null을 사용하세요.")
        return self

    @property
    def identity(self) -> tuple[Provider, str, str]:
        return self.provider, self.entry_id, self.sense_id


class GlossaryLookup(BaseModel):
    """A complete successful search; failures must never become empty successes."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    query: str
    status: Literal["found", "not_found"]
    entries: tuple[GlossaryEntry, ...] = ()
    providers_checked: tuple[Provider, ...]
    queried_at: datetime
    from_cache: bool = False

    _normalize_query = field_validator("query")(normalize_query)

    @model_validator(mode="after")
    def validate_complete_lookup(self) -> Self:
        if self.queried_at.tzinfo is None or self.queried_at.utcoffset() is None:
            raise ValueError("조회 시각에는 시간대가 필요합니다.")
        if not self.providers_checked or len(set(self.providers_checked)) != len(
            self.providers_checked
        ):
            raise ValueError("조회한 사전 목록이 올바르지 않습니다.")
        if (self.status == "found") != bool(self.entries):
            raise ValueError("조회 상태와 항목 목록이 일치하지 않습니다.")
        identities = [entry.identity for entry in self.entries]
        if len(set(identities)) != len(identities):
            raise ValueError("조회 결과에 중복 의미가 있습니다.")
        if any(entry.provider not in self.providers_checked for entry in self.entries):
            raise ValueError("조회하지 않은 사전의 항목이 포함되어 있습니다.")
        return self


def active_cached_lookup(cached: GlossaryLookup | None) -> GlossaryLookup | None:
    """Reuse active-source results without deleting historical dictionary records.

    A former three-provider fallback already checked OnTerm and Ourmalsam before
    consulting the excluded source. Its empty active-source results can therefore
    be reused as not_found, while its excluded definitions never enter new output.
    An excluded-only cache without those checks is not a complete active lookup.
    """
    if cached is None or "krdict" not in cached.providers_checked:
        return cached
    checked = tuple(
        provider for provider in cached.providers_checked if provider in ACTIVE_PROVIDERS
    )
    entries = tuple(entry for entry in cached.entries if entry.provider in ACTIVE_PROVIDERS)
    if not entries and checked != ACTIVE_PROVIDERS:
        return None
    if (
        not entries
        and cached.status == "found"
        and cached.providers_checked != (*ACTIVE_PROVIDERS, "krdict")
    ):
        return None
    return GlossaryLookup(
        query=cached.query,
        status="found" if entries else "not_found",
        entries=entries,
        providers_checked=checked,
        queried_at=cached.queried_at,
        from_cache=cached.from_cache,
    )
