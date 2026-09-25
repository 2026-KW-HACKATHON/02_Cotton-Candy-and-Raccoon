from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def isolate_pipeline_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not let the developer's real credentials or overrides affect tests."""
    for name in (
        "DATABASE_URL", "SEOUL_API_KEY", "NOWON_NOTICE_API_KEY",
        "HTTP_CONNECT_TIMEOUT_SECONDS", "HTTP_READ_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def mock_collect_db(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Keep CLI collection tests off any real database."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://pipeline@localhost/test")
    conn = MagicMock()
    conn.__enter__.return_value = conn
    monkeypatch.setattr("pipeline.cli.psycopg.connect", lambda *args, **kwargs: conn)
    monkeypatch.setattr("pipeline.cli.save_notice_with_files", lambda *args: 42)
    return conn
