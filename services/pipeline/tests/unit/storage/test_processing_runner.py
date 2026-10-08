"""Retry policy and whole-process deadlines, independent of provider and database services."""

import json
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from pipeline.config import DatabaseSettings
from pipeline.processing_runner import (
    ProcessingOutcome,
    WorkerCleanupError,
    execute_claim,
    failure_outcome,
    next_retry_at,
    run_processing,
)
from pipeline.storage.processing_jobs import Candidate, Claim, make_contract_key

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)
DATABASE = DatabaseSettings("postgresql://test:secret@localhost/test")
KEY = "private-provider-key"


def claim_for(feature="summary", *, attempts=1):
    return Claim(
        notice_id=7, feature=feature, input_version="1" if feature == "summary" else "a" * 64,
        contract_key=make_contract_key("test-model", "test-prompt"),
        claim_token=uuid4(), attempts=attempts, lease_expires_at=NOW + timedelta(minutes=5),
    )


@pytest.mark.parametrize(("failure", "retryable"), [
    ({"reason_code": "api_timeout"}, True),
    ({"reason_code": "attachment_download_failed", "retryable": True}, True),
    ({"reason_code": "attachment_download_failed"}, False),
    ({"reason_code": "input_preparation_failed", "retryable": True}, False),
    ({"reason_code": "api_connection_error"}, True),
    ({"reason_code": "api_error"}, False),
    ({"reason_code": "api_error", "retryable": False}, False),  # e.g. HTTP 401
    ({"reason_code": "api_error", "retryable": True}, True),  # structured 429/5xx
    ({"reason_code": "api_timeout", "retryable": False}, False),
    ({"reason_code": "response_validation_failed", "retryable": True}, False),
    ({"reason_code": "summary_information_loss", "retryable": True}, False),
    ({"reason_code": "job_timeout", "retryable": True}, False),
    ({"reason_code": "api_error", "retryable": "true"}, False),
    ({"reason_code": KEY, "retryable": True}, False),
    ({"reason_code": "api_error", "retryable": True, "retry_at": "invalid"}, False),
])
def test_only_classified_transient_failures_are_automatically_retried(failure, retryable):
    outcome = failure_outcome(SimpleNamespace(execution_failure=failure))
    assert outcome.retryable is retryable
    assert KEY not in json.dumps(outcome.report())


def test_future_structured_retry_after_is_never_shortened():
    provider_time = NOW + timedelta(hours=10)
    outcome = failure_outcome(SimpleNamespace(execution_failure={
        "reason_code": "api_error", "retryable": True, "retry_at": provider_time.isoformat(),
    }))
    assert next_retry_at(
        2, now=NOW, provider_retry_at=outcome.retry_at, jitter=lambda low, high: high,
    ) == provider_time
    assert next_retry_at(2, now=NOW, jitter=lambda low, high: high) == NOW + timedelta(seconds=75)
    assert next_retry_at(
        2, now=NOW, provider_retry_at=NOW - timedelta(hours=1), jitter=lambda low, high: 0,
    ) == NOW + timedelta(seconds=60)


@pytest.mark.parametrize("payload", [
    {"status": "failed", "reason_code": KEY, "retryable": False, "retry_at": None},
    {"status": "failed", "reason_code": "summary_information_loss", "retryable": True,
     "retry_at": None},
    {"status": "failed", "reason_code": "api_error", "retryable": True,
     "retry_at": "not-a-date"},
    {"status": "succeeded", "reason_code": None, "retryable": False, "retry_at": None,
     "raw_response": "private"},
])
def test_parent_rejects_unsafe_or_inconsistent_worker_protocol(payload):
    with pytest.raises(ValueError, match="invalid_worker_response"):
        ProcessingOutcome.parse(payload)


@pytest.fixture
def queue(monkeypatch):
    active = [0]

    @contextmanager
    def connect(database):
        active[0] += 1
        try:
            yield object()
        finally:
            active[0] -= 1

    monkeypatch.setattr("pipeline.processing_runner._connect", connect)
    candidate = Candidate(7, "summary", "1", make_contract_key("test-model", "test-prompt"))
    selected = Mock(return_value=[candidate])
    enqueued = Mock(return_value=1)
    claimed = Mock(return_value=None)
    finished = Mock(return_value=True)
    retried = Mock(return_value=False)
    for name, value in {
        "select_candidates": selected, "enqueue_candidates": enqueued,
        "claim_next": claimed, "finish_claim": finished, "retry_job": retried,
    }.items():
        monkeypatch.setattr("pipeline.processing_runner." + name, value)
    return SimpleNamespace(
        active=active, selected=selected, enqueued=enqueued, claimed=claimed,
        finished=finished, retried=retried,
    )


def test_dry_run_requires_no_key_and_performs_no_queue_mutation(queue):
    outcome = run_processing(DATABASE, dry_run=True)
    assert outcome.report()["selected_count"] == 1
    assert outcome.exit_code == 0
    for call in (queue.enqueued, queue.claimed, queue.finished, queue.retried):
        call.assert_not_called()


def test_feature_failure_does_not_prevent_the_other_feature_and_connections_are_closed(queue):
    queue.claimed.side_effect = [claim_for(), claim_for("easy_text"), None]

    def execute(database, claim, api_key, timeout):
        assert queue.active[0] == 0
        return ProcessingOutcome("failed", "api_error") if claim.feature == "summary" else (
            ProcessingOutcome("succeeded")
        )

    result = run_processing(DATABASE, api_key=KEY, executor=execute)
    assert [record["state"] for record in result.records] == ["blocked", "succeeded"]
    assert [call.kwargs["state"] for call in queue.finished.call_args_list] == [
        "blocked", "succeeded",
    ]
    assert queue.claimed.call_args.kwargs["contract_keys"].keys() == {"summary", "easy_text"}
    assert KEY not in json.dumps(result.report())
    assert DATABASE.database_url not in json.dumps(result.report())


