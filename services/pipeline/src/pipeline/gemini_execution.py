"""Bound Gemini work with one parent-owned deadline and disposable workers."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_HTTP_ATTEMPTS = 2
DEFAULT_MAX_LOGICAL_REQUESTS = 2
MAX_WORKER_OUTPUT_BYTES = 4 * 1024 * 1024
_DISPATCH_PREFIX = b'{"event":"dispatch","attempt":1}\n'
_CLEANUP_RESERVE_SECONDS = 0.5
_RETRY_DELAY_SECONDS = 1.0
_SAFE_REASONS = frozenset({
    "api_error", "api_timeout", "api_connection_error", "input_too_large",
    "response_incomplete", "empty_response", "configuration_error", "missing_api_key",
    "empty_prompt", "invalid_input", "empty_input", "empty_input_block",
    "invalid_input_block", "unsupported_mime_type", "unsupported_input_block",
    "invalid_media_data", "invalid_retry_text", "response_validation_failed",
    "summary_processing_failed",
})
type FailureKind = Literal["deadline", "transient", "permanent", "deferred"]


class GeminiExecutionError(RuntimeError):
    """Only safe, structured metadata crosses the worker boundary."""

    def __init__(
        self,
        reason_code: str = "api_error",
        *,
        failure_kind: FailureKind = "permanent",
        retryable: bool = False,
        retry_at: datetime | None = None,
        status_code: int | None = None,
    ) -> None:
        self.reason_code = (
            reason_code
            if isinstance(reason_code, str) and reason_code in _SAFE_REASONS
            else "api_error"
        )
        self.failure_kind = (
            failure_kind
            if isinstance(failure_kind, str)
            and failure_kind in {"deadline", "transient", "permanent", "deferred"}
            else "permanent"
        )
        self.retryable = retryable is True
        self.retry_at = (
            retry_at.astimezone(UTC)
            if isinstance(retry_at, datetime) and retry_at.utcoffset() is not None
            else None
        )
        self.status_code = (
            status_code if type(status_code) is int and 400 <= status_code <= 599 else None
        )
        super().__init__(self.reason_code)

    def to_dict(self) -> dict[str, object]:
        return {
            "reason_code": self.reason_code,
            "failure_kind": self.failure_kind,
            "retryable": self.retryable,
            "retry_at": self.retry_at.isoformat() if self.retry_at is not None else None,
            "status_code": self.status_code,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> GeminiExecutionError:
        retry_at = None
        if isinstance(value.get("retry_at"), str):
            try:
                retry_at = datetime.fromisoformat(value["retry_at"])
            except ValueError:
                pass
        return cls(
            value.get("reason_code", "api_error"),
            failure_kind=value.get("failure_kind", "permanent"),
            retryable=value.get("retryable") is True,
            retry_at=retry_at,
            status_code=value.get("status_code"),
        )


@dataclass(slots=True)
class ExecutionBudget:
    """Counts and time shared by transport retries and response corrections."""

    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_http_attempts: int = DEFAULT_MAX_HTTP_ATTEMPTS
    max_logical_requests: int = DEFAULT_MAX_LOGICAL_REQUESTS
    logical_requests: int = field(default=0, init=False)
    http_attempts: int = field(default=0, init=False)
    last_failure: GeminiExecutionError | None = field(default=None, init=False, repr=False)
    deadline: float = field(init=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or type(self.max_http_attempts) is not int
            or self.max_http_attempts < 1
            or type(self.max_logical_requests) is not int
            or self.max_logical_requests < 1
        ):
            raise GeminiExecutionError("configuration_error")
        self.deadline = time.monotonic() + self.timeout_seconds

    @property
    def cleanup_reserve_seconds(self) -> float:
        return min(_CLEANUP_RESERVE_SECONDS, self.timeout_seconds / 10)

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def fail(self, error: GeminiExecutionError) -> None:
        self.last_failure = error
        raise error

    def check(self) -> None:
        if self.remaining_seconds() <= 0:
            self.fail(_deadline_error())


_execution: ContextVar[ExecutionBudget | None] = ContextVar("gemini_execution", default=None)
_dispatch_sink: Callable[[], None] | None = None


@dataclass(slots=True)
class ExecutionStats:
    """Observe request deltas without starting the AI deadline during cache reads."""

    logical_requests: int = 0
    http_attempts: int = 0
    _budgets: dict[int, tuple[ExecutionBudget, int, int]] = field(default_factory=dict, repr=False)

    def _track(self, budget: ExecutionBudget) -> None:
        self._budgets.setdefault(
            id(budget), (budget, budget.logical_requests, budget.http_attempts)
        )

    def _finish(self) -> None:
        for budget, logical_start, http_start in self._budgets.values():
            self.logical_requests += budget.logical_requests - logical_start
            self.http_attempts += budget.http_attempts - http_start
        self._budgets.clear()


_stats_observers: ContextVar[tuple[ExecutionStats, ...]] = ContextVar(
    "gemini_stats_observers", default=()
)


@contextmanager
def capture_execution_stats() -> Iterator[ExecutionStats]:
    """Snapshot this scope's counters, including work inside an existing budget."""
    stats = ExecutionStats()
    if (active := current_execution()) is not None:
        stats._track(active)
    token = _stats_observers.set((*_stats_observers.get(), stats))
    try:
        yield stats
    finally:
        stats._finish()
        _stats_observers.reset(token)


