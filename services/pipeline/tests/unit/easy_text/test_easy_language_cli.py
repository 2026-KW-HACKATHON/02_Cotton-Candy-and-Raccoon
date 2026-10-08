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
                ],
                "dictionary_candidates": [],
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
    connect = MagicMock()
    conn = connect.return_value.__enter__.return_value
    monkeypatch.setattr(module, "simplify_notice", gemini)
    monkeypatch.setattr(module.psycopg, "connect", connect)
    monkeypatch.setattr(module, "load_gemini_api_key", lambda: "fake")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    return SimpleNamespace(
        source=source,
        path=path,
        result=result,
        gemini=gemini,
        connect=connect,
        conn=conn,
    )


def test_file_mode_preserves_original_and_never_calls_dictionary(offline, capsys):
    assert module.main(["--input", str(offline.path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["original_text"] == offline.source.text
    assert payload["easy_text"] == "준비할 서류를 지참하세요.\n"
    assert payload["body_text_present"] is None
    assert payload["attachment_content_included"] is None
    assert not {"dictionary_terms", "dictionary_results", "dictionary_failures"} & payload.keys()
    offline.connect.assert_not_called()


def test_direct_json_cannot_inject_db_processing_scope(offline, capsys):
    payload = json.loads(offline.path.read_text("utf-8"))
    payload.update(body_text_present=True, attachment_content_included=False)
    offline.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert module.main(["--input", str(offline.path)]) == 2
    assert capsys.readouterr().out == ""
    offline.gemini.assert_not_called()
    offline.connect.assert_not_called()


def test_default_entry_uses_gemini_route(monkeypatch):
    entry = MagicMock(return_value=0)
    monkeypatch.setattr(module, "main", entry)
    assert cli.main() == 0
    entry.assert_called_once_with()


def test_notice_db_mode_uses_saved_service_without_eager_credentials(offline, monkeypatch, capsys):
    phases = []
    transaction = offline.conn.transaction.return_value
    transaction.__enter__.side_effect = lambda: phases.append("read_started")
    transaction.__exit__.side_effect = lambda *_args: phases.append("read_finished")

    def load(conn, notice_id):
        assert conn is offline.conn and notice_id == 7
        assert phases == ["read_started"]
        return offline.source

    def process(conn, source, **_kwargs):
        assert conn is offline.conn and source is offline.source
        assert phases == ["read_started", "read_finished"]
        return offline.result

    saved = MagicMock(side_effect=process)
    monkeypatch.setattr(module, "simplify_and_store_notice", saved)
    monkeypatch.setattr(module, "load_notice_glossary_input", load)
    monkeypatch.setattr(module, "load_gemini_api_key", MagicMock(side_effect=AssertionError))
    assert module.main(["--notice-id", "7"]) == 0
    assert json.loads(capsys.readouterr().out)["original_text"] == offline.source.text
    saved.assert_called_once()
    offline.gemini.assert_not_called()


def test_file_save_mode_uses_conversion_service_and_forwards_refresh(offline, monkeypatch, capsys):
    saved = MagicMock(return_value=offline.result)
    monkeypatch.setattr(module, "simplify_and_store_notice", saved)
    monkeypatch.setattr(module, "load_gemini_api_key", MagicMock(side_effect=AssertionError))
    assert module.main(["--input", str(offline.path), "--save", "--refresh"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["original_text"] == offline.source.text
    assert payload["easy_text"] == offline.result.easy_text
    assert payload["changes"] == offline.result.model_dump(mode="json")["changes"]
    assert not {"dictionary_terms", "dictionary_results", "dictionary_failures"} & payload.keys()
    assert "conversion" not in payload
    saved.assert_called_once_with(
        offline.conn, offline.source, refresh=True, model=module.DEFAULT_MODEL
    )
    offline.gemini.assert_not_called()


def test_corrupt_notice_conversion_outputs_no_result(offline, monkeypatch, capsys):
    from pipeline.storage.notice_easy_text import EasyTextStorageError

    saved = MagicMock(side_effect=EasyTextStorageError("private-snapshot-details"))
    monkeypatch.setattr(module, "simplify_and_store_notice", saved)
    monkeypatch.setattr(module, "load_notice_glossary_input", lambda *args: offline.source)
    assert module.main(["--notice-id", "7"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "private-snapshot-details" not in output.err


def test_word_mode_is_removed_before_database_or_network(offline):
    with pytest.raises(SystemExit) as error:
        module.main(["--word", "산정"])
    assert error.value.code == 2
    offline.connect.assert_not_called()
    offline.gemini.assert_not_called()


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


def test_refresh_requires_database_mode_before_network(offline):
    with pytest.raises(SystemExit):
        module.main(["--input", str(offline.path), "--refresh"])
    offline.connect.assert_not_called()
    offline.gemini.assert_not_called()
