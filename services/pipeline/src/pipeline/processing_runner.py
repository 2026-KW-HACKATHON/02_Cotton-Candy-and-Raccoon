"""Run a finite, private retry queue without holding a DB connection during AI work."""

import json
import math
import os
import random
import signal
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import psycopg

from pipeline.config import DatabaseSettings
from pipeline.glossary.easy_language import DEFAULT_MODEL as EASY_TEXT_MODEL
from pipeline.glossary.easy_language import PROMPT_VERSION as EASY_TEXT_PROMPT_VERSION
from pipeline.storage.processing_jobs import (
    FEATURES,
    Claim,
    Feature,
    claim_next,
    enqueue_candidates,
    finish_claim,
    make_contract_key,
    retry_job,
    select_candidates,
)
from pipeline.storage.summary_record import FAILURE_CODES
from pipeline.transform.gemini_client import DEFAULT_MODEL
from pipeline.transform.gemini_prompt import SUMMARY_PROMPT_VERSION

# Leave time for #60's 120-second provider budget to return structured failure
# metadata, plus source preparation and persistence, before the hard kill.
DEFAULT_JOB_TIMEOUT_SECONDS = 180.0
DEFAULT_LEASE_SECONDS = 240
CLEANUP_ALLOWANCE_SECONDS = 30
MAX_OUTCOME_BYTES = 8192
SAFE_REASONS = FAILURE_CODES | {
    "api_rate_limited", "api_server_error", "rate_limited", "server_error",
    "db_unavailable", "summary_storage_failed", "summary_read_failed",
    "no_body_text", "notice_not_found_or_hidden", "processing_claim_superseded",
    "summary_execution_superseded", "easy_text_storage_failed", "invalid_response",
    "invalid_notice", "job_timeout", "worker_failed", "invalid_worker_response",
    "processing_failed", "configuration_error", "processing_attempts_exhausted",
    "processing_result_not_current",
}
TRANSIENT_REASONS = frozenset({"api_timeout", "api_connection_error"})


def safe_reason(value: object, default: str = "processing_failed") -> str:
    return value if isinstance(value, str) and value in SAFE_REASONS else default


