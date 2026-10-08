"""Collection adapter failure isolation and committed app visibility."""

from contextlib import nullcontext
from unittest.mock import Mock

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
    processor = adapter.create_ai_processing(DatabaseSettings("unused"), source="seoul")
    processor.finish()  # No new raw IDs still scans the source's old work.
    assert not processor.complete
    assert all(r["readiness"][remaining] == 1 for r in processor.report().values())
