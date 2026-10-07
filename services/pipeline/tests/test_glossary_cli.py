"""Run the file/DB glossary CLI offline and verify real result JSON and safe exits."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import psycopg
import pytest

from pipeline.config import ConfigError
from pipeline.glossary import cli as module
from pipeline.glossary.document import NoticeGlossaryResult
from pipeline.glossary.models import GlossaryEntry, GlossaryLookup
from pipeline.glossary.process import process_notice_glossary
from pipeline.glossary.source import NoticeGlossaryInput
from pipeline.storage.glossary import GlossaryStorageError
from pipeline.storage.notice_glossary import NoticeGlossaryStorageError

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
SECRET = "private-cli-secret"
DATABASE_URL = f"postgresql://pipeline:{SECRET}@localhost/test"


def lookup_result(query):
    entries = ()
    if query == "익일":
        entries = (
            GlossaryEntry(
                provider="opendict",
                entry_id="123",
                sense_id="001",
                headword=query,
                definition="어떤 날의 다음 날.",
                easy_terms=("다음 날",),
                source_url="https://opendict.korean.go.kr/dictionary/view?sense_no=123",
            ),
        )
    return GlossaryLookup(
        query=query,
        status="found" if entries else "not_found",
        entries=entries,
        providers_checked=("opendict",),
        queried_at=NOW,
    )


def run(monkeypatch, *arguments):
    monkeypatch.setattr("sys.argv", ["notice-glossary", *(str(argument) for argument in arguments)])
    return module._dictionary_main()


@pytest.fixture
def source_file(tmp_path):
    source = NoticeGlossaryInput(notice_id=42, text="익일 공람")
    path = tmp_path / "source.json"
    path.write_text(source.model_dump_json(), encoding="utf-8-sig")
    return source, path


@pytest.fixture
def offline(monkeypatch):
    query = MagicMock(side_effect=lookup_result)
    connect = MagicMock(side_effect=AssertionError("file mode must not connect to a DB"))
    save = MagicMock(side_effect=AssertionError("file mode must not save to a DB"))
    gemini = MagicMock(side_effect=AssertionError("glossary processing must not invoke Gemini"))
    monkeypatch.setattr(module, "query_glossary", query)
    monkeypatch.setattr(module.psycopg, "connect", connect)
    monkeypatch.setattr(module, "process_and_store_notice_glossary", save)
    monkeypatch.setattr("google.genai.Client", gemini)
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    return SimpleNamespace(query=query, connect=connect, save=save, gemini=gemini)


@pytest.fixture
def db_mode(monkeypatch, offline):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    offline.connect.side_effect = None
    offline.connect.return_value = conn
    offline.save.side_effect = lambda actual_conn, source, **kwargs: process_notice_glossary(
        source, lookup_result, max_queries=kwargs["max_queries"], clock=lambda: NOW
    )
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    return conn, offline


def parsed_stdout(capsys):
    output = capsys.readouterr()
    assert output.err == ""
    return NoticeGlossaryResult.model_validate_json(output.out)


def test_file_mode_prints_full_validated_result_with_default_query_budget(
    monkeypatch, capsys, source_file, offline
):
    source, path = source_file
    original = path.read_bytes()
    process = MagicMock(wraps=module.process_notice_glossary)
    monkeypatch.setattr(module, "process_notice_glossary", process)
    assert run(monkeypatch, "--input", path) == 0
    returned = parsed_stdout(capsys)
    assert returned.original_text == source.text
    assert returned.easy_text == "다음 날 공람"
    assert len(returned.terms) == len(returned.queries) == 2
    assert returned.terms[0].entries[0].source_url.endswith("sense_no=123")
    assert process.call_args.kwargs["max_queries"] == 100
    assert process.call_args.kwargs["previous"] is None
    assert path.read_bytes() == original
    offline.connect.assert_not_called()
    offline.save.assert_not_called()
    offline.gemini.assert_not_called()


def test_output_file_matches_stdout_and_preserves_source(
    monkeypatch, capsys, tmp_path, source_file, offline
):
    source, path = source_file
    output_path = tmp_path / "result.json"
    assert run(monkeypatch, "--input", path, "--output", output_path) == 0
    returned = parsed_stdout(capsys)
    assert NoticeGlossaryResult.model_validate_json(output_path.read_text("utf-8")) == returned
    assert NoticeGlossaryInput.model_validate_json(path.read_text("utf-8-sig")) == source


def test_budget_exhaustion_outputs_explicit_partial_json_and_exit_three(
    monkeypatch, capsys, source_file, offline
):
    source, path = source_file
    assert run(monkeypatch, "--input", path, "--max-queries", 1) == 3
    returned = parsed_stdout(capsys)
    assert returned.status == "partial"
    assert returned.new_query_count == 1
    assert returned.pending_queries == ("공람",)
    assert len(returned.terms) == 2 and len(returned.queries) == 1
    assert returned.original_text == source.text
    assert [call.args[0] for call in offline.query.call_args_list] == ["익일"]


def test_resume_reads_validated_result_and_only_queries_remaining_words(
    monkeypatch, capsys, tmp_path, source_file, offline
):
    source, path = source_file
    previous = process_notice_glossary(source, lookup_result, max_queries=1, clock=lambda: NOW)
    resume_path = tmp_path / "previous.json"
    resume_path.write_text(previous.model_dump_json(), encoding="utf-8-sig")
    assert run(monkeypatch, "--input", path, "--resume", resume_path, "--max-queries", 1) == 0
    returned = parsed_stdout(capsys)
    assert returned.status == "completed" and returned.new_query_count == 1
    assert len(returned.terms) == len(returned.queries) == 2
    assert [call.args[0] for call in offline.query.call_args_list] == ["공람"]
    assert returned.easy_text == "다음 날 공람"


def test_completed_file_resume_has_no_dictionary_queries(
    monkeypatch, capsys, tmp_path, source_file, offline
):
    source, path = source_file
    previous = process_notice_glossary(source, lookup_result, clock=lambda: NOW)
    resume_path = tmp_path / "previous.json"
    resume_path.write_text(previous.model_dump_json(), encoding="utf-8")
    assert run(monkeypatch, "--input", path, "--resume", resume_path) == 0
    returned = parsed_stdout(capsys)
    assert returned.new_query_count == 0
    offline.query.assert_not_called()


@pytest.mark.parametrize("changed", ["source", "rules"])
def test_stale_resume_is_rebuilt_from_current_input(
    monkeypatch, capsys, tmp_path, source_file, offline, changed
):
    source, path = source_file
    previous_source = (
        NoticeGlossaryInput(notice_id=42, text="예전") if changed == "source" else source
    )
    previous = process_notice_glossary(previous_source, lookup_result, clock=lambda: NOW)
    if changed == "rules":
        previous = previous.model_copy(update={"rules_version": "older-rules"})
    resume_path = tmp_path / "previous.json"
    resume_path.write_text(previous.model_dump_json(), encoding="utf-8")
    assert run(monkeypatch, "--input", path, "--resume", resume_path) == 0
    returned = parsed_stdout(capsys)
    assert returned.original_text == source.text and returned.new_query_count == 2
    assert [call.args[0] for call in offline.query.call_args_list] == ["익일", "공람"]


@pytest.mark.parametrize(
    "payload",
    [
        "{not-json " + SECRET,
        json.dumps({"text": "익일", "api_key": SECRET}),
        json.dumps({"text": "익일", "notice_id": 0}),
        "null",
        "[]",
    ],
)
def test_invalid_input_returns_generic_error_before_lookup_or_database(
    monkeypatch, capsys, tmp_path, offline, payload
):
    path = tmp_path / "bad.json"
    path.write_text(payload, encoding="utf-8")
    assert run(monkeypatch, "--input", path) == 2
    output = capsys.readouterr()
    assert output.out == "" and "입력 또는 설정" in output.err
    assert SECRET not in output.err
    offline.query.assert_not_called()
    offline.connect.assert_not_called()


def test_invalid_resume_is_rejected_before_lookup(
    monkeypatch, capsys, tmp_path, source_file, offline
):
    _, path = source_file
    resume_path = tmp_path / "bad-result.json"
    resume_path.write_text(json.dumps({"private": SECRET}), encoding="utf-8")
    assert run(monkeypatch, "--input", path, "--resume", resume_path) == 2
    output = capsys.readouterr()
    assert output.out == "" and SECRET not in output.err
    offline.query.assert_not_called()


def test_resume_of_old_rules_rebuilds_before_current_condition_validation(
    monkeypatch, capsys, tmp_path, source_file, offline
):
    source, path = source_file
    payload = process_notice_glossary(source, lookup_result, clock=lambda: NOW).model_dump(
        mode="json"
    )
    payload["rules_version"] = "dictionary-replacement-v3"
    condition = [{"type": "순화", "description": "노원구 주민에 한함"}]
    payload["terms"][0]["entries"][0]["norm_info"] = condition
    payload["queries"][0]["lookup"]["entries"][0]["norm_info"] = condition
    # A previous bad replacement is invalid under v4, but its generation must
    # be discarded without preventing the supplied current input from running.
    with pytest.raises(ValueError):
        NoticeGlossaryResult.model_validate(payload)
    resume_path = tmp_path / "old-v3.json"
    resume_path.write_text(json.dumps(payload), encoding="utf-8")
    assert run(monkeypatch, "--input", path, "--resume", resume_path) == 0
    returned = parsed_stdout(capsys)
    assert returned.rules_version == "dictionary-replacement-v6"
    assert returned.easy_text == "다음 날 공람"
    assert [call.args[0] for call in offline.query.call_args_list] == ["익일", "공람"]


def test_old_rules_resume_with_forged_source_hash_is_rejected_before_lookup(
    monkeypatch, capsys, tmp_path, source_file, offline
):
    source, path = source_file
    payload = process_notice_glossary(source, lookup_result, clock=lambda: NOW).model_dump(
        mode="json"
    )
    payload.update(rules_version="dictionary-replacement-v3", source_hash="a" * 64)
    resume_path = tmp_path / "forged-old.json"
    resume_path.write_text(json.dumps(payload), encoding="utf-8")
    assert run(monkeypatch, "--input", path, "--resume", resume_path) == 2
    assert capsys.readouterr().out == ""
    offline.query.assert_not_called()


@pytest.mark.parametrize("notice_id", [0, -1])
def test_invalid_notice_id_is_rejected_before_db_or_network(
    monkeypatch, capsys, offline, notice_id
):
    with pytest.raises(SystemExit) as caught:
        run(monkeypatch, "--notice-id", notice_id)
    assert caught.value.code == 2
    offline.connect.assert_not_called()
    offline.query.assert_not_called()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_query_limit_is_rejected_before_processing(
    monkeypatch, capsys, source_file, offline, limit
):
    _, path = source_file
    with pytest.raises(SystemExit) as caught:
        run(monkeypatch, "--input", path, "--max-queries", limit)
    assert caught.value.code == 2
    offline.connect.assert_not_called()
    offline.query.assert_not_called()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--input", "source.json", "--notice-id", "42"],
        ["--input", "source.json", "--refresh"],
        ["--input", "source.json", "--save", "--resume", "old.json"],
        ["--notice-id", "42", "--resume", "old.json"],
    ],
)
def test_invalid_mode_combinations_fail_before_io(monkeypatch, capsys, offline, arguments):
    with pytest.raises(SystemExit) as caught:
        run(monkeypatch, *arguments)
    assert caught.value.code == 2
    offline.connect.assert_not_called()
    offline.query.assert_not_called()
    assert capsys.readouterr().out == ""


def test_notice_id_mode_loads_existing_source_and_saves_on_command_owned_connection(
    monkeypatch, capsys, db_mode
):
    conn, mocks = db_mode
    source = NoticeGlossaryInput(notice_id=42, text="익일 공람")
    load = MagicMock(return_value=source)
    monkeypatch.setattr(module, "load_notice_glossary_input", load)
    assert run(monkeypatch, "--notice-id", 42, "--max-queries", 1, "--refresh") == 3
    returned = parsed_stdout(capsys)
    assert returned.status == "partial" and returned.notice_id == 42
    load.assert_called_once_with(conn, 42)
    mocks.save.assert_called_once_with(conn, source, max_queries=1, refresh=True)
    mocks.connect.assert_called_once_with(DATABASE_URL)
    conn.__enter__.assert_called_once()
    conn.__exit__.assert_called_once_with(None, None, None)
    mocks.gemini.assert_not_called()


def test_file_save_mode_passes_original_input_and_writes_matching_output(
    monkeypatch, capsys, tmp_path, source_file, db_mode
):
    conn, mocks = db_mode
    source, path = source_file
    output_path = tmp_path / "saved.json"
    load = MagicMock(side_effect=AssertionError("--input must use the supplied original"))
    monkeypatch.setattr(module, "load_notice_glossary_input", load)
    assert run(monkeypatch, "--input", path, "--save", "--output", output_path) == 0
    returned = parsed_stdout(capsys)
    assert NoticeGlossaryResult.model_validate_json(output_path.read_text("utf-8")) == returned
    mocks.save.assert_called_once_with(conn, source, max_queries=100, refresh=False)
    load.assert_not_called()


def test_missing_database_config_fails_before_connect_or_lookup(
    monkeypatch, capsys, source_file, offline
):
    _, path = source_file
    assert run(monkeypatch, "--input", path, "--save") == 2
    assert capsys.readouterr().out == ""
    offline.connect.assert_not_called()
    offline.query.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        psycopg.OperationalError(SECRET),
        GlossaryStorageError(SECRET),
        NoticeGlossaryStorageError(SECRET),
        RuntimeError(SECRET),
    ],
)
def test_database_errors_have_generic_output_and_leave_context_with_exception(
    monkeypatch, capsys, source_file, db_mode, error
):
    conn, mocks = db_mode
    _, path = source_file
    mocks.save.side_effect = error
    assert run(monkeypatch, "--input", path, "--save") == 1
    output = capsys.readouterr()
    assert output.out == "" and "실패했습니다" in output.err
    assert SECRET not in output.err and DATABASE_URL not in output.err
    assert conn.__exit__.call_args.args[0] is type(error)


def test_secret_bearing_config_error_is_never_printed(monkeypatch, capsys, source_file, offline):
    _, path = source_file
    monkeypatch.setattr(
        module.DatabaseSettings, "from_env", MagicMock(side_effect=ConfigError(SECRET))
    )
    assert run(monkeypatch, "--input", path, "--save") == 2
    output = capsys.readouterr()
    assert output.out == "" and SECRET not in output.err
    offline.connect.assert_not_called()


@pytest.mark.parametrize("kind", ["missing", "invalid_utf8"])
def test_unreadable_input_file_returns_generic_error(monkeypatch, capsys, tmp_path, offline, kind):
    path = tmp_path / "private-path.json"
    if kind == "invalid_utf8":
        path.write_bytes(b"\xff\xfe")
    assert run(monkeypatch, "--input", path) == 2
    output = capsys.readouterr()
    assert output.out == "" and output.err
    assert str(path) not in output.err
    offline.query.assert_not_called()


def test_output_failure_exits_database_context_with_error_so_command_can_rollback(
    monkeypatch, capsys, tmp_path, source_file, db_mode
):
    conn, _ = db_mode
    _, path = source_file
    # A directory cannot be written as a JSON file on either supported platform.
    assert run(monkeypatch, "--input", path, "--save", "--output", tmp_path) == 2
    output = capsys.readouterr()
    assert output.out == "" and "읽거나 저장" in output.err
    assert issubclass(conn.__exit__.call_args.args[0], OSError)
    conn.commit.assert_not_called()


def test_read_input_without_save_needs_no_notice_id(monkeypatch, capsys, tmp_path, offline):
    path = tmp_path / "text-only.json"
    path.write_text(NoticeGlossaryInput(text="익일").model_dump_json(), encoding="utf-8")
    assert run(monkeypatch, "--input", path) == 0
    assert parsed_stdout(capsys).notice_id is None
    offline.connect.assert_not_called()
