"""An operational summary job requires the revision of its prepared snapshot."""

import inspect
from unittest.mock import MagicMock

import pytest
from support.prepared_summary_storage import _metadata, _prepared_source

from pipeline import summary_job
from pipeline.storage.summary_record import SummaryRecordError
from pipeline.transform import summarize as summarize_module
from pipeline.transform.grounding import unknown_summary
from pipeline.transform.prepared_summary import PreparedSummaryResult


@pytest.fixture
def untouched_job(monkeypatch):
    conn = MagicMock()
    prepared = _prepared_source("text")
    metadata = _metadata()
    boundaries = {}
    for name in (
        "begin_summary_execution", "summarize_prepared_notice", "save_prepared_summary",
        "record_summary_failure",
    ):
        boundary = MagicMock(side_effect=AssertionError("invalid revision crossed a boundary"))
        monkeypatch.setattr(summary_job, name, boundary)
        boundaries[name] = boundary
    provider = MagicMock(side_effect=AssertionError("invalid revision invoked the provider"))
    monkeypatch.setattr(summarize_module, "generate_summary_json", provider)
    boundaries["provider"] = provider
    return conn, prepared, metadata, boundaries


def _assert_untouched(job):
    conn, prepared, _, boundaries = job
    assert conn.mock_calls == []
    assert prepared.calls == []
    for boundary in boundaries.values():
        boundary.assert_not_called()


def test_captured_source_revision_is_a_required_keyword_integer():
    parameter = inspect.signature(summary_job.summarize_and_save_prepared_notice).parameters[
        "expected_source_revision"
    ]
    assert parameter.kind == inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
    assert parameter.annotation is int


def test_omitting_revision_stops_before_db_preparation_provider_or_failure_write(untouched_job):
    conn, prepared, metadata, _ = untouched_job
    with pytest.raises(TypeError, match="expected_source_revision"):
        summary_job.summarize_and_save_prepared_notice(conn, prepared, metadata)
    _assert_untouched(untouched_job)


def test_revision_cannot_be_passed_positionally(untouched_job):
    conn, prepared, metadata, _ = untouched_job
    with pytest.raises(TypeError):
        summary_job.summarize_and_save_prepared_notice(conn, prepared, metadata, 1)
    _assert_untouched(untouched_job)


@pytest.mark.parametrize("revision", [
    None, True, False, 0, -1, -(2**63), 2**63, 2**100, 1.0, "1",
], ids=[
    "none", "true", "false", "zero", "negative", "negative-bigint", "bigint-overflow",
    "huge-integer", "float", "string",
])
def test_invalid_revision_stops_before_any_db_provider_or_failure_write(untouched_job, revision):
    conn, prepared, metadata, _ = untouched_job
    with pytest.raises(SummaryRecordError, match="^invalid_source_revision$") as failure:
        summary_job.summarize_and_save_prepared_notice(
            conn, prepared, metadata, expected_source_revision=revision,
        )
    assert failure.value.reason_code == "invalid_source_revision"
    _assert_untouched(untouched_job)


@pytest.mark.parametrize("revision", [1, 2**63 - 1], ids=["first-revision", "maximum-bigint"])
def test_valid_captured_revision_is_forwarded_unchanged_to_registration(monkeypatch, revision):
    conn = MagicMock()
    prepared = _prepared_source("text")
    metadata = _metadata()
    result = PreparedSummaryResult(
        notice_id=prepared.notice_id, summary=unknown_summary(prepared.notice), warnings=(),
    )
    registration = MagicMock(return_value=17)
    generate = MagicMock(return_value=result)
    saved = object()
    save = MagicMock(return_value=saved)
    monkeypatch.setattr(summary_job, "begin_summary_execution", registration)
    monkeypatch.setattr(summary_job, "summarize_prepared_notice", generate)
    monkeypatch.setattr(summary_job, "save_prepared_summary", save)
    outcome = summary_job.summarize_and_save_prepared_notice(
        conn, prepared, metadata, expected_source_revision=revision,
    )
    assert outcome is saved
    registration.assert_called_once_with(
        conn, prepared.notice_id, expected_source_revision=revision,
    )
    generate.assert_called_once()
    assert save.call_args.kwargs["execution_token"] == 17
