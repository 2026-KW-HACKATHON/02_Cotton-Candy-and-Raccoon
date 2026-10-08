"""Run one e2e case: environment, steps, expectation compare or update, report."""

import json
import os
from pathlib import Path

import psycopg
import pytest
from e2e.harness.report import write_report
from e2e.harness.snapshot import diff
from e2e.harness.steps import (
    StepResult,
    configure_environment,
    credential_patterns,
    load_case,
    run_step,
)
from support.paths import PIPELINE_DIR, TESTS_DIR

CASES_DIR = TESTS_DIR / "e2e" / "cases"
REPORTS_DIR = PIPELINE_DIR / "artifacts" / "e2e"
CASES = sorted(path.name for path in CASES_DIR.iterdir() if (path / "case.json").is_file())


def update_requested() -> bool:
    if os.getenv("E2E_UPDATE") != "1":
        return False
    if os.getenv("CI"):
        pytest.fail("E2E_UPDATE는 CI에서 사용할 수 없습니다. 기대값은 로컬에서 갱신해 커밋하세요.")
    return True


def _canonical(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def run_case(
    case_dir: Path,
    database: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    reports_dir: Path = REPORTS_DIR,
) -> None:
    case = load_case(case_dir)
    update = update_requested()
    configure_environment(monkeypatch, database)
    secrets = credential_patterns(database)
    results: list[StepResult] = []
    failures: list[str] = []
    status = "실행 중단"
    try:
        with psycopg.connect(**database, autocommit=True) as conn:
            previous = None
            for number, step in enumerate(case.steps, 1):
                result = run_step(case, number, step, conn, monkeypatch, previous, secrets)
                results.append(result)
                expected_path = case_dir / "expected" / f"step-{number}.json"
                actual = _canonical(result.snapshot)
                if update:
                    expected_path.parent.mkdir(exist_ok=True)
                    expected_path.write_text(
                        json.dumps(actual, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                        newline="\n",
                    )
                elif not expected_path.is_file():
                    result.differences.append(
                        f"{expected_path.relative_to(case_dir)} 없음: "
                        "E2E_UPDATE=1로 생성한 뒤 내용을 확인하세요"
                    )
                else:
                    expected = json.loads(expected_path.read_text(encoding="utf-8"))
                    result.differences.extend(diff(expected, actual))
                failures.extend(f"step {number}: {line}" for line in result.problems)
                failures.extend(f"step {number}: {line}" for line in result.differences)
                previous = result
        if update:
            for stale in (case_dir / "expected").glob("step-*.json"):
                if int(stale.stem.removeprefix("step-")) > len(case.steps):
                    stale.unlink()
        status = "실패" if failures else ("기대값 갱신" if update else "통과")
    finally:
        write_report(reports_dir / f"{case.name}.md", case, results, status)
    if failures:
        pytest.fail(
            f"{case.name}: {len(failures)}건 불일치 (보고서: artifacts/e2e/{case.name}.md)\n"
            + "\n".join(failures),
            pytrace=False,
        )

