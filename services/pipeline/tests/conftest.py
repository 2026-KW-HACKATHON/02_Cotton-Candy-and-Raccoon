import pytest


@pytest.fixture(autouse=True)
def isolate_pipeline_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not let the developer's real credentials or overrides affect tests."""
    for name in (
        "DATABASE_URL", "SEOUL_API_KEY", "NOWON_NOTICE_API_KEY",
        "HTTP_CONNECT_TIMEOUT_SECONDS", "HTTP_READ_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
