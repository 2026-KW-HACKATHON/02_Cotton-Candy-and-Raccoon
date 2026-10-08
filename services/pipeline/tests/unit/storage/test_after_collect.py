"""Post-commit easy-text processing is isolated, bounded and safe to retry."""

import json
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

import psycopg
import pytest
from support.easy_rewrite import rewrite_response

from pipeline.after_collect import AfterCollectEasyText
from pipeline.collection_processing import CollectionPostprocessing, create_easy_text_processing
from pipeline.config import DatabaseSettings
from pipeline.gemini_execution import GeminiExecutionError
from pipeline.glossary.easy_language import (
    EasyLanguageAPIError,
    EasyLanguageConfigurationError,
    EasyLanguageValidationError,
    NoNoticeBodyError,
    simplify_notice,
)
from pipeline.glossary.source import StoredNoticeInput
from pipeline.storage.notice_easy_text import EasyTextStorageError
from pipeline.transform.gemini_prompt import GeminiConfigurationError


def _source(notice_id: int = 42, *, body_present: bool = True) -> StoredNoticeInput:
    return StoredNoticeInput(
        notice_id=notice_id,
        notice_revision="a" * 64,
        title="제목",
        text="제목\n익일 방문하세요." if body_present else "제목",
        body_text_present=body_present,
    )


@pytest.fixture
def processor() -> AfterCollectEasyText:
    return AfterCollectEasyText(DatabaseSettings("postgresql://user:secret@localhost/test"))


@pytest.fixture
def conn() -> MagicMock:
    connection = MagicMock()
    connection.__enter__.return_value = connection
    return connection


@pytest.fixture(autouse=True)
def dictionary(monkeypatch):
    lookup = MagicMock(return_value={"dictionary_status": "complete"})
    monkeypatch.setattr("pipeline.after_collect.enrich_notice_dictionary", lookup)
    return lookup


def test_separate_transaction_reuses_current_cache_without_forced_refresh(
    processor: AfterCollectEasyText,
    conn: MagicMock,
) -> None:
    source = _source()
    with (
        patch("pipeline.after_collect.psycopg.connect", return_value=conn) as connect,
        patch("pipeline.after_collect.load_notice_glossary_input", return_value=source) as load,
        patch("pipeline.after_collect.simplify_and_store_notice") as simplify,
    ):
        processor(42)
    connect.assert_called_once_with(
        processor.database.database_url,
        connect_timeout=5,
        autocommit=False,
    )
    load.assert_called_once_with(conn, 42)
    simplify.assert_called_once_with(conn, source, refresh=False)
    conn.__exit__.assert_called_once_with(None, None, None)
    assert processor.report() == {
        "status": "complete",
        "complete": True,
        "attempted_count": 1,
        "successful_count": 1,
        "gemini_requests": 0,
        "gemini_http_attempts": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "skipped": [],
        "failures": [],
        "dictionary": {
            "attempted_count": 1, "complete_count": 1, "partial_count": 0,
            "pending_count": 0, "failures": [],
        },
    }


@pytest.mark.parametrize("raced", [False, True])
def test_bodyless_notice_is_skipped_without_api_work(
    processor: AfterCollectEasyText,
    conn: MagicMock,
    raced: bool,
    dictionary,
) -> None:
    with (
        patch("pipeline.after_collect.psycopg.connect", return_value=conn),
        patch(
            "pipeline.after_collect.load_notice_glossary_input",
            return_value=_source(body_present=raced),
        ),
        patch(
            "pipeline.after_collect.simplify_and_store_notice",
            side_effect=NoNoticeBodyError("no body"),
        ) as simplify,
    ):
        processor(42)
    assert simplify.call_count == int(raced)
    report = processor.report()
    assert report["complete"] is True
    assert report["successful_count"] == report["failed_count"] == 0
    assert report["skipped_count"] == 1
    assert report["skipped"] == [{"notice_id": 42, "reason_code": "no_body_text"}]
    dictionary.assert_not_called()