@pytest.mark.parametrize(("attempts", "expected"), [(1, "retry_wait"), (3, "exhausted")])
def test_transient_attempts_have_a_durable_backoff_and_stop_at_the_limit(queue, attempts, expected):
    queue.claimed.side_effect = [claim_for(attempts=attempts), None]
    result = run_processing(
        DATABASE, api_key=KEY,
        executor=lambda *args: ProcessingOutcome("failed", "api_timeout", True),
    )
    assert result.records[0]["state"] == expected
    due = queue.finished.call_args.kwargs["next_attempt_at"]
    assert (due is not None) is (expected == "retry_wait")


def test_batch_never_attempts_more_than_the_limit_even_if_work_remains(queue):
    queue.claimed.return_value = claim_for()
    result = run_processing(
        DATABASE, api_key=KEY, limit=2,
        executor=lambda *args: ProcessingOutcome("succeeded"),
    )
    assert len(result.records) == queue.claimed.call_count == 2


def test_cleanup_failure_keeps_lease_and_never_finishes_the_claim(queue):
    queue.claimed.return_value = claim_for()

    def execute(*args):
        raise WorkerCleanupError()

    result = run_processing(DATABASE, api_key=KEY, executor=execute)
    assert result.error_code == "worker_cleanup_failed"
    assert result.exit_code == 1
    queue.finished.assert_not_called()


def test_recovered_exhaustion_is_reported_without_claiming_another_attempt(queue):
    def claim(conn, *, transitions, **kwargs):
        transitions.append({
            "notice_id": 7, "feature": "summary", "state": "exhausted", "attempts": 3,
            "reason_code": "processing_lease_expired", "next_attempt_at": None, "recovered": True,
        })
        return None

    queue.claimed.side_effect = claim
    result = run_processing(DATABASE, api_key=KEY)
    assert result.exit_code == 1
    assert result.report()["exhausted_count"] == 1
    assert result.report()["attempted_count"] == 0
    queue.finished.assert_not_called()


def test_lost_claim_cannot_report_a_committed_queue_completion(queue):
    queue.claimed.side_effect = [claim_for(), None]
    queue.finished.return_value = False
    result = run_processing(
        DATABASE, api_key=KEY, executor=lambda *args: ProcessingOutcome("succeeded"),
    )
    assert result.records[0]["state"] == "superseded"
    assert result.exit_code == 1


@pytest.mark.parametrize("options", [
    {"retry_stopped": True},
    {"retry_stopped": True, "notice_id": 7},
    {"retry_stopped": True, "notice_id": 7, "features": ("summary",), "dry_run": True},
    {"job_timeout_seconds": 150, "lease_seconds": 180},
    {"job_timeout_seconds": float("inf")},
    {"limit": 0},
])
def test_invalid_operations_fail_before_database_access(queue, options):
    with pytest.raises(ValueError):
        run_processing(DATABASE, api_key=KEY, **options)
    queue.selected.assert_not_called()


@pytest.mark.parametrize("leader_mode", ["running", "crashed", "succeeded"])
def test_deadline_kills_the_owned_worker_and_descendant_without_credentials_in_arguments(
    monkeypatch, tmp_path, leader_mode,
):
    """A process-tree test: a leaked descendant would write after the parent times out."""
    marker = tmp_path / "leaked-child"
    ready = tmp_path / "worker-started"
    grandchild = (
        "import time; from pathlib import Path; time.sleep(2); "
        f"Path({str(marker)!r}).write_text('leaked')"
    )
    child_stdio = ", stdout=subprocess.DEVNULL" if leader_mode == "succeeded" else ""
    endings = {
        "running": "time.sleep(30)", "crashed": "sys.exit(2)",
        "succeeded": f"print({json.dumps(ProcessingOutcome('succeeded').report())!r})",
    }
    child = (
        "import subprocess,sys,time,json; from pathlib import Path; json.load(sys.stdin); "
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}]{child_stdio}); "
        f"Path({str(ready)!r}).write_text('ready'); "
        + endings[leader_mode]
    )
    original = subprocess.Popen
    processes = []

    def launch(args, **kwargs):
        assert KEY not in str(args) and DATABASE.database_url not in str(args)
        process = original([sys.executable, "-c", child], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("pipeline.processing_runner.subprocess.Popen", launch)
    # taskkill also uses Popen through subprocess.run; preserve its normal launch.
    original_run = subprocess.run

    def run_command(args, **kwargs):
        monkeypatch.setattr("pipeline.processing_runner.subprocess.Popen", original)
        try:
            return original_run(args, **kwargs)
        finally:
            monkeypatch.setattr("pipeline.processing_runner.subprocess.Popen", launch)

    monkeypatch.setattr("pipeline.processing_runner.subprocess.run", run_command)
    outcome = execute_claim(DATABASE, claim_for(), KEY, 0.8)
    assert ready.exists(), "the test must actually start the supervised worker"
    assert outcome == (ProcessingOutcome("succeeded") if leader_mode == "succeeded" else
                       ProcessingOutcome("failed", "job_timeout"))
    assert processes[0].poll() is not None
    time.sleep(2.3)
    assert not marker.exists(), "a descendant outlived the timed-out worker"
