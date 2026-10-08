"""Guard the e2e harness itself: failures must stay failures and replays must stay faithful."""

import json
import shutil
from pathlib import Path

import pytest
from e2e.harness import gemini_replay, scaffold
from e2e.harness.http_replay import API_KEY
from e2e.harness.runner import CASES_DIR, run_case, update_requested
from e2e.harness.steps import database_uri
from support.db import owned_migrated_database

from pipeline.glossary.easy_language import EasyLanguageAPIError
from pipeline.glossary.easy_language_client import generate_easy_language_json
from pipeline.transform.gemini_client import GeminiRequestError, generate_summary_json

BASE_CASE = "nowon_new_text_only"


def _fresh_database():
    return owned_migrated_database(
        env="E2E_TEST_DATABASE_URL",
        required_prefix="pipeline_e2e_test_",
        missing="fail",
        missing_message="E2E_TEST_DATABASE_URL 미설정",
        invalid_message="e2e 전용 로컬 DB가 필요합니다.",
    )


def _copy_case(tmp_path: Path, name: str = BASE_CASE) -> Path:
    target = tmp_path / name
    shutil.copytree(CASES_DIR / name, target)
    return target


def test_unregistered_request_fails_the_case(tmp_path, e2e_database, monkeypatch):
    case_dir = _copy_case(tmp_path)
    definition = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    definition["steps"][0]["http"] = [
        route for route in definition["steps"][0]["http"] if route["route"] != "nowon_page"
    ]
    (case_dir / "case.json").write_text(json.dumps(definition), encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="Unexpected external URL: GET https://www"):
        run_case(case_dir, e2e_database, monkeypatch, reports_dir=tmp_path / "reports")


def test_changed_expectation_is_reported_by_table_row_and_column(
    tmp_path, e2e_database, monkeypatch
):
    case_dir = _copy_case(tmp_path)
    path = case_dir / "expected" / "step-1.json"
    expected = json.loads(path.read_text(encoding="utf-8"))
    expected["db"]["notices"]["nowon/1001/900101"]["title"] = "다른 제목"
    path.write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(pytest.fail.Exception) as failure:
        run_case(case_dir, e2e_database, monkeypatch, reports_dir=tmp_path / "reports")
    assert 'db["notices"]["nowon/1001/900101"]["title"]: expected "다른 제목"' in str(
        failure.value
    )


def test_same_case_gives_the_same_result_twice(tmp_path, monkeypatch):
    for _ in range(2):
        with _fresh_database() as database, monkeypatch.context() as patch:
            run_case(CASES_DIR / BASE_CASE, database, patch, reports_dir=tmp_path / "reports")


def test_report_is_written_without_credentials(tmp_path, e2e_database, monkeypatch):
    run_case(CASES_DIR / BASE_CASE, e2e_database, monkeypatch, reports_dir=tmp_path)
    text = (tmp_path / f"{BASE_CASE}.md").read_text(encoding="utf-8")
    assert "## step 1:" in text and "nowon_page 900101" in text
    assert API_KEY not in text
    assert database_uri(e2e_database) not in text
    assert "postgresql://" not in text


def test_update_mode_is_refused_in_ci(monkeypatch):
    monkeypatch.setenv("E2E_UPDATE", "1")
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception, match="CI"):
        update_requested()
    monkeypatch.delenv("CI")
    assert update_requested() is True


def test_missing_database_setting_fails_instead_of_skipping(monkeypatch):
    monkeypatch.delenv("E2E_TEST_DATABASE_URL", raising=False)
    with pytest.raises(pytest.fail.Exception, match="E2E_TEST_DATABASE_URL"):
        with _fresh_database():
            pass


def _install(monkeypatch, tmp_path, kind: str, *payloads: object) -> gemini_replay.GeminiReplay:
    names = []
    for index, payload in enumerate(payloads):
        name = f"{kind}_{index}.json"
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
        names.append(name)
    replay = gemini_replay.GeminiReplay.from_spec({kind: names}, tmp_path)
    gemini_replay.install(monkeypatch, replay)
    return replay


def test_easy_text_replay_matches_the_real_client(monkeypatch, tmp_path):
    replay = _install(monkeypatch, tmp_path, "easy_text", {"changes": []}, {"__error__": "timeout"})
    output = generate_easy_language_json(prompt="p", notice_text="본문", api_key=API_KEY)
    assert json.loads(output) == {"changes": []}
    with pytest.raises(EasyLanguageAPIError):
        generate_easy_language_json(prompt="p", notice_text="본문", api_key=API_KEY)
    assert [call["input"][0]["type"] for call in replay.calls["easy_text"]] == ["text", "text"]
    assert replay.leftovers() == {}


@pytest.mark.parametrize(
    ("error", "reason_code"),
    [
        ("timeout", "api_timeout"),
        ("connection", "api_connection_error"),
        ("api_error", "api_error"),
    ],
)
def test_summary_replay_errors_follow_the_real_client_classification(
    monkeypatch, tmp_path, error, reason_code
):
    _install(monkeypatch, tmp_path, "summary", {"summary": "ok"}, {"__error__": error})
    output = generate_summary_json(prompt="p", notice_text="본문", api_key=API_KEY)
    assert json.loads(output) == {"summary": "ok"}
    with pytest.raises(GeminiRequestError) as failure:
        generate_summary_json(prompt="p", notice_text="본문", api_key=API_KEY)
    assert failure.value.reason_code == reason_code


def test_gemini_call_without_a_queued_response_fails(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, "summary")
    with pytest.raises(gemini_replay.GeminiQueueError):
        generate_summary_json(prompt="p", notice_text="본문", api_key=API_KEY)


def test_scaffold_registers_the_same_routes_as_a_hand_written_case(tmp_path, capsys):
    source = CASES_DIR / BASE_CASE / "input"
    assert scaffold.scaffold([
        "--name", "draft", "--source", "nowon", "--cases-dir", str(tmp_path),
        "--api", str(source / "nowon_list.xml"), "--page", str(source / "nowon_page_900101.html"),
    ]) == 0
    draft = json.loads((tmp_path / "draft" / "case.json").read_text(encoding="utf-8"))
    written = json.loads((CASES_DIR / BASE_CASE / "case.json").read_text(encoding="utf-8"))
    assert draft["steps"][0]["http"] == written["steps"][0]["http"]
    assert draft["steps"][0]["args"] == written["steps"][0]["args"]
    assert "TODO: case.json의 title" in capsys.readouterr().out


def test_scaffold_warns_about_masked_names_and_phone_numbers(tmp_path, capsys):
    source = CASES_DIR / BASE_CASE / "input"
    api = tmp_path / "list.xml"
    api.write_text(
        (source / "nowon_list.xml").read_text(encoding="utf-8").replace(
            "문의: 보건정책과", "문의: 담당자 이0진 010-1234-5678"
        ),
        encoding="utf-8",
    )
    scaffold.scaffold(["--name", "masked", "--source", "nowon", "--cases-dir", str(tmp_path),
                       "--api", str(api)])
    err = capsys.readouterr().err
    assert "이0진" in err and "010-1234-5678" in err