@pytest.mark.parametrize(
    "error,reason",
    [
        (EasyLanguageAPIError("private body/key"), "api_failed"),
        (EasyLanguageValidationError("private body/key"), "invalid_response"),
        (EasyLanguageConfigurationError("private body/key"), "configuration_error"),
        (GeminiConfigurationError("private body/key"), "configuration_error"),
        (OSError("private body/key"), "configuration_error"),
        (EasyTextStorageError("private body/key"), "db_processing_failed"),
        (psycopg.OperationalError("private body/key"), "db_processing_failed"),
        (ValueError("private body/key"), "invalid_notice"),
        (RuntimeError("private body/key"), "processing_failed"),
    ],
)
def test_processing_failure_is_fixed_and_later_notice_still_runs(
    processor: AfterCollectEasyText,
    conn: MagicMock,
    error: Exception,
    reason: str,
) -> None:
    with (
        patch("pipeline.after_collect.psycopg.connect", return_value=conn),
        patch("pipeline.after_collect.load_notice_glossary_input", return_value=_source()),
        patch("pipeline.after_collect.simplify_and_store_notice", side_effect=[error, None]),
    ):
        processor(42)
        processor(43)
    report = processor.report()
    assert report["complete"] is False
    assert report["attempted_count"] == 2
    assert report["successful_count"] == report["failed_count"] == 1
    expected_failure = {"notice_id": 42, "reason_code": reason}
    if isinstance(error, EasyLanguageAPIError):
        expected_failure["execution_failure"] = error.to_dict()
    elif isinstance(error, EasyLanguageValidationError):
        expected_failure["execution_failure"] = GeminiExecutionError(
            "response_validation_failed"
        ).to_dict()
    assert report["failures"] == [expected_failure]
    assert "private" not in json.dumps(report)
    assert "secret" not in json.dumps(report)


def test_connection_failure_is_reported_for_saved_notice(processor: AfterCollectEasyText) -> None:
    with patch("pipeline.after_collect.psycopg.connect", side_effect=psycopg.OperationalError):
        processor(42)
    assert processor.report()["failures"] == [
        {"notice_id": 42, "reason_code": "db_processing_failed"},
    ]


@pytest.mark.parametrize("body_present", [False, True])
def test_commit_failure_does_not_claim_success_or_skip(
    processor: AfterCollectEasyText,
    conn: MagicMock,
    body_present: bool,
) -> None:
    conn.__exit__.side_effect = psycopg.OperationalError("commit failed")
    with (
        patch("pipeline.after_collect.psycopg.connect", return_value=conn),
        patch(
            "pipeline.after_collect.load_notice_glossary_input",
            return_value=_source(body_present=body_present),
        ),
        patch("pipeline.after_collect.simplify_and_store_notice"),
    ):
        processor(42)
    assert processor.report()["successful_count"] == 0
    assert processor.report()["skipped_count"] == 0
    assert processor.report()["failed_count"] == 1


def test_collection_callback_defers_work_and_deduplicates_committed_ids() -> None:
    runner = MagicMock(return_value={"easy_text": {"complete": True}})
    processor = CollectionPostprocessing(("easy_text",), runner)
    for notice_id in (42, 43, 42):
        processor(notice_id)
    runner.assert_not_called()
    assert processor.complete is False
    with pytest.raises(RuntimeError, match="not_finished"):
        processor.report()
    processor.finish()
    processor.finish()
    runner.assert_called_once_with((42, 43))
    assert processor.complete is True
    with pytest.raises(RuntimeError, match="already_finished"):
        processor(44)


def test_empty_collection_still_gives_common_runner_a_chance_to_recover_due_work() -> None:
    runner = MagicMock(return_value={"summary": {"complete": True, "successful_count": 2}})
    processor = CollectionPostprocessing(("summary",), runner)
    processor.finish()
    runner.assert_called_once_with(())
    assert processor.report()["summary"]["successful_count"] == 2


@pytest.mark.parametrize("failed_feature", ["summary", "easy_text"])
def test_feature_failure_does_not_hide_the_other_features_report(failed_feature: str) -> None:
    reports = {
        feature: {"complete": feature != failed_feature, "failures": []}
        for feature in ("summary", "easy_text")
    }
    processor = CollectionPostprocessing(("summary", "easy_text"), lambda _: reports)
    processor(42)
    processor.finish()
    assert processor.complete is False
    assert processor.report() == reports
    # Neither the runner nor a caller can mutate the captured result afterwards.
    reports["summary"]["failures"].append("changed")
    processor.report()["easy_text"]["failures"].append("changed")
    assert all(report["failures"] == [] for report in processor.report().values())


@pytest.mark.parametrize("bad_report", [
    {}, {"summary": {"complete": True}},
    {"summary": {"complete": True}, "easy_text": {"complete": "false"}},
])
def test_missing_or_invalid_feature_result_cannot_claim_success(bad_report: dict) -> None:
    processor = CollectionPostprocessing(("summary", "easy_text"), lambda _: bad_report)
    processor.finish()
    assert not processor.complete
    assert all(
        item["reason_code"] == "postprocessing_failed" for item in processor.report().values()
    )


def test_failed_runner_is_not_repeated_or_exposed_as_a_collection_failure() -> None:
    runner = MagicMock(side_effect=RuntimeError("private body password"))
    processor = CollectionPostprocessing(("summary", "easy_text"), runner)
    processor(42)
    processor.finish()
    processor.finish()
    runner.assert_called_once()
    assert not processor.complete
    assert "private" not in json.dumps(processor.report())


