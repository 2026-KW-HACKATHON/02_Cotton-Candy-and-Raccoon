"""Collection adapter failure isolation and committed app visibility."""

import json
from contextlib import nullcontext
from unittest.mock import MagicMock, Mock, patch

import pytest

from pipeline import collection_processing as adapter
from pipeline.config import DatabaseSettings
from pipeline.processing_runner import ProcessingRunResult


@pytest.mark.parametrize("failed_feature", ["summary", "easy_text"])
def test_adapter_still_runs_other_feature_and_reads_final_public_state(monkeypatch, failed_feature):
    calls = []
    def run(database, **kwargs):
        feature, = kwargs["features"]
        calls.append(feature)
        assert kwargs["source"] == "dong" and kwargs["limit"] == 7
        if feature == failed_feature:
            raise RuntimeError("secret must not leak")
        return ProcessingRunResult(False, 0, 0, ())
    monkeypatch.setattr(adapter, "load_gemini_api_key", lambda: "fake")
    monkeypatch.setattr(adapter, "run_processing", run)
    monkeypatch.setattr(adapter.psycopg, "connect", lambda *a, **kw: nullcontext(object()))
    monkeypatch.setattr(adapter, "readiness_counts", lambda *a, **kw: {"ready": 1, **dict.fromkeys(
        ("pending", "running", "retry_wait", "blocked", "exhausted"), 0,
    )})
    published = [{"id": 42, "has_easy_text": True, "display_status": "summarized", "url": "url"}]
    reader = Mock(return_value=published)
    monkeypatch.setattr(adapter, "read_published", reader)
    monkeypatch.setattr(adapter, "dictionary_work", lambda *a, **kw: ([], 0))
    monkeypatch.setattr(
        adapter, "enrich_notice_dictionary", lambda *a: {"dictionary_status": "complete"},
    )
    processor = adapter.create_ai_processing(DatabaseSettings("unused"), source="wolgye1", limit=7)
    processor(42)
    assert not calls
    processor.finish()
    assert calls == ["summary", "easy_text"]
    reports = processor.report()
    assert reports[failed_feature]["complete"] is False
    assert reports["easy_text" if failed_feature == "summary" else "summary"]["complete"] is True
    assert "secret" not in str(reports)
    assert all(r["published"] == published for r in reports.values())
    reader.assert_called_with(DatabaseSettings("unused"), [42])


@pytest.mark.parametrize("remaining", ["pending", "running", "retry_wait", "blocked", "exhausted"])
def test_empty_batch_does_not_claim_deferred_or_stopped_work_is_ready(monkeypatch, remaining):
    monkeypatch.setattr(adapter, "load_gemini_api_key", lambda: "fake")
    monkeypatch.setattr(
        adapter, "run_processing", lambda *a, **kw: ProcessingRunResult(False, 0, 0, ()),
    )
    monkeypatch.setattr(adapter.psycopg, "connect", lambda *a, **kw: nullcontext(object()))
    counts = dict.fromkeys(("pending", "running", "retry_wait", "blocked", "exhausted"), 0)
    counts[remaining] = 1
    monkeypatch.setattr(adapter, "readiness_counts", lambda *a, **kw: counts)
    monkeypatch.setattr(adapter, "read_published", lambda *a: [])
    monkeypatch.setattr(adapter, "dictionary_work", lambda *a, **kw: ([], 0))
    processor = adapter.create_ai_processing(DatabaseSettings("unused"), source="seoul")
    processor.finish()  # No new raw IDs still scans the source's old work.
    assert not processor.complete
    assert all(r["readiness"][remaining] == 1 for r in processor.report().values())


