"""Run every tests/e2e/cases/<name>/case.json through the real CLI and compare snapshots.

    uv run pytest tests/e2e                         # all cases and harness checks
    uv run pytest tests/e2e -k nowon_api_error      # one case
    E2E_UPDATE=1 uv run pytest tests/e2e -k <name>  # rewrite expected/ (refused in CI)
"""

import pytest
from e2e.harness.runner import CASES, CASES_DIR, run_case


@pytest.mark.parametrize("case_name", CASES)
def test_case(case_name: str, e2e_database: dict[str, str], monkeypatch: pytest.MonkeyPatch):
    run_case(CASES_DIR / case_name, e2e_database, monkeypatch)
