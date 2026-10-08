"""Run every tests/e2e/cases/<name>/case.json through the real CLI and compare snapshots.

    uv run pytest tests/e2e                         # all cases and harness checks
    uv run pytest tests/e2e -k nowon_api_error      # one case
    E2E_UPDATE=1 uv run pytest tests/e2e -k <name>  # rewrite expected/ (refused in CI)
"""

import psycopg
import pytest
from e2e.harness.gemini_replay import GeminiReplay
from e2e.harness.runner import CASES, CASES_DIR, run_case


@pytest.mark.parametrize("case_name", CASES)
def test_case(case_name: str, e2e_database: dict[str, str], monkeypatch: pytest.MonkeyPatch):
    run_case(CASES_DIR / case_name, e2e_database, monkeypatch)


@pytest.mark.parametrize("case_name", ["easy_text_api_failure", "wolgye1_easy_text"])
def test_all_raw_notices_are_committed_before_first_ai_request(
    case_name: str, e2e_database: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_next = GeminiReplay.next
    checked = []

    def checked_next(replay, kind, request):
        # Each case has three raw notices, one of which fails to save. A separate
        # connection must see both successful commits even at the FIRST AI call.
        with psycopg.connect(**e2e_database, autocommit=True) as conn:
            assert conn.execute("select count(*) from notices").fetchone() == (2,)
        checked.append(kind)
        return original_next(replay, kind, request)

    monkeypatch.setattr(GeminiReplay, "next", checked_next)
    run_case(CASES_DIR / case_name, e2e_database, monkeypatch)
    assert checked == ["easy_text", "easy_text"]