def current_execution() -> ExecutionBudget | None:
    return _execution.get()


def _configured_timeout() -> float:
    name = "GEMINI_EXECUTION_TIMEOUT_SECONDS"
    value = os.environ.get(name)
    if value is None:
        # Match the local credential loader without exporting unrelated secrets.
        from pipeline.transform.gemini_prompt import DEFAULT_ENV_PATH

        try:
            lines = DEFAULT_ENV_PATH.read_text(encoding="utf-8-sig").splitlines()
        except FileNotFoundError:
            lines = []
        except OSError:
            raise GeminiExecutionError("configuration_error") from None
        for line in lines:
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, candidate = line.split("=", 1)
            if key.strip() == name:
                value = candidate.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '\"'):
                    value = value[1:-1]
                break
    try:
        return float(DEFAULT_TIMEOUT_SECONDS if value is None else value)
    except (TypeError, ValueError):
        raise GeminiExecutionError("configuration_error") from None


@contextmanager
def execution_budget(
    budget: ExecutionBudget | None = None, *, timeout_seconds: float | None = None
) -> Iterator[ExecutionBudget]:
    """Nested helpers reuse the job budget; exit never discards a usable candidate."""
    active = current_execution()
    if active is not None:
        yield active
        return
    if budget is None:
        budget = ExecutionBudget(
            timeout_seconds=_configured_timeout() if timeout_seconds is None else timeout_seconds
        )
    for observer in _stats_observers.get():
        observer._track(budget)
    token = _execution.set(budget)
    try:
        yield budget
    finally:
        _execution.reset(token)


def record_http_dispatch() -> None:
    """Emit before sending bytes so a killed worker still reports its attempt."""
    if _dispatch_sink is not None:
        _dispatch_sink()
    elif (budget := current_execution()) is not None:
        budget.check()
        if budget.http_attempts >= budget.max_http_attempts:
            budget.fail(_attempt_limit_error())
        budget.http_attempts += 1


def _deadline_error() -> GeminiExecutionError:
    return GeminiExecutionError(
        "api_timeout", failure_kind="deadline", retryable=True,
        retry_at=datetime.now(UTC) + timedelta(seconds=_RETRY_DELAY_SECONDS),
    )


def _attempt_limit_error() -> GeminiExecutionError:
    return GeminiExecutionError(
        "api_error", failure_kind="deferred", retryable=True,
        retry_at=datetime.now(UTC) + timedelta(seconds=_RETRY_DELAY_SECONDS),
    )


def _worker_environment() -> dict[str, str]:
    # Whitelist OS/network configuration; DB/provider credentials are never inherited.
    allowed = {
        "PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "TEMP", "TMP", "TMPDIR",
        "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "LANG", "LC_ALL", "LC_CTYPE",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment.update({
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PIPELINE_GEMINI_WORKER": "1",
    })
    return environment


def _stop_worker(process: subprocess.Popen[bytes], budget: ExecutionBudget) -> bytes:
    """Terminate, escalate, and reap before handing control back to the caller."""
    if process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    try:
        output, _ = process.communicate(timeout=min(0.2, max(0.01, budget.remaining_seconds())))
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        # After kill, join even if the clock has expired: never leave a live worker.
        output, _ = process.communicate()
    return output or b""


