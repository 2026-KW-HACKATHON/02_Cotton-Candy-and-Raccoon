"""Run every tests/e2e/cases/<name>/case.json through the real CLI and compare snapshots.

    uv run pytest tests/e2e                         # all cases and harness checks
    uv run pytest tests/e2e -k nowon_api_error      # one case
    E2E_UPDATE=1 uv run pytest tests/e2e -k <name>  # rewrite expected/ (refused in CI)
"""

import psycopg
import pytest
from e2e.harness import runner
from e2e.harness.gemini_replay import GeminiReplay
from e2e.harness.runner import CASES, CASES_DIR, run_case


@pytest.mark.parametrize("case_name", CASES)
def test_case(case_name: str, e2e_database: dict[str, str], monkeypatch: pytest.MonkeyPatch):
    if case_name.startswith("collect_ai_"):
        original = runner.run_step
        def checked_step(*args, **kwargs):
            result = original(*args, **kwargs)
            number = args[1]
            if number in (1, 2, 4, 5, 7, 9, 11):
                _assert_ai_result(number, result)
            return result
        monkeypatch.setattr(runner, "run_step", checked_step)
    if case_name.startswith("wolgye1_backfill"):
        original = runner.run_step
        results = []
        def checked_backfill(*args, **kwargs):
            result = original(*args, **kwargs)
            results.append(result)
            _assert_backfill(case_name, results)
            return result
        monkeypatch.setattr(runner, "run_step", checked_backfill)
    run_case(CASES_DIR / case_name, e2e_database, monkeypatch)


def _assert_ai_result(number, result):
    # Assertions independent of snapshots also run when updating expectations.
    report = result.report
    assert report["complete"] is True  # Raw collection survived every AI error.
    detail, = result.raw_anon["app_notice_detail"].values()
    assert detail["url"].startswith("https://") and detail["body_text"]
    assert len(result.raw_anon["app_notice_list"]) == 1
    if number in (4, 5):
        assert report["summary"]["complete"] is False
        assert report["easy_text"]["complete"] is True
        assert detail["has_easy_text"] and detail["easy_text"]
        assert detail["display_status"] == "needs_review"
        assert report["summary"]["readiness"]["retry_wait"] == 1
    elif number == 9:
        assert report["summary"]["complete"] is True
        assert report["easy_text"]["complete"] is False
        assert detail["card_summaries"] and not detail["has_easy_text"]
        assert detail["display_status"] == "summarized"
    else:
        assert all(report[f]["complete"] for f in ("summary", "easy_text"))
        assert detail["has_easy_text"] and len(detail["card_summaries"]) == 4
    if number in (2, 5):
        assert report["summary"]["attempted_count"] == 0
        assert report["easy_text"]["attempted_count"] == 0
        assert not any(result.gemini.calls.values())
    if number == 7:
        assert report["summary"]["attempted_count"] == 1
        assert report["easy_text"]["attempted_count"] == 0
    if number == 11:
        assert report["summary"]["attempted_count"] == 0
        assert report["easy_text"]["attempted_count"] == 1


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
    assert checked and set(checked) == {"easy_text"}
    # #60 may retry an individual provider request; every request sees both commits.


def _assert_backfill(case_name, results):
    result = results[-1]
    report = result.report
    if case_name == "wolgye1_backfill_preserve_ai":
        if len(results) == 2:
            assert report["delta"] == {"stored": 0, "public": 0}
            for table in ("notice_summaries", "notice_easy_texts"):
                assert result.raw_db[table] == results[0].raw_db[table]
            detail, = result.raw_anon["app_notice_detail"].values()
            assert detail["has_easy_text"] and len(detail["card_summaries"]) == 4
    elif case_name == "wolgye1_backfill_page_failure":
        assert report["failed_pages"] == [2]
        assert report["pages_read"] == [1, 3]
        assert report["saved_count"] == report["after"]["public"] == 3
        assert report["scope_complete"] is False
    else:
        number = len(results)
        expected_after = [0, 1, 3, 3, 3][number - 1]
        expected_delta = [0, 1, 2, 0, 0][number - 1]
        assert report["after"] == {"stored": expected_after, "public": expected_after}
        assert report["delta"] == {"stored": expected_delta, "public": expected_delta}
        assert len(result.raw_anon["app_notice_list"]) == expected_after
        if number == 1:
            assert report["selected_count"] == 3 and report["saved_count"] == 0
        if number == 5:
            assert report["limited"] and report["scope_complete"] is False