@pytest.mark.parametrize("notice_id", [True, 0, -1, 2**63, "42"])
def test_invalid_notice_id_never_reaches_runner(notice_id: object) -> None:
    runner = MagicMock()
    processor = CollectionPostprocessing(("easy_text",), runner)
    with pytest.raises(ValueError, match="invalid_notice_id"):
        processor(notice_id)
    runner.assert_not_called()


def test_deferred_easy_text_keeps_existing_report_and_processes_later_notices() -> None:
    legacy = MagicMock()
    legacy.report.return_value = {"complete": False, "failed_count": 1, "successful_count": 1}
    with patch("pipeline.collection_processing.AfterCollectEasyText", return_value=legacy):
        deferred = create_easy_text_processing(DatabaseSettings("postgresql://localhost/test"))
        deferred(42)
        deferred(43)
        legacy.assert_not_called()
        deferred.finish()
    assert [call.args for call in legacy.call_args_list] == [(42,), (43,)]
    assert deferred.report() == {"easy_text": legacy.report.return_value}
    assert not deferred.complete

@pytest.mark.parametrize("shared", [False, True])
def test_counters_are_per_notice_deltas_even_with_a_shared_budget(
    processor, conn, monkeypatch, shared
):
    from pipeline import gemini_execution

    def worker(operation, payload, budget):
        gemini_execution.record_http_dispatch()
        return rewrite_response(payload["notice_text"])

    def process(connection, source, **kwargs):
        return simplify_notice(source, api_key="fake")

    monkeypatch.setattr(gemini_execution, "_invoke_worker", worker)
    with (
        patch("pipeline.after_collect.psycopg.connect", return_value=conn),
        patch("pipeline.after_collect.load_notice_glossary_input", return_value=_source()),
        patch("pipeline.after_collect.simplify_and_store_notice", side_effect=process),
        gemini_execution.execution_budget() if shared else nullcontext(),
    ):
        processor(42)
        processor(43)
    report = processor.report()
    assert report["successful_count"] == 2
    assert report["gemini_requests"] == report["gemini_http_attempts"] == 2


def test_cache_read_time_does_not_exhaust_the_default_ai_budget(processor, conn, monkeypatch):
    from pipeline import gemini_execution

    now = [0.0]
    monkeypatch.setattr(gemini_execution.time, "monotonic", lambda: now[0])

    def worker(operation, payload, budget):
        assert budget.remaining_seconds() == 120
        gemini_execution.record_http_dispatch()
        return rewrite_response(payload["notice_text"])

    def process(connection, source, **kwargs):
        now[0] += 180.0  # Simulate the service's cache read before it starts conversion.
        return simplify_notice(source, api_key="fake")

    monkeypatch.setattr(gemini_execution, "_invoke_worker", worker)
    with (
        patch("pipeline.after_collect.psycopg.connect", return_value=conn),
        patch("pipeline.after_collect.load_notice_glossary_input", return_value=_source()),
        patch("pipeline.after_collect.simplify_and_store_notice", side_effect=process),
    ):
        processor(42)
    assert processor.report()["successful_count"] == 1
    assert processor.report()["gemini_http_attempts"] == 1


@pytest.mark.parametrize("status", ["partial", "pending"])
def test_dictionary_failure_retains_conversion_success_and_later_notice_runs(
    processor, conn, dictionary, status,
):
    dictionary.side_effect = [{"dictionary_status": status}, {"dictionary_status": "complete"}]
    with (
        patch("pipeline.after_collect.psycopg.connect", return_value=conn),
        patch("pipeline.after_collect.load_notice_glossary_input", return_value=_source()),
        patch("pipeline.after_collect.simplify_and_store_notice"),
    ):
        processor(42)
        processor(43)
    report = processor.report()
    assert report["successful_count"] == 2 and report["failed_count"] == 0
    assert report["complete"] is False
    assert report["dictionary"]["complete_count"] == 1
    assert report["dictionary"]["failures"] == [
        {"notice_id": 42, "reason_code": "dictionary_incomplete"},
    ]


def test_dictionary_starts_after_easy_text_connection_closes_and_masks_errors(
    processor, conn, dictionary,
):
    def fail(*args):
        conn.__exit__.assert_called_once_with(None, None, None)
        raise RuntimeError("private-api-key")

    dictionary.side_effect = fail
    with (
        patch("pipeline.after_collect.psycopg.connect", return_value=conn),
        patch("pipeline.after_collect.load_notice_glossary_input", return_value=_source()),
        patch("pipeline.after_collect.simplify_and_store_notice"),
    ):
        processor(42)
    report = processor.report()
    assert report["successful_count"] == 1 and report["complete"] is False
    assert "private" not in json.dumps(report)
    assert report["dictionary"]["failures"][0]["reason_code"] == "dictionary_processing_failed"
