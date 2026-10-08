"""Guard disposable CI setup and reject reports which silently skip DB tests."""

import importlib.util
import re
import subprocess
from types import ModuleType
from unittest.mock import MagicMock

import pytest
from support.paths import PIPELINE_DIR, REPO_ROOT, TESTS_DIR


def _load_script(name: str) -> ModuleType:
    path = PIPELINE_DIR / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = _load_script("prepare_test_databases")
report = _load_script("check_test_report")


def test_seed_checkout_preserves_hash_literal_line_endings():
    seed = REPO_ROOT / "supabase" / "seed.sql"
    assert b"\r" not in seed.read_bytes(), "Seed hash literals must retain LF on Windows"
    result = subprocess.run(
        ["git", "check-attr", "eol", "--", "supabase/seed.sql"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "supabase/seed.sql: eol: lf"


@pytest.mark.parametrize("url", [
    "",
    "not-a-connection-string",
    "postgresql://postgres@database.example/postgres",
    "postgresql://postgres@localhost/postgres",
    "postgresql://postgres@127.0.0.1/production",
    "host=127.0.0.1 hostaddr=192.0.2.1 dbname=postgres",
    "host=127.0.0.1 dbname=postgres service=production",
])
def test_database_preparation_rejects_unsafe_targets_before_connect(monkeypatch, tmp_path, url):
    connect = MagicMock()
    monkeypatch.setattr(prepare.psycopg, "connect", connect)
    with pytest.raises(prepare.TestDatabasePreparationError):
        prepare.prepare_test_databases(url, migration_directory=tmp_path)
    connect.assert_not_called()


def test_connection_parameters_pin_loopback_and_short_timeout():
    parameters = prepare._connection_parameters("postgresql://postgres@127.0.0.1:61009/postgres")
    assert parameters["hostaddr"] == "127.0.0.1"
    assert parameters["connect_timeout"] == "5"
    assert parameters["port"] == "61009"


@pytest.mark.parametrize("existing_database", prepare.TEST_DATABASES)
def test_preparation_refuses_existing_database_without_resetting_it(
    monkeypatch, tmp_path, existing_database
):
    (tmp_path / "0001.sql").write_text("select 1", encoding="utf-8")
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = [(existing_database,)]
    monkeypatch.setattr(prepare.psycopg, "connect", lambda **kwargs: conn)
    with pytest.raises(prepare.TestDatabasePreparationError, match="test_database_already_exists"):
        prepare.prepare_test_databases(
            "postgresql://postgres@127.0.0.1/postgres", migration_directory=tmp_path,
        )
    assert conn.execute.call_count == 1
    assert conn.execute.call_args.args[0].startswith("select datname")
    assert conn.execute.call_args.args[1] == prepare.TEST_DATABASES


def test_preparation_migrates_only_pipeline_and_keeps_rollback_databases_empty(
    monkeypatch, tmp_path
):
    (tmp_path / "0002.sql").write_text("select 'second'", encoding="utf-8")
    (tmp_path / "0001.sql").write_text("select 'first'", encoding="utf-8")
    admin = MagicMock()
    admin.__enter__.return_value = admin
    admin.execute.return_value.fetchall.return_value = []
    migrated = MagicMock()
    migrated.__enter__.return_value = migrated
    connect = MagicMock(side_effect=[admin, migrated])
    roles = MagicMock()
    monkeypatch.setattr(prepare.psycopg, "connect", connect)
    monkeypatch.setattr(prepare, "_ensure_plain_roles", roles)

    assert prepare.prepare_test_databases(
        "postgresql://postgres@127.0.0.1/postgres", migration_directory=tmp_path,
    ) == 2

    roles.assert_called_once_with(admin)
    assert [call.args[0] for call in admin.execute.call_args_list[1:]] == [
        prepare.SQL("create database {}").format(prepare.Identifier(database))
        for database in prepare.TEST_DATABASES
    ]
    assert [call.kwargs["dbname"] for call in connect.call_args_list] == [
        "postgres", prepare.PIPELINE_DATABASE,
    ]
    assert [call.args[0] for call in migrated.execute.call_args_list] == [
        "grant usage on schema public to anon, authenticated, service_role",
        "select 'first'",
        "select 'second'",
    ]


@pytest.mark.parametrize("flags,membership", [
    ((False, True, False, False, False, False), (0,)),
    ((False, False, False, False, False, True), (0,)),
    ((False,) * 6, (1,)),
])
def test_existing_privileged_roles_cannot_mask_app_permission_failures(flags, membership):
    conn = MagicMock()
    conn.execute.return_value.fetchone.side_effect = [flags, membership]
    with pytest.raises(prepare.TestDatabasePreparationError, match="has_privileges"):
        prepare._ensure_plain_roles(conn)
    assert conn.execute.call_count == 2


def _xml(tmp_path, *, attributes="tests='1' skipped='0' failures='0' errors='0'", body=""):
    path = tmp_path / "test-report.xml"
    path.write_text(
        f"<testsuites><testsuite {attributes}><testcase name='sample'>{body}</testcase>"
        "</testsuite></testsuites>", encoding="utf-8",
    )
    return path


def test_executed_suite_without_skips_passes_report_check(tmp_path):
    assert report.check_test_report(_xml(tmp_path)) == 1


@pytest.mark.parametrize("attributes,body,reason", [
    ("tests='0' skipped='0' failures='0' errors='0'", "", "empty_test_report"),
    ("tests='1' skipped='1' failures='0' errors='0'", "", "skipped_tests"),
    ("tests='1' skipped='0' failures='0' errors='0'", "<skipped/>", "skipped_tests"),
    ("tests='1' skipped='0' failures='1' errors='0'", "", "failed_tests"),
    ("tests='1' skipped='0' failures='0' errors='0'", "<error/>", "failed_tests"),
    ("tests='-1' skipped='0' failures='0' errors='0'", "", "invalid_or_missing"),
    ("tests='1'", "", "invalid_or_missing"),
])
def test_skipped_failed_empty_and_invalid_reports_fail_ci(tmp_path, attributes, body, reason):
    with pytest.raises(report.TestReportError, match=reason):
        report.check_test_report(_xml(tmp_path, attributes=attributes, body=body))


def test_missing_and_malformed_reports_do_not_pass_ci(tmp_path):
    path = tmp_path / "missing.xml"
    with pytest.raises(report.TestReportError, match="invalid_or_missing"):
        report.check_test_report(path)
    path.write_text("<broken", encoding="utf-8")
    with pytest.raises(report.TestReportError, match="invalid_or_missing"):
        report.check_test_report(path)


def test_every_pipeline_test_file_is_inside_a_folder_that_ci_runs():
    # CI runs pytest per folder; a test placed elsewhere would never run and never be
    # counted by the skip check (issue #47). Compare ci.yml targets with every test file.
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    targets = [
        (PIPELINE_DIR / target).resolve()
        for target in re.findall(r"\bpytest\s+([^\s-]\S*)", workflow)
    ]
    assert targets, "no pytest targets found in ci.yml"
    not_run = sorted(
        str(path.relative_to(TESTS_DIR))
        for path in TESTS_DIR.rglob("test_*.py")
        if not any(path.resolve().is_relative_to(target) for target in targets)
    )
    assert not_run == [], f"test files outside CI pytest targets: {not_run}"


def test_raw_collection_is_independent_of_ai_credentials():
    workflow = (REPO_ROOT / ".github" / "workflows" / "collect.yml").read_text("utf-8")
    before_processing = workflow.split("      - name: Process nowon summary")[0]
    assert "GEMINI_API_KEY" not in before_processing
    assert "STDICT_API_KEY" not in before_processing
    assert "timeout-minutes: 360" in before_processing
    assert "cancel-in-progress: false" in before_processing


def test_all_raw_sources_precede_independently_bounded_features():
    workflow = (REPO_ROOT / ".github" / "workflows" / "collect.yml").read_text("utf-8")
    steps = re.split(r"(?m)^      - ", workflow)
    raw = [i for i, step in enumerate(steps) if "pipeline collect --source" in step]
    processing = [i for i, step in enumerate(steps) if "pipeline process-stored" in step]
    assert len(raw) == 3 and len(processing) == 6
    assert max(raw) < min(processing)
    seoul_step = next(steps[i] for i in raw if "--source seoul" in steps[i])
    assert "--source-board" not in seoul_step
    for i in raw:
        assert "--process-ai" not in steps[i] and "--easy-text" not in steps[i]
        assert "timeout-minutes: 10" in steps[i]
    for i in processing:
        step = steps[i]
        assert "timeout-minutes: 330" in step
        assert "continue-on-error: true" in step
        assert "!cancelled()" in step
        assert "--all" in step
        assert '[ -z "$GEMINI_API_KEY" ]' in step
        if "--feature easy_text" in step:
            assert '[ -z "$STDICT_API_KEY" ]' in step
        else:
            assert "--feature summary" in step
            assert "STDICT_API_KEY" not in step


@pytest.mark.parametrize("ai,easy,outcomes,exit_code", [
    ("false", "false", {"nowon": "success", "wolgye1": "success"}, 0),
    ("true", "false", {"nowon": "success", "wolgye1": "success"}, 1),
    ("false", "false", {"nowon": "failure", "wolgye1": "success"}, 1),
    ("false", "true", {"nowon": "success", "wolgye1": "success",
                       "easy_text_nowon": "success", "easy_text_wolgye1": "success"}, 0),
    ("true", "false", {"nowon": "success", "wolgye1": "success",
                       "summary_nowon": "failure", "summary_wolgye1": "success",
                       "easy_text_nowon": "success", "easy_text_wolgye1": "success"}, 1),
])
def test_workflow_report_does_not_hide_failed_or_unrun_features(
    monkeypatch, tmp_path, ai, easy, outcomes, exit_code,
):
    import json
    import textwrap

    workflow = (REPO_ROOT / ".github" / "workflows" / "collect.yml").read_text("utf-8")
    script = textwrap.dedent(workflow.split("python - <<'PY'\n")[1].rsplit("          PY", 1)[0])
    monkeypatch.setenv("STEP_RESULTS", json.dumps({k: {"outcome": v} for k, v in outcomes.items()}))
    monkeypatch.setenv("AI_ENABLED", ai)
    monkeypatch.setenv("EASY_ENABLED", easy)
    monkeypatch.setenv("SEOUL_ENABLED", "false")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    with pytest.raises(SystemExit) as result:
        exec(compile(script, "workflow_report", "exec"), {})
    assert result.value.code == exit_code
