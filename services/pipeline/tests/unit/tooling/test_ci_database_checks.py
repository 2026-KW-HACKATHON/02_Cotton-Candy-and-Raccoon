"""Guard disposable CI setup and reject reports which silently skip DB tests."""

import importlib.util
import re
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
