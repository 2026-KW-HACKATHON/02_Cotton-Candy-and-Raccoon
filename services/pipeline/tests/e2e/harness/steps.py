"""Load a case and run its steps through the real CLI entry point."""

import io
import json
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psycopg
import pytest
from e2e.harness import gemini_replay, http_replay
from e2e.harness.http_replay import API_KEY, CaseDefinitionError
from e2e.harness.snapshot import normalize, normalize_json, read_anon, read_database
from support.db import database_uri

STEP_TYPES = ("collect", "collect-one", "notice-glossary", "sql")
# Values in the CLI JSON report that change between runs; none are known today.
VOLATILE_REPORT_KEYS: frozenset[str] = frozenset()


@dataclass
class Case:
    name: str
    path: Path
    title: str
    steps: list[dict[str, Any]]
    strict_unused: bool
    now: str | None


def load_case(path: Path) -> Case:
    try:
        data = json.loads((path / "case.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CaseDefinitionError(f"{path.name}/case.json cannot be read: {error}") from None
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list) or not data["steps"]:
        raise CaseDefinitionError(f"{path.name}/case.json needs a non-empty steps list")
    for number, step in enumerate(data["steps"], 1):
        if not isinstance(step, dict) or step.get("type") not in STEP_TYPES:
            raise CaseDefinitionError(f"step {number}: type must be one of {STEP_TYPES}")
        if step["type"] == "sql":
            if not isinstance(step.get("sql"), str) or not str(step.get("reason", "")).strip():
                raise CaseDefinitionError(f"step {number}: sql steps need 'sql' and 'reason'")
        elif not isinstance(step.get("args", []), list):
            raise CaseDefinitionError(f"step {number}: args must be a list")
    return Case(
        name=path.name,
        path=path,
        title=str(data.get("title", path.name)),
        steps=data["steps"],
        strict_unused=bool(data.get("strict_unused", False)),
        now=data.get("now"),
    )



def configure_environment(monkeypatch: pytest.MonkeyPatch, info: dict[str, str]) -> None:
    monkeypatch.setenv("DATABASE_URL", database_uri(info))
    for name in (
        "NOWON_NOTICE_API_KEY", "SEOUL_NEWS_API_KEY", "SEOUL_API_KEY", "GEMINI_API_KEY",
        "STDICT_API_KEY",
    ):
        # GEMINI_API_KEY must be set: otherwise the pipeline falls back to a local .env file.
        monkeypatch.setenv(name, API_KEY)


@dataclass
class StepResult:
    number: int
    step: dict[str, Any]
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    report: Any = None
    error: str | None = None
    http: http_replay.HttpReplay | None = None
    gemini: gemini_replay.GeminiReplay | None = None
    sleeps: list[float] = field(default_factory=list)
    raw_db: dict = field(default_factory=dict)
    raw_anon: dict = field(default_factory=dict)
    snapshot: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    differences: list[str] = field(default_factory=list)


def _patch_sleep(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    """Record retry and pacing waits instead of sleeping; the order of calls is kept."""
    import pipeline.cli  # noqa: F401  (loads every module the CLI can reach)

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    fake_sleep.e2e_fake = True  # type: ignore[attr-defined]
    for name, module in list(sys.modules.items()):
        current = getattr(module, "sleep", None)
        # Replace the real sleep, and the previous step's recorder, so each step owns its list.
        if name.startswith("pipeline") and (
            current is time.sleep or getattr(current, "e2e_fake", False)
        ):
            monkeypatch.setattr(module, "sleep", fake_sleep)


def _drop_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_volatile(v) for k, v in value.items() if k not in VOLATILE_REPORT_KEYS}
    if isinstance(value, list):
        return [_drop_volatile(v) for v in value]
    return value


def _parse_report(stdout: str) -> Any:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        return _drop_volatile(json.loads(stdout))
    except ValueError:
        try:
            return _drop_volatile(json.loads(lines[-1]))
        except ValueError:
            return None


def run_step(
    case: Case,
    number: int,
    step: dict[str, Any],
    conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    previous: StepResult | None,
    secrets: tuple[str, ...],
) -> StepResult:
    from pipeline import cli

    result = StepResult(number, step)
    result.http = http_replay.HttpReplay(
        [http_replay.Route.from_spec(spec, case.path) for spec in step.get("http", [])]
    )
    result.gemini = gemini_replay.GeminiReplay.from_spec(step.get("gemini"), case.path)
    http_replay.install(monkeypatch, result.http)
    gemini_replay.install(monkeypatch, result.gemini)
    _patch_sleep(monkeypatch, result.sleeps)

    if step["type"] == "sql":
        conn.execute(step["sql"])
    else:
        args = [str(arg) for arg in step.get("args", [])]
        if step["type"] == "notice-glossary":
            from pipeline.glossary.cli import main

            argv = args
        else:
            main = cli.main
            argv = [step["type"], *args]
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                result.exit_code = main(argv)
        except SystemExit as exit_:
            result.exit_code = exit_.code if isinstance(exit_.code, int) else 2
        except Exception as error:  # reported as a step problem, with the report still written
            result.error = f"{type(error).__name__}: {error}"
        result.stdout = redact(out.getvalue(), secrets)
        result.stderr = redact(err.getvalue(), secrets)
        result.report = _parse_report(result.stdout)
        expected_exit = step.get("expect_exit", 0)
        if result.error is None and result.exit_code != expected_exit:
            result.problems.append(
                f"exit code {result.exit_code}, case expects {expected_exit}"
            )
    if result.error:
        result.problems.append(f"step raised {result.error}")
    result.problems.extend(result.http.unexpected)
    result.problems.extend(result.gemini.exhausted)
    unused = result.http.unused()
    if unused:
        message = f"registered but not requested: {', '.join(unused)}"
        (result.problems if case.strict_unused else result.warnings).append(message)
    for kind, files in result.gemini.leftovers().items():
        result.problems.append(f"unused Gemini {kind} responses: {', '.join(files)}")

    raw_db, counts, types = read_database(conn)
    raw_anon, denied = read_anon(conn)
    result.raw_db, result.raw_anon = raw_db, raw_anon
    snapshot: dict[str, Any] = {}
    if step["type"] != "sql":
        snapshot |= {
            "exit_code": result.exit_code,
            "report": normalize_json(result.report, previous.report if previous else None),
            "stderr": [line for line in result.stderr.splitlines() if line.strip()],
        }
    snapshot |= {
        "http_calls": list(result.http.calls),
        "gemini_calls": {kind: calls for kind, calls in result.gemini.calls.items() if calls},
        "db": normalize(raw_db, types, previous.raw_db if previous else None),
        "row_counts": counts,
        "anon": normalize(raw_anon, types, previous.raw_anon if previous else None),
    }
    if denied:
        snapshot["anon_denied"] = denied
    result.snapshot = snapshot
    return result


def _mask(secret: str) -> str:
    """Keep the shape of a credential so a reader can tell what was hidden."""
    if secret.startswith("password="):
        return "password=[REDACTED]"
    if secret.startswith(":") and secret.endswith("@"):
        return ":[REDACTED]@"
    return "[REDACTED]"


def redact(text: str, secrets: tuple[str, ...]) -> str:
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        text = text.replace(secret, _mask(secret))
    return text


def credential_patterns(info: dict[str, str]) -> tuple[str, ...]:
    """Credential-shaped strings to hide from reports.

    Only connection-string forms are hidden, never the bare password: a common
    password such as "postgres" would otherwise rewrite ordinary words and make
    expectations depend on the machine that generated them.
    """
    patterns = [database_uri(info), API_KEY]
    password = info.get("password")
    if password:
        for form in {password, quote(password, safe="")}:
            patterns += [f":{form}@", f"password={form}"]
    return tuple(patterns)
