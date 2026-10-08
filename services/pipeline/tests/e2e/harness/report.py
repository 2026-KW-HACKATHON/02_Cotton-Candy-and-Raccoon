"""Write a readable Markdown record of what each step did, pass or fail."""

import json
from pathlib import Path
from typing import Any

from e2e.harness.steps import Case, StepResult


def _code(text: str, lang: str = "") -> str:
    fence = "````" if "```" in text else "```"
    return f"{fence}{lang}\n{text.rstrip()}\n{fence}\n"


def _json(value: Any) -> str:
    return _code(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False), "json")


def _command(step: dict[str, Any]) -> str:
    if step["type"] == "sql":
        return f"sql ({step.get('reason', '')})"
    return " ".join(["pipeline", step["type"], *map(str, step.get("args", []))])


def _notice_lines(rows: dict[str, dict[str, Any]]) -> list[str]:
    lines = []
    for key, row in rows.items():
        flags = ", ".join(
            f"{name}={row[name]}"
            for name in ("is_visible", "is_modified", "content_revision")
            if name in row
        )
        lines.append(f"- `{key}` {row.get('title', '')!r} ({flags})")
    return lines or ["- (없음)"]


def write_report(path: Path, case: Case, results: list[StepResult], status: str) -> None:
    parts = [f"# {case.name}\n", f"{case.title}\n", f"결과: **{status}**\n"]
    for result in results:
        snap = result.snapshot
        parts.append(f"\n## step {result.number}: `{_command(result.step)}`\n")
        if result.step["type"] == "sql":
            parts.append(_code(result.step["sql"], "sql"))
        else:
            parts.append(f"- exit code: {result.exit_code}\n")
        for title, items in (
            ("문제", result.problems),
            ("기대값과의 차이", result.differences),
            ("경고", result.warnings),
        ):
            if items:
                parts.append(f"\n### {title}\n")
                parts.append(_code("\n".join(items)))
        if result.http is not None:
            parts.append("\n### 외부 요청\n")
            parts.append(_code("\n".join(result.http.calls) or "(없음)"))
        if result.gemini is not None:
            counts = {kind: len(calls) for kind, calls in result.gemini.calls.items()}
            parts.append(f"\n- Gemini 호출: {counts}\n")
        if result.sleeps:
            parts.append(f"- 대기(sleep) 호출: {result.sleeps}\n")
        if result.report is not None:
            parts.append("\n### CLI 보고서\n")
            parts.append(_json(result.report))
        if result.stderr.strip():
            parts.append("\n### stderr\n")
            parts.append(_code(result.stderr))
        if snap:
            parts.append("\n### 테이블별 행 수\n")
            parts.append(_json(snap.get("row_counts", {})))
            parts.append("\n### 저장된 공지\n")
            parts.append("\n".join(_notice_lines(snap["db"].get("notices", {}))) + "\n")
            parts.append("\n### 앱(anon)에 보이는 공지\n")
            parts.append("\n".join(_notice_lines(snap["anon"].get("notices", {}))) + "\n")
            parts.append("\n<details><summary>스냅샷 전체</summary>\n\n")
            parts.append(_json(snap))
            parts.append("\n</details>\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(parts), encoding="utf-8")
