import os
from dataclasses import dataclass, field
from math import isfinite
from typing import Self
from urllib.parse import quote, quote_plus, unquote, urlsplit


class ConfigError(ValueError):
    """Raised when required pipeline configuration is invalid."""


def _redact(message: str, secrets: set[str]) -> str:
    variants = {
        variant
        for secret in secrets if secret
        for variant in (secret, quote(secret, safe=""), quote_plus(secret, safe=""))
    }
    for variant in sorted(variants, key=len, reverse=True):
        message = message.replace(variant, "[REDACTED]")
    return message


def _positive_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    try:
        value = float(raw_value)
    except ValueError:
        # Do not include the supplied value in a chained traceback.
        raise ConfigError(f"{name}은(는) 숫자여야 합니다.") from None

    if not isfinite(value) or value <= 0:
        raise ConfigError(f"{name}은(는) 유한한 양수여야 합니다.")
    return value


def _validate_database_url(value: str) -> None:
    """Check a TCP PostgreSQL URI without connecting or exposing credentials."""
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in ("postgresql", "postgres")
            and bool(parsed.hostname)
            and not parsed.fragment
            and not any(character.isspace() for character in value)
            and (parsed.port is None or parsed.port > 0)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ConfigError("DATABASE_URL은(는) 호스트가 있는 PostgreSQL URI여야 합니다.")


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """Database-only settings, independent of source API credentials."""

    database_url: str = field(repr=False)

    @classmethod
    def from_env(cls) -> Self:
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            raise ConfigError("필수 환경 변수가 없습니다: DATABASE_URL")
        _validate_database_url(database_url)
        return cls(database_url=database_url)


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = field(repr=False)
    seoul_api_key: str = field(repr=False)
    http_connect_timeout_seconds: float
    http_read_timeout_seconds: float

    @classmethod
    def from_env(cls) -> Self:
        required = {
            "DATABASE_URL": os.getenv("DATABASE_URL", "").strip(),
            "SEOUL_API_KEY": os.getenv("SEOUL_API_KEY", "").strip(),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ConfigError(f"필수 환경 변수가 없습니다: {', '.join(missing)}")

        _validate_database_url(required["DATABASE_URL"])
        return cls(
            database_url=required["DATABASE_URL"],
            seoul_api_key=required["SEOUL_API_KEY"],
            http_connect_timeout_seconds=_positive_float("HTTP_CONNECT_TIMEOUT_SECONDS", 5.0),
            http_read_timeout_seconds=_positive_float("HTTP_READ_TIMEOUT_SECONDS", 20.0),
        )

    def redact(self, message: str) -> str:
        """Mask known secrets before emitting future request/DB error messages.

        Callers must use this explicitly; it does not install a logging filter.
        Handles raw and URL-encoded values, not arbitrary secret encodings.
        """
        secrets = {self.database_url, self.seoul_api_key}
        password = urlsplit(self.database_url).password
        if password:
            secrets.update((password, unquote(password)))
        return _redact(message, secrets)


@dataclass(frozen=True, slots=True)
class NowonSettings:
    """Only the credentials needed to read the Nowon notice API."""

    nowon_notice_api_key: str = field(repr=False)
    http_connect_timeout_seconds: float
    http_read_timeout_seconds: float

    @classmethod
    def from_env(cls) -> Self:
        key = os.getenv("NOWON_NOTICE_API_KEY", "").strip()
        if not key:
            raise ConfigError("필수 환경 변수가 없습니다: NOWON_NOTICE_API_KEY")
        return cls(
            nowon_notice_api_key=key,
            http_connect_timeout_seconds=_positive_float("HTTP_CONNECT_TIMEOUT_SECONDS", 5.0),
            http_read_timeout_seconds=_positive_float("HTTP_READ_TIMEOUT_SECONDS", 20.0),
        )

    def redact(self, message: str) -> str:
        return _redact(message, {self.nowon_notice_api_key})
