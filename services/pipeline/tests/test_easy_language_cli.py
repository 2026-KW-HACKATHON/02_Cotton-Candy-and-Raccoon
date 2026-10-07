"""Exercise the default Gemini CLI without external services."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pipeline.glossary import cli
from pipeline.glossary import easy_language_cli as module
from pipeline.glossary.easy_language import (
    EasyLanguageAPIError,
    EasyLanguageValidationError,
    simplify_notice,
)
from pipeline.glossary.source import NoticeGlossaryInput


@pytest.fixture
def offline(monkeypatch, tmp_path):
    source = NoticeGlossaryInput(notice_id=7, text="구비서류를 지참하세요.\n")
    path = tmp_path / "notice.json"
    path.write_text(source.model_dump_json(), encoding="utf-8")

    def request(**kwargs):
        return json.dumps(
            {
                "changes": [
                    {
                        "original": "구비서류를",
                        "replacement": "준비할 서류를",
                        "context": source.text,
                    }
                ]
            },
            ensure_ascii=False,
        )

    result = simplify_notice(
        source,
        api_key="fake",
        request=request,
        clock=lambda: datetime(2026, 10, 7, tzinfo=UTC),
    )
    gemini = MagicMock(return_value=result)
    dictionary = MagicMock(
        side_effect=AssertionError("notice conversion must not call a dictionary")
    )
    connect = MagicMock()
    conn = connect.return_value.__enter__.return_value
    monkeypatch.setattr(module, "simplify_notice", gemini)
    monkeypatch.setattr(module, "lookup_dictionary_definition", dictionary)
    monkeypatch.setattr(module.psycopg, "connect", connect)
    monkeypatch.setattr(module, "load_gemini_api_key", lambda: "fake")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    return SimpleNamespace(
        source=source,
        path=path,
        result=result,
        gemini=gemini,
        dictionary=dictionary,
        connect=connect,
        conn=conn,
    )


def test_file_mode_preserves_original_and_never_calls_dictionary(offline, capsys):
    assert module.main(["--input", str(offline.path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["original_text"] == offline.source.text
    assert payload["easy_text"] == "준비할 서류를 지참하세요.\n"
    offline.dictionary.assert_not_called()
    offline.connect.assert_not_called()


def test_default_entry_uses_gemini_route(monkeypatch):
    entry = MagicMock(return_value=0)
    monkeypatch.setattr(module, "main", entry)
    assert cli.main() == 0
    entry.assert_called_once_with()


def test_notice_db_mode_uses_saved_service_without_eager_credentials(offline, monkeypatch, capsys):
    saved = MagicMock(return_value=offline.result)
    monkeypatch.setattr(module, "simplify_and_store_notice", saved)
    monkeypatch.setattr(module, "load_notice_glossary_input", lambda conn, notice: offline.source)
    monkeypatch.setattr(module, "load_gemini_api_key", MagicMock(side_effect=AssertionError))
    assert module.main(["--notice-id", "7"]) == 0
    assert json.loads(capsys.readouterr().out)["original_text"] == offline.source.text
    saved.assert_called_once()
    offline.dictionary.assert_not_called()
    offline.gemini.assert_not_called()


def test_word_mode_always_uses_db_cache_and_never_reads_gemini_key(offline, monkeypatch, capsys):
    result = MagicMock()
    result.model_dump_json.return_value = '{"status":"not_found"}'
    offline.dictionary.side_effect = None
    offline.dictionary.return_value = result
    monkeypatch.setattr(module, "load_gemini_api_key", MagicMock(side_effect=AssertionError))
    assert module.main(["--word", "산정"]) == 0
    offline.dictionary.assert_called_once_with(offline.conn, "산정", refresh=False)
    offline.gemini.assert_not_called()
    assert json.loads(capsys.readouterr().out)["status"] == "not_found"


@pytest.mark.parametrize("error", [EasyLanguageAPIError, EasyLanguageValidationError])
def test_api_failure_outputs_no_result_and_never_exposes_details(error, offline, capsys):
    offline.gemini.side_effect = error("private-api-secret")
    assert module.main(["--input", str(offline.path)]) == 1
    output = capsys.readouterr()
    assert output.out == "" and "private-api-secret" not in output.err


def test_output_cannot_overwrite_input(offline):
    original = offline.path.read_bytes()
    with pytest.raises(SystemExit):
        module.main(["--input", str(offline.path), "--output", str(offline.path)])
    assert offline.path.read_bytes() == original
    offline.gemini.assert_not_called()


@pytest.mark.parametrize("word", ["", " ", "\t\n"])
def test_blank_word_rejected_before_database_or_network(offline, word):
    with pytest.raises(SystemExit):
        module.main(["--word", word])
    offline.dictionary.assert_not_called()
    offline.connect.assert_not_called()
