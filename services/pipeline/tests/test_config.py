import traceback
from urllib.parse import quote

import pytest

from pipeline.config import ConfigError, Settings


def test_settings_load_required_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/postgres")
    monkeypatch.setenv("SEOUL_API_KEY", "sample")

    settings = Settings.from_env()

    assert settings.database_url == "postgresql://localhost/postgres"
    assert settings.seoul_api_key == "sample"
    assert settings.http_connect_timeout_seconds == 5.0
    assert settings.http_read_timeout_seconds == 20.0


def test_settings_report_all_missing_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SEOUL_API_KEY", raising=False)

    with pytest.raises(ConfigError, match="DATABASE_URL, SEOUL_API_KEY"):
        Settings.from_env()


def test_settings_reject_non_positive_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/postgres")
    monkeypatch.setenv("SEOUL_API_KEY", "sample")
    monkeypatch.setenv("HTTP_READ_TIMEOUT_SECONDS", "0")

    with pytest.raises(ConfigError, match="HTTP_READ_TIMEOUT_SECONDS"):
        Settings.from_env()


@pytest.fixture
def valid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:fake-password@localhost/db")
    monkeypatch.setenv("SEOUL_API_KEY", "fake-api-key")


@pytest.mark.usefixtures("valid_env")
@pytest.mark.parametrize("name", ["DATABASE_URL", "SEOUL_API_KEY"])
@pytest.mark.parametrize("value", ["", " \t\n"])
def test_required_settings_reject_blank(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str,
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=name):
        Settings.from_env()


@pytest.mark.usefixtures("valid_env")
@pytest.mark.parametrize("name", ["HTTP_CONNECT_TIMEOUT_SECONDS", "HTTP_READ_TIMEOUT_SECONDS"])
@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf", "1e999", "", "bad-secret"])
def test_timeout_rejects_invalid_or_unbounded_values(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str,
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=name) as error:
        Settings.from_env()
    rendered = "".join(traceback.format_exception(error.value))
    assert "bad-secret" not in rendered


@pytest.mark.usefixtures("valid_env")
def test_timeout_overrides_and_required_value_trimming(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_CONNECT_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setenv("HTTP_READ_TIMEOUT_SECONDS", " 30 ")
    monkeypatch.setenv("SEOUL_API_KEY", " sample ")
    monkeypatch.setenv("DATABASE_URL", " postgresql://localhost/db ")
    settings = Settings.from_env()
    assert settings.http_connect_timeout_seconds == 0.5
    assert settings.http_read_timeout_seconds == 30.0
    assert settings.seoul_api_key == "sample"
    assert settings.database_url == "postgresql://localhost/db"


@pytest.mark.usefixtures("valid_env")
@pytest.mark.parametrize("url", [
    "postgresql://localhost/db", "postgres://user:pass@localhost:5432/db",
    "postgresql://user:p%40ss@[::1]:5432/db?sslmode=require",
])
def test_postgresql_uri_forms(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    assert Settings.from_env().database_url == url


@pytest.mark.usefixtures("valid_env")
@pytest.mark.parametrize("url", [
    "https://user:fake-secret@localhost/db", "postgresql:///db",
    "postgresql://user:fake-secret@localhost:bad/db",
    "postgresql://localhost:99999/db", "postgresql://localhost:0/db",
    "postgresql://[broken/db", "postgresql://localhost/db#fragment",
    "postgresql://local host/db", "host=localhost dbname=db",
])
def test_database_uri_rejects_invalid_without_leaking(
    monkeypatch: pytest.MonkeyPatch, url: str,
) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(ConfigError, match="DATABASE_URL") as error:
        Settings.from_env()
    rendered = "".join(traceback.format_exception(error.value))
    assert url not in rendered
    assert "fake-secret" not in rendered


@pytest.mark.usefixtures("valid_env")
def test_settings_repr_excludes_credentials() -> None:
    settings = Settings.from_env()
    assert settings.seoul_api_key not in repr(settings)
    assert settings.database_url not in repr(settings)
    assert "fake-password" not in str(settings)


@pytest.mark.usefixtures("valid_env")
def test_redact_masks_request_key_and_database_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    key = "fake/key+value"
    password = "fake/p@ss"
    url = f"postgresql://user:{quote(password, safe='')}@localhost/db"
    monkeypatch.setenv("SEOUL_API_KEY", key)
    monkeypatch.setenv("DATABASE_URL", url)
    settings = Settings.from_env()
    request_url = (
        f"http://openapi.seoul.go.kr:8088/{quote(key, safe='')}/json/NowonNewsNoticeList/1/5/"
    )
    message = f"{request_url} {key} {url} {password} {quote(password, safe='')}"
    redacted = settings.redact(message)
    for secret in (key, quote(key, safe=""), url, password, quote(password, safe="")):
        assert secret not in redacted
    assert "NowonNewsNoticeList/1/5/" in redacted
    assert "[REDACTED]" in redacted
    assert settings.redact("request timed out") == "request timed out"
