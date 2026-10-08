"""Exercise process lifetime, IPC and the shared Gemini execution policy."""

import io
import json
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from pipeline import gemini_execution as execution
from pipeline.gemini_execution import (
    ExecutionBudget,
    GeminiExecutionError,
    current_execution,
    execution_budget,
    record_http_dispatch,
    run_gemini_request,
)

_DISPATCH = 'print(\'{"event":"dispatch","attempt":1}\', flush=True)\n'
_SUCCESS = 'print(\'{"event":"result","ok":true,"value":"{}"}\', flush=True)\n'


def _script_workers(monkeypatch, scripts):
    """Use real OS children with local scripts instead of any provider connection."""
    popen = subprocess.Popen
    processes = []
    commands = []

    def start(command, **kwargs):
        commands.append((command, kwargs))
        script = scripts[len(processes)]
        child = popen([sys.executable, "-u", "-c", script], **kwargs)
        processes.append(child)
        return child

    monkeypatch.setattr(execution.subprocess, "Popen", start)
    return processes, commands


def test_stalled_worker_is_reaped_and_dispatch_count_is_not_duplicated(monkeypatch):
    children, _ = _script_workers(
        monkeypatch,
        ["import sys,time\nsys.stdin.buffer.read()\n" + _DISPATCH + "time.sleep(30)"],
    )
    budget = ExecutionBudget(timeout_seconds=2)
    started = time.monotonic()
    with execution_budget(budget), pytest.raises(GeminiExecutionError) as caught:
        run_gemini_request("summary", {"api_key": "not-a-real-key"})
    assert time.monotonic() - started < 3
    assert caught.value.reason_code == "api_timeout"
    assert caught.value.failure_kind == "deadline"
    assert budget.logical_requests == 1
    assert budget.http_attempts == 1
    assert children[0].poll() is not None
    assert budget.last_failure is caught.value
    assert not any(t.name == "gemini-deadline-watchdog" for t in threading.enumerate())


def test_continuously_producing_worker_output_does_not_extend_deadline(monkeypatch):
    # An active pipe, like a response dribble, must not reset the total timer.
    children, _ = _script_workers(monkeypatch, [
        "import sys,time\nsys.stdin.buffer.read()\n" + _DISPATCH
        + "while True:\n sys.stdout.write(' ');sys.stdout.flush();time.sleep(.02)"
    ])
    budget = ExecutionBudget(timeout_seconds=2)
    with execution_budget(budget), pytest.raises(GeminiExecutionError) as caught:
        run_gemini_request("summary", {})
    assert caught.value.failure_kind == "deadline"
    assert children[0].poll() is not None
    assert budget.http_attempts == 1


def test_worker_that_never_reads_large_stdin_is_killed_within_budget(monkeypatch):
    children, _ = _script_workers(monkeypatch, ["import time\ntime.sleep(30)"])
    budget = ExecutionBudget(timeout_seconds=0.7)
    started = time.monotonic()
    with execution_budget(budget), pytest.raises(GeminiExecutionError) as caught:
        run_gemini_request("summary", {"notice_text": "x" * 2_000_000})
    assert time.monotonic() - started < 1.7
    assert caught.value.failure_kind == "deadline"
    assert children[0].poll() is not None
    assert budget.http_attempts == 0