def _retry_time(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(UTC)
    return None


@dataclass(frozen=True, slots=True)
class ProcessingOutcome:
    """Only safe execution facts cross the process boundary; never a summary or error text."""

    status: Literal["succeeded", "failed", "skipped", "superseded"]
    reason_code: str | None = None
    retryable: bool = False
    retry_at: datetime | None = None

    def report(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "retryable": self.retryable,
            "retry_at": None if self.retry_at is None else self.retry_at.isoformat(),
        }

    @classmethod
    def parse(cls, value: object) -> "ProcessingOutcome":
        if not isinstance(value, dict) or set(value) != {
            "status", "reason_code", "retryable", "retry_at",
        }:
            raise ValueError("invalid_worker_response")
        if value["status"] not in {"succeeded", "failed", "skipped", "superseded"}:
            raise ValueError("invalid_worker_response")
        if value["reason_code"] is not None and value["reason_code"] not in SAFE_REASONS:
            raise ValueError("invalid_worker_response")
        if type(value["retryable"]) is not bool:
            raise ValueError("invalid_worker_response")
        if value["retryable"] and value["reason_code"] not in TRANSIENT_REASONS | {
            "api_error", "api_rate_limited", "api_server_error", "rate_limited", "server_error",
        }:
            raise ValueError("invalid_worker_response")
        retry_at = _retry_time(value["retry_at"])
        if value["retry_at"] is not None and retry_at is None:
            raise ValueError("invalid_worker_response")
        return cls(value["status"], value["reason_code"], value["retryable"], retry_at)


def failure_outcome(
    source: object, *, default_reason: str = "processing_failed",
) -> ProcessingOutcome:
    """Consume #60's structured failure when present; unknown api_error is not retried.

    A schema/grounding failure can never become retryable merely because an object
    happens to contain retryable=True. Provider errors require explicit structured
    classification; legacy timeout/connection reason codes already carry that fact.
    """
    failure = getattr(source, "execution_failure", None)
    if failure is None:
        failure = source

    def field(name: str, default: object = None) -> object:
        return failure.get(name, default) if isinstance(failure, Mapping) else getattr(
            failure, name, default
        )

    reason = safe_reason(field("reason_code", default_reason), default_reason)
    explicit = field("retryable")
    retryable = reason in TRANSIENT_REASONS if explicit is None else explicit is True
    if reason not in TRANSIENT_REASONS | {
        "api_error", "api_rate_limited", "api_server_error", "rate_limited", "server_error",
    }:
        retryable = False
    retry_at = _retry_time(field("retry_at"))
    if field("retry_at") is not None and retry_at is None:
        retryable = False
    return ProcessingOutcome("failed", reason, retryable, retry_at)


class WorkerCleanupError(RuntimeError):
    """Do not settle/release a claim unless its owned process has stopped."""

    def __init__(self) -> None:
        super().__init__("worker_cleanup_failed")


class _WindowsJob:
    """Own descendants even if the worker exits before taskkill can find its PID.

    The worker waits for its stdin payload before doing work. Assign it to this
    kill-on-close job before sending that payload, so nested SDK workers inherit
    the job. A Python/SDK crash therefore cannot orphan a provider subprocess.
    """

    def __init__(self, process: subprocess.Popen) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimit(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t),
                ("max_ws", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimit), ("io_counters", ctypes.c_ulonglong * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel = kernel
        self._handle = kernel.CreateJobObjectW(None, None)
        info = ExtendedLimit()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (not self._handle or not kernel.SetInformationJobObject(
                self._handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ) or not kernel.AssignProcessToJobObject(self._handle, int(process._handle))):
            self.close()
            process.kill()
            process.wait(timeout=10)
            raise WorkerCleanupError()

    def close(self) -> None:
        if self._handle:
            if not self._kernel.CloseHandle(self._handle):
                raise WorkerCleanupError()
            self._handle = None


def _stop_worker(process: subprocess.Popen, job: _WindowsJob | None = None) -> None:
    """Kill the worker's tree, including a future nested provider subprocess, then reap it."""
    try:
        if os.name == "nt":
            killed = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW, check=False,
            )
            if killed.returncode != 0 and process.poll() is None and job is None:
                raise WorkerCleanupError()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if job is not None:
            job.close()
        process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        if job is not None:
            job.close()
            process.wait(timeout=10)
            return
        raise WorkerCleanupError() from None


def execute_claim(
    database: DatabaseSettings, claim: Claim, api_key: str, timeout_seconds: float,
) -> ProcessingOutcome:
    """Supervise the entire operation, including source reads and attachment downloads.

    Credentials travel over stdin, never command-line arguments or returned output.
    A hard timeout has unknown provider state (possibly Retry-After sleep), so it
    stops automatic retries. A structured provider timeout can be retried safely.
    """
    payload = {
        "database_url": database.database_url, "api_key": api_key,
        "claim": {
            "notice_id": claim.notice_id, "feature": claim.feature,
            "input_version": claim.input_version, "contract_key": claim.contract_key,
            "claim_token": str(claim.claim_token), "attempts": claim.attempts,
            "lease_expires_at": claim.lease_expires_at.isoformat(),
        },
    }
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {
        "start_new_session": True,
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "pipeline.processing_worker"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        **kwargs,
    )
    job = None
    try:
        if os.name == "nt":
            job = _WindowsJob(process)
        output, _ = process.communicate(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"), timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        _stop_worker(process, job)
        return ProcessingOutcome("failed", "job_timeout")
    except BaseException:
        _stop_worker(process, job)
        raise
    finally:
        if job is not None:
            job.close()
        elif os.name != "nt":
            # A normally exited leader can still leave descendants whose stdio
            # is detached. Finish owning the whole process group before settling.
            _stop_worker(process)
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
    if process.returncode != 0:
        return ProcessingOutcome("failed", "worker_failed")
    if len(output) > MAX_OUTCOME_BYTES:
        return ProcessingOutcome("failed", "invalid_worker_response")
    try:
        return ProcessingOutcome.parse(json.loads(output))
    except (TypeError, ValueError, UnicodeError):
        return ProcessingOutcome("failed", "invalid_worker_response")


def next_retry_at(
    attempts: int, *, now: datetime, provider_retry_at: datetime | None = None,
    jitter: Callable[[float, float], float] = random.uniform,
) -> datetime:
    """Bound exponential delay; never shorten a provider's Retry-After instruction."""
    delay = min(3600.0, 30.0 * 2 ** min(max(attempts - 1, 0), 10))
    policy_time = now + timedelta(seconds=delay + jitter(0.0, delay * 0.25))
    return max(policy_time, provider_retry_at) if provider_retry_at is not None else policy_time


@dataclass(frozen=True, slots=True)
class ProcessingRunResult:
    dry_run: bool
    selected_count: int
    enqueued_count: int
    records: tuple[dict[str, object], ...]
    retry_released: bool = False
    error_code: str | None = None

    @property
    def exit_code(self) -> int:
        return int(self.error_code is not None or (not self.dry_run and any(
            record["state"] not in {"succeeded", "skipped"} for record in self.records
        )))

    def report(self) -> dict[str, object]:
        states = ("succeeded", "skipped", "blocked", "retry_wait", "exhausted", "superseded")
        return {
            "dry_run": self.dry_run, "complete": self.exit_code == 0,
            "selected_count": self.selected_count, "enqueued_count": self.enqueued_count,
            "attempted_count": 0 if self.dry_run else sum(
                not record.get("recovered", False) for record in self.records
            ),
            **{state + "_count": sum(r.get("state") == state for r in self.records)
               for state in states},
            "retry_released": self.retry_released, "error_code": self.error_code,
            "records": list(self.records),
        }


def _connect(database: DatabaseSettings) -> psycopg.Connection:
    return psycopg.connect(
        database.database_url, connect_timeout=5, autocommit=True,
        options="-c statement_timeout=10000 -c lock_timeout=5000",
    )


def run_processing(
    database: DatabaseSettings, *, api_key: str | None = None,
    features: tuple[Feature, ...] = FEATURES, notice_id: int | None = None, limit: int = 100,
    dry_run: bool = False, retry_stopped: bool = False, max_attempts: int = 3,
    job_timeout_seconds: float = DEFAULT_JOB_TIMEOUT_SECONDS,
    lease_seconds: int = DEFAULT_LEASE_SECONDS, summary_model: str = DEFAULT_MODEL,
    easy_text_model: str = EASY_TEXT_MODEL,
    executor: Callable[[DatabaseSettings, Claim, str, float], ProcessingOutcome] | None = None,
) -> ProcessingRunResult:
    """Scan once, enqueue, and attempt at most limit jobs sequentially; never sleep for retries."""
    if not features or any(feature not in FEATURES for feature in features):
        raise ValueError("invalid_processing_feature")
    features = tuple(dict.fromkeys(features))
    if notice_id is not None and (type(notice_id) is not int or not 0 < notice_id <= 2**63 - 1):
        raise ValueError("invalid_notice_id")
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError("invalid_processing_limit")
    if type(max_attempts) is not int or not 1 <= max_attempts <= 100:
        raise ValueError("invalid_processing_attempts")
    if (isinstance(job_timeout_seconds, bool) or not math.isfinite(job_timeout_seconds)
            or job_timeout_seconds <= 0):
        raise ValueError("invalid_processing_timeout")
    if (type(lease_seconds) is not int
            or lease_seconds <= job_timeout_seconds + CLEANUP_ALLOWANCE_SECONDS):
        raise ValueError("processing_lease_too_short")
    if retry_stopped and (dry_run or notice_id is None or len(features) != 1):
        raise ValueError("retry_requires_one_notice_and_feature")
    if not dry_run and (not isinstance(api_key, str) or not api_key.strip()):
        raise ValueError("missing_api_key")
    contracts = {
        "summary": make_contract_key(summary_model, SUMMARY_PROMPT_VERSION),
        "easy_text": make_contract_key(easy_text_model, EASY_TEXT_PROMPT_VERSION),
    }
    records: list[dict[str, object]] = []
    selected = enqueued = 0
    released = False
    execute = execute_claim if executor is None else executor
    try:
        with _connect(database) as conn:
            if retry_stopped:
                released = retry_job(conn, notice_id, features[0])
            candidates = select_candidates(
                conn, features=features, notice_id=notice_id, limit=limit,
                summary_model=summary_model, summary_prompt_version=SUMMARY_PROMPT_VERSION,
                easy_text_model=easy_text_model, easy_text_prompt_version=EASY_TEXT_PROMPT_VERSION,
            )
            selected = len(candidates)
            if dry_run:
                return ProcessingRunResult(True, selected, 0, tuple({
                    "notice_id": item.notice_id, "feature": item.feature, "state": "candidate",
                } for item in candidates))
            enqueued = enqueue_candidates(conn, candidates)
        for _ in range(limit):
            transitions: list[dict[str, object]] = []
            with _connect(database) as conn:
                claim = claim_next(
                    conn, features=features, notice_id=notice_id, max_attempts=max_attempts,
                    lease_seconds=lease_seconds, contract_keys=contracts, transitions=transitions,
                )
            records.extend(transitions)
            if claim is None:
                break
            try:
                outcome = execute(database, claim, api_key, job_timeout_seconds)
                outcome = ProcessingOutcome.parse(outcome.report())
            except WorkerCleanupError:
                # Keep the lease: never release a possibly live worker to another owner.
                return ProcessingRunResult(
                    False, selected, enqueued, tuple(records), released, "worker_cleanup_failed",
                )
            except Exception:
                outcome = ProcessingOutcome("failed", "worker_failed")
            state = outcome.status
            due = None
            if state == "failed":
                state = "blocked"
                if outcome.retryable:
                    state = "exhausted" if claim.attempts >= max_attempts else "retry_wait"
                    if state == "retry_wait":
                        due = next_retry_at(
                            claim.attempts, now=datetime.now(UTC),
                            provider_retry_at=outcome.retry_at,
                        )
            finished = False
            if state != "superseded":
                with _connect(database) as conn:
                    finished = finish_claim(
                        conn, claim, state=state, last_error_code=outcome.reason_code,
                        next_attempt_at=due,
                    )
            if not finished:
                state, due = "superseded", None
            records.append({
                "notice_id": claim.notice_id, "feature": claim.feature, "state": state,
                "attempts": claim.attempts,
                "reason_code": outcome.reason_code if finished else "processing_claim_superseded",
                "next_attempt_at": None if due is None else due.isoformat(),
            })
    except psycopg.Error:
        return ProcessingRunResult(
            dry_run, selected, enqueued, tuple(records), released, "db_unavailable",
        )
    return ProcessingRunResult(dry_run, selected, enqueued, tuple(records), released)