def _read_worker_output(
    output: bytes, budget: ExecutionBudget
) -> tuple[str | None, GeminiExecutionError | None]:
    if len(output) > MAX_WORKER_OUTPUT_BYTES:
        # Count the flushed event without splitting/parsing an oversized response.
        if output.startswith(_DISPATCH_PREFIX):
            budget.http_attempts += 1
        return None, GeminiExecutionError("response_incomplete")
    attempts = 0
    result: dict[str, object] | None = None
    invalid = False
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            invalid = True
            continue
        if not isinstance(event, dict):
            invalid = True
        elif event.get("event") == "dispatch" and type(event.get("attempt")) is int:
            # TimeoutExpired.output may be a prefix of communicate()'s final output.
            # Only the final output is parsed, and sequence values are never summed.
            attempts = max(attempts, event["attempt"])
        elif event.get("event") == "result" and result is None:
            result = event
        else:
            invalid = True
    budget.http_attempts += max(0, attempts)
    if invalid or attempts > 1 or budget.http_attempts > budget.max_http_attempts:
        return None, GeminiExecutionError("configuration_error")
    if result is None:
        return None, GeminiExecutionError("api_error")
    if result.get("ok") is True and isinstance(result.get("value"), str) and attempts == 1:
        return result["value"], None
    if result.get("ok") is False and isinstance(result.get("error"), dict):
        return None, GeminiExecutionError.from_dict(result["error"])
    return None, GeminiExecutionError("api_error")


def _invoke_worker(
    operation: str, payload: dict[str, object], budget: ExecutionBudget
) -> str:
    available = budget.remaining_seconds() - budget.cleanup_reserve_seconds
    if available <= 0:
        raise _deadline_error()
    try:
        request = json.dumps(
            {"operation": operation, "payload": payload}, ensure_ascii=False
        ).encode()
    except (TypeError, ValueError, UnicodeError):
        raise GeminiExecutionError("configuration_error") from None
    budget.check()
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "pipeline.gemini_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_worker_environment(),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, ValueError):
        raise GeminiExecutionError("configuration_error") from None
    finished = threading.Event()
    expired = threading.Event()

    def interrupt_worker() -> None:
        if finished.is_set() or process.poll() is not None:
            return
        expired.set()
        try:
            process.terminate()
        except ProcessLookupError:
            return
        if not finished.wait(min(0.1, budget.cleanup_reserve_seconds / 2)):
            if process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass

    # On Windows communicate() writes stdin synchronously before its timed waits.
    # A separate watchdog must interrupt a child that never starts reading input.
    watchdog = threading.Timer(
        max(0.001, budget.remaining_seconds() - budget.cleanup_reserve_seconds), interrupt_worker
    )
    watchdog.name = "gemini-deadline-watchdog"
    watchdog.daemon = True
    watchdog.start()
    try:
        # communicate drains stdout while feeding input; the watchdog also bounds writes.
        output, _ = process.communicate(
            request,
            timeout=max(0.001, budget.remaining_seconds() - budget.cleanup_reserve_seconds),
        )
    except subprocess.TimeoutExpired:
        output = _stop_worker(process, budget)
        _read_worker_output(output, budget)
        raise _deadline_error() from None
    except BaseException:
        output = _stop_worker(process, budget)
        _read_worker_output(output, budget)
        raise
    finally:
        finished.set()
        watchdog.cancel()
        watchdog.join()
    value, error = _read_worker_output(output, budget)
    if expired.is_set():
        raise _deadline_error()
    budget.check()
    if error is not None:
        raise error
    if process.returncode != 0 or value is None:
        raise GeminiExecutionError("api_error")
    return value


def run_gemini_request(operation: str, payload: dict[str, object]) -> str:
    """One logical request, with application-owned transport retries if they fit."""
    if operation not in {"summary", "easy_language"}:
        raise GeminiExecutionError("configuration_error")
    with execution_budget() as budget:
        budget.check()
        if budget.logical_requests >= budget.max_logical_requests:
            budget.fail(_attempt_limit_error())
        budget.logical_requests += 1
        while True:
            budget.check()
            if budget.http_attempts >= budget.max_http_attempts:
                budget.fail(_attempt_limit_error())
            before = budget.http_attempts
            try:
                value = _invoke_worker(operation, payload, budget)
            except GeminiExecutionError as error:
                if (
                    not error.retryable
                    or error.failure_kind not in {"transient", "deferred"}
                    or budget.http_attempts == before
                ):
                    budget.fail(error)
                retry_at = error.retry_at or datetime.now(UTC) + timedelta(
                    seconds=_RETRY_DELAY_SECONDS
                )
                delay = max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
                if (
                    budget.http_attempts >= budget.max_http_attempts
                    or delay >= budget.remaining_seconds() - budget.cleanup_reserve_seconds
                ):
                    budget.fail(GeminiExecutionError(
                        error.reason_code,
                        failure_kind="deferred",
                        retryable=True,
                        retry_at=retry_at,
                        status_code=error.status_code,
                    ))
                time.sleep(delay)
                continue
            budget.check()
            budget.last_failure = None
            return value
