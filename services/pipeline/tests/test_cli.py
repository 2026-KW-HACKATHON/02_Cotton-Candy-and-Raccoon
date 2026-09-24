import pytest

from pipeline.cli import main


def test_check_config_does_not_print_secrets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_url = "postgresql://pipeline:secret-password@localhost/postgres"
    api_key = "secret-api-key"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("SEOUL_API_KEY", api_key)

    exit_code = main(["check-config"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert database_url not in output
    assert api_key not in output


def test_check_config_missing_values_returns_failure(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check-config"]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "DATABASE_URL" in output.err
    assert "SEOUL_API_KEY" in output.err


@pytest.mark.parametrize("name,value", [
    ("DATABASE_URL", "https://user:secret-password@localhost/db"),
    ("HTTP_READ_TIMEOUT_SECONDS", "private-invalid-timeout"),
])
def test_check_config_failure_does_not_print_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    name: str, value: str,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/db")
    monkeypatch.setenv("SEOUL_API_KEY", "secret-api-key")
    monkeypatch.setenv(name, value)
    assert main(["check-config"]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert name in output.err
    assert value not in output.err
    assert "secret-api-key" not in output.err