@pytest.mark.parametrize("publication_fails", [False, True])
def test_ai_recovers_dictionary_without_new_gemini_jobs_and_keeps_reports(publication_fails):
    import pipeline.collection_processing as processing
    from pipeline.processing_runner import ProcessingRunResult

    ready = dict.fromkeys(
        ("pending", "running", "retry_wait", "blocked", "exhausted", "skipped"), 0,
    )
    ready["ready"] = 1
    result = ProcessingRunResult(False, 0, 0, ())
    with (
        patch.object(processing, "load_gemini_api_key", return_value="test-key"),
        patch.object(processing, "execution_budget", return_value=nullcontext()),
        patch.object(processing, "run_processing", return_value=result) as runner,
        patch.object(processing, "_report_connection", return_value=MagicMock()),
        patch.object(processing, "readiness_counts", return_value=ready),
        patch.object(processing, "dictionary_work", side_effect=[([42], 1), ([], 0)]),
        patch.object(processing, "enrich_notice_dictionary", return_value={
            "dictionary_status": "complete",
        }) as dictionary,
        patch.object(processing, "read_published", return_value=[],
                     side_effect=RuntimeError("private") if publication_fails else None),
    ):
        processor = processing.create_ai_processing(DatabaseSettings("postgresql://localhost/test"),
                                                    source="nowon")
        processor.finish()  # No newly collected IDs and no new Gemini jobs.
    dictionary.assert_called_once()
    assert dictionary.call_args.args[1] == 42
    assert runner.call_count == 2
    reports = processor.report()
    for report in reports.values():
        assert report["attempted_count"] == 0
        assert report["records"] == []
        assert report["complete"] is (not publication_fails)
        if publication_fails:
            assert report["published"] is None
            assert report["published_error_code"] == "published_read_failed"
    assert reports["easy_text"]["dictionary"] == [{"notice_id": 42, "status": "complete"}]
    assert reports["easy_text"]["dictionary_remaining_count"] == 0


def test_readiness_failure_retains_successful_execution_and_runs_other_feature():
    import pipeline.collection_processing as processing
    from pipeline.processing_runner import ProcessingRunResult

    result = ProcessingRunResult(False, 1, 1, ({
        "notice_id": 42, "state": "succeeded", "feature": "summary",
    },))
    with (
        patch.object(processing, "load_gemini_api_key", return_value="test-key"),
        patch.object(processing, "execution_budget", return_value=nullcontext()),
        patch.object(processing, "run_processing", return_value=result) as runner,
        patch.object(processing, "_report_connection", return_value=MagicMock()),
        patch.object(processing, "readiness_counts", side_effect=RuntimeError("private")),
        patch.object(processing, "dictionary_work", return_value=([], 0)),
        patch.object(processing, "read_published", side_effect=RuntimeError("private")),
    ):
        processor = processing.create_ai_processing(DatabaseSettings("postgresql://localhost/test"),
                                                    source="nowon")
        processor.finish()
    assert runner.call_count == 2
    for report in processor.report().values():
        assert report["succeeded_count"] == 1
        assert report["records"][0]["notice_id"] == 42
        assert report["complete"] is False
        assert "private" not in json.dumps(report)


def test_summary_only_does_not_require_or_call_dictionary(monkeypatch):
    monkeypatch.delenv("STDICT_API_KEY", raising=False)
    with (
        patch.object(adapter, "load_gemini_api_key", return_value="test-key"),
        patch.object(adapter, "run_processing",
                     return_value=ProcessingRunResult(False, 0, 0, ())) as runner,
        patch.object(adapter, "_report_connection", return_value=MagicMock()),
        patch.object(adapter, "readiness_counts", return_value=dict.fromkeys(
            ("ready", "pending", "running", "retry_wait", "blocked", "exhausted"), 0,
        )),
        patch.object(adapter, "dictionary_work") as dictionary,
        patch.object(adapter, "enrich_notice_dictionary") as enrich,
        patch.object(adapter, "read_published", return_value=[]),
    ):
        processor = adapter.create_ai_processing(
            DatabaseSettings("unused"), source="nowon", features=("summary",), limit=5,
        )
        processor.finish()
    assert processor.complete
    assert set(processor.report()) == {"summary"}
    assert runner.call_args.kwargs["features"] == ("summary",)
    assert runner.call_args.kwargs["limit"] == 5
    dictionary.assert_not_called()
    enrich.assert_not_called()