@pytest.mark.parametrize("operation", ["summary", "easy_language"])
@pytest.mark.parametrize("status", [200, 429])
def test_real_sdk_worker_closes_dribbling_http_connection(monkeypatch, operation, status):
    received = threading.Event()
    disconnected = threading.Event()
    stopped = threading.Event()

    class DribbleHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            received.set()
            self.send_response(status)
            if status == 429:
                self.send_header("Retry-After", "3600")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "1000000")
            self.end_headers()
            try:
                while not stopped.wait(0.02):
                    self.wfile.write(b" ")
                    self.wfile.flush()
            except ConnectionError:
                disconnected.set()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), DribbleHandler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    environment = execution._worker_environment()
    environment["GOOGLE_GEMINI_BASE_URL"] = f"http://127.0.0.1:{server.server_port}"
    environment["NO_PROXY"] = "127.0.0.1,localhost"
    monkeypatch.setattr(execution, "_worker_environment", lambda: environment)
    popen = subprocess.Popen
    children = []

    def track_child(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(execution.subprocess, "Popen", track_child)
    # Leave room for cold SDK imports on Windows before testing the stuck response.
    budget = ExecutionBudget(timeout_seconds=15 if status == 429 else 6)
    started = time.monotonic()
    try:
        with execution_budget(budget), pytest.raises(GeminiExecutionError) as caught:
            run_gemini_request(operation, {
                "prompt": "Return JSON.", "notice_text": "Local test notice.",
                "api_key": "fake-loopback-only", "model": "gemini-3.5-flash-lite",
            })
        if status == 429:
            assert caught.value.failure_kind == "deferred"
            assert caught.value.status_code == 429
            assert caught.value.retry_at > datetime.now(UTC) + timedelta(minutes=59)
        else:
            assert caught.value.failure_kind == "deadline"
        assert time.monotonic() - started < budget.timeout_seconds + 1.5
        assert received.is_set(), "The worker must reach the local server before the deadline."
        assert disconnected.wait(1), "Killing the worker must close its in-flight HTTP socket."
        assert len(children) == 1 and children[0].poll() is not None
        assert budget.http_attempts == 1
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        serving.join()


def test_large_input_uses_pipes_and_worker_does_not_inherit_credentials(monkeypatch):
    children, commands = _script_workers(monkeypatch, [
        "import sys,json\nrequest=json.load(sys.stdin)\n"
        "assert len(request['payload']['notice_text']) == 2_000_000\n"
        + _DISPATCH + _SUCCESS
    ])
    for key in ("DATABASE_URL", "PGPASSWORD", "GEMINI_API_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.setenv(key, "must-stay-in-parent")
    budget = ExecutionBudget(timeout_seconds=5)
    payload = {"notice_text": "가" * 2_000_000, "api_key": "secret-only-in-stdin"}
    with execution_budget(budget):
        assert run_gemini_request("summary", payload) == "{}"
    command, options = commands[0]
    assert "secret-only-in-stdin" not in repr(command)
    assert not any(key in options["env"] for key in (
        "DATABASE_URL", "PGPASSWORD", "GEMINI_API_KEY", "SUPABASE_SERVICE_ROLE_KEY"
    ))
    assert options["stderr"] == subprocess.DEVNULL
    assert children[0].poll() == 0
    assert budget.http_attempts == budget.logical_requests == 1


def test_one_retry_and_correction_share_the_same_http_cap(monkeypatch):
    error = GeminiExecutionError(
        "api_error", failure_kind="transient", retryable=True,
        retry_at=datetime(2000, 1, 1, tzinfo=UTC), status_code=429,
    )
    failure = json.dumps({"event": "result", "ok": False, "error": error.to_dict()})
    _, commands = _script_workers(monkeypatch, [
        "import sys\nsys.stdin.buffer.read()\n" + _DISPATCH + f"print({failure!r},flush=True)",
        "import sys\nsys.stdin.buffer.read()\n" + _DISPATCH + _SUCCESS,
    ])
    budget = ExecutionBudget(timeout_seconds=5)
    with execution_budget(budget):
        assert run_gemini_request("summary", {}) == "{}"
        assert budget.last_failure is None
        with pytest.raises(GeminiExecutionError) as caught:
            run_gemini_request("summary", {})
    assert caught.value.failure_kind == "deferred"
    assert budget.logical_requests == 2
    assert budget.http_attempts == 2
    assert len(commands) == 2


def test_long_retry_after_is_deferred_without_waiting_or_resending(monkeypatch):
    retry_at = datetime.now(UTC) + timedelta(hours=1)
    failure = GeminiExecutionError(
        "api_error", failure_kind="transient", retryable=True,
        retry_at=retry_at, status_code=503,
    )
    calls = []

    def fail_once(operation, payload, budget):
        calls.append(operation)
        record_http_dispatch()
        raise failure

    monkeypatch.setattr(execution, "_invoke_worker", fail_once)
    budget = ExecutionBudget(timeout_seconds=2)
    with execution_budget(budget), pytest.raises(GeminiExecutionError) as caught:
        run_gemini_request("easy_language", {})
    assert calls == ["easy_language"]
    assert caught.value.failure_kind == "deferred"
    assert caught.value.retry_at == retry_at
    assert caught.value.status_code == 503
    assert budget.http_attempts == 1


def test_late_success_is_rejected_after_child_has_been_reaped(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(execution.time, "monotonic", lambda: clock[0])

    class LateChild:
        returncode = 0

        def poll(self):
            return 0

        def communicate(self, data, timeout):
            clock[0] = 12.0
            return (
                b'{"event":"dispatch","attempt":1}\n'
                b'{"event":"result","ok":true,"value":"late"}\n', None
            )

    monkeypatch.setattr(execution.subprocess, "Popen", lambda *args, **kwargs: LateChild())
    budget = ExecutionBudget(timeout_seconds=1)
    with execution_budget(budget), pytest.raises(GeminiExecutionError) as caught:
        run_gemini_request("summary", {})
    assert caught.value.failure_kind == "deadline"
    assert budget.http_attempts == 1


def test_keyboard_interrupt_terminates_and_reaps_worker_before_propagating(monkeypatch):
    events = []

    class InterruptedChild:
        def poll(self):
            return None

        def terminate(self):
            events.append("terminate")

        def communicate(self, data=None, timeout=None):
            if data is not None:
                raise KeyboardInterrupt
            events.append("reap")
            return b'{"event":"dispatch","attempt":1}\n', None

    monkeypatch.setattr(execution.subprocess, "Popen", lambda *args, **kwargs: InterruptedChild())
    budget = ExecutionBudget(timeout_seconds=5)
    with execution_budget(budget), pytest.raises(KeyboardInterrupt):
        run_gemini_request("summary", {})
    assert events == ["terminate", "reap"]
    assert budget.http_attempts == 1


def test_worker_ignoring_termination_is_killed_and_reaped(monkeypatch):
    events = []

    class UncooperativeChild:
        def poll(self):
            return None

        def terminate(self):
            events.append("terminate")

        def kill(self):
            events.append("kill")

        def communicate(self, data=None, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("test-child", timeout)
            events.append("reap")
            return b'{"event":"dispatch","attempt":1}\n', None

    monkeypatch.setattr(execution.subprocess, "Popen", lambda *args, **kwargs: UncooperativeChild())
    budget = ExecutionBudget(timeout_seconds=5)
    with execution_budget(budget), pytest.raises(GeminiExecutionError) as caught:
        run_gemini_request("summary", {})
    assert caught.value.failure_kind == "deadline"
    assert events == ["terminate", "kill", "reap"]
    assert budget.http_attempts == 1


def test_nested_scope_reuses_deadline_and_does_not_raise_on_exit(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(execution.time, "monotonic", lambda: clock[0])
    budget = ExecutionBudget(timeout_seconds=2)
    with execution_budget(budget):
        with execution_budget(timeout_seconds=100) as inner:
            assert inner is budget
            clock[0] = 13.0
        # A previously usable candidate may still be returned by its owner.
        assert current_execution() is budget
        with pytest.raises(GeminiExecutionError) as caught:
            budget.check()
        assert budget.last_failure is caught.value
    assert current_execution() is None


@pytest.mark.parametrize("value", ["nan", "inf", "0", "-1", "private bad value"])
def test_invalid_timeout_configuration_has_only_a_safe_error(monkeypatch, value):
    monkeypatch.setenv("GEMINI_EXECUTION_TIMEOUT_SECONDS", value)
    with pytest.raises(GeminiExecutionError, match="^configuration_error$"):
        with execution_budget():
            pytest.fail("invalid configuration was accepted")


def test_configured_timeout_is_only_read_at_outer_scope(monkeypatch):
    monkeypatch.setenv("GEMINI_EXECUTION_TIMEOUT_SECONDS", "9")
    with execution_budget() as budget:
        assert budget.timeout_seconds == 9
        monkeypatch.setenv("GEMINI_EXECUTION_TIMEOUT_SECONDS", "invalid")
        with execution_budget() as inner:
            assert inner is budget


def test_malformed_worker_error_fields_cannot_leak_or_crash_parser():
    error = GeminiExecutionError.from_dict({
        "reason_code": ["secret"], "failure_kind": {"secret": True},
        "status_code": True, "retry_at": "secret",
    })
    assert str(error) == "api_error"
    assert error.to_dict() == {
        "reason_code": "api_error", "failure_kind": "permanent", "retryable": False,
        "retry_at": None, "status_code": None,
    }


@pytest.mark.parametrize("value", ["x" * 1025, "가" * 400])
def test_worker_rejects_oversized_output_as_safe_failure(monkeypatch, value):
    from pipeline import gemini_worker
    from pipeline.transform import gemini_client

    monkeypatch.setattr(execution, "MAX_WORKER_OUTPUT_BYTES", 1024)

    def oversized(**kwargs):
        record_http_dispatch()
        return value

    monkeypatch.setattr(gemini_client, "_generate_summary_json_direct", oversized)
    request = json.dumps({"operation": "summary", "payload": {}}).encode()
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(request)))
    monkeypatch.setattr(sys, "stdout", output)
    assert gemini_worker.main() == 0
    encoded = output.getvalue().encode()
    assert len(encoded) < 1024
    budget = ExecutionBudget()
    result, error = execution._read_worker_output(encoded, budget)
    assert result is None
    assert error.reason_code == "response_incomplete"
    assert error.failure_kind == "permanent"
    assert not error.retryable
    assert budget.http_attempts == 1


def test_parent_rejects_oversized_protocol_before_json_parsing(monkeypatch):
    monkeypatch.setattr(execution, "MAX_WORKER_OUTPUT_BYTES", 128)
    budget = ExecutionBudget()
    result, error = execution._read_worker_output(
        b'{"event":"dispatch","attempt":1}\n' + b"x" * 129, budget
    )
    assert result is None
    assert error.reason_code == "response_incomplete"
    assert budget.http_attempts == 1


@pytest.mark.parametrize("value", ["5", "'5'", '"5"'])
def test_timeout_reads_dotenv_and_environment_takes_precedence(monkeypatch, tmp_path, value):
    from pipeline.transform import gemini_prompt

    path = tmp_path / ".env"
    path.write_text(f"GEMINI_EXECUTION_TIMEOUT_SECONDS={value}\n", encoding="utf-8")
    monkeypatch.setattr(gemini_prompt, "DEFAULT_ENV_PATH", path)
    monkeypatch.delenv("GEMINI_EXECUTION_TIMEOUT_SECONDS", raising=False)
    with execution_budget() as budget:
        assert budget.timeout_seconds == 5
    assert "GEMINI_EXECUTION_TIMEOUT_SECONDS" not in execution.os.environ
    monkeypatch.setenv("GEMINI_EXECUTION_TIMEOUT_SECONDS", "9")
    with execution_budget() as budget:
        assert budget.timeout_seconds == 9


@pytest.mark.parametrize("value", ["", "nan", "inf", "0", "-1", "invalid"])
def test_invalid_dotenv_timeout_is_rejected(monkeypatch, tmp_path, value):
    from pipeline.transform import gemini_prompt

    path = tmp_path / ".env"
    path.write_text(f"GEMINI_EXECUTION_TIMEOUT_SECONDS={value}\n", encoding="utf-8")
    monkeypatch.setattr(gemini_prompt, "DEFAULT_ENV_PATH", path)
    monkeypatch.delenv("GEMINI_EXECUTION_TIMEOUT_SECONDS", raising=False)
    with pytest.raises(GeminiExecutionError):
        with execution_budget():
            pass


@pytest.mark.parametrize("explicit_budget", [False, True])
def test_summary_registration_preserves_ai_budget_boundary(monkeypatch, explicit_budget):
    from unittest.mock import MagicMock

    from support.prepared_summary_storage import _metadata, _prepared_source

    from pipeline import summary_job

    now = [0.0]
    monkeypatch.setattr(execution.time, "monotonic", lambda: now[0])
    monkeypatch.setenv("GEMINI_EXECUTION_TIMEOUT_SECONDS", "120")
    budget = ExecutionBudget(timeout_seconds=120) if explicit_budget else None

    def register(*args, **kwargs):
        assert current_execution() is None
        now[0] += 121
        return 17

    def generate(*args, **kwargs):
        active = current_execution()
        if explicit_budget:
            assert active is budget
            active.check()
        assert active.remaining_seconds() == 120
        raise GeminiExecutionError("response_incomplete")

    monkeypatch.setattr(summary_job, "begin_summary_execution", register)
    monkeypatch.setattr(summary_job, "summarize_prepared_notice", generate)
    record = MagicMock()
    monkeypatch.setattr(summary_job, "record_summary_failure", record)
    result = summary_job.summarize_and_save_prepared_notice(
        MagicMock(), _prepared_source("pdf"), _metadata(),
        expected_source_revision=1, api_key="test-only-key", budget=budget,
    )
    assert result.reason_code == ("api_timeout" if explicit_budget else "response_incomplete")
    record.assert_called_once()
    assert current_execution() is None


def test_default_timeout_uses_isolated_dotenv():
    from pipeline.transform import gemini_prompt

    assert not gemini_prompt.DEFAULT_ENV_PATH.exists()
    with execution_budget() as budget:
        assert budget.timeout_seconds == execution.DEFAULT_TIMEOUT_SECONDS
