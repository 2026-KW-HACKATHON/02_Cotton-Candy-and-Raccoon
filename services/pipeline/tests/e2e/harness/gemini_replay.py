"""Replay Gemini responses from per-kind queues instead of calling the API.

Both pipeline clients construct ``google.genai.Client``: easy text calls
``models.generate_content`` and summaries call ``interactions.create``. One fake
client serves both, so the attributes below mirror only what those modules read.
"""

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import httpx
import pytest
from e2e.harness.http_replay import CaseDefinitionError
from google import genai
from google.genai import errors

Kind = Literal["easy_text", "summary"]
KINDS: tuple[Kind, ...] = ("easy_text", "summary")


class GeminiQueueError(AssertionError):
    """Gemini was called more or fewer times than the case declared."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _describe_input(value: object) -> list[dict[str, object]]:
    """Block kinds and text hashes, without storing the notice text itself."""
    if isinstance(value, str):
        return [{"type": "text", "sha256": _sha(value), "length": len(value)}]
    if isinstance(value, (list, tuple)):
        return [item for part in value for item in _describe_input(part)]
    if isinstance(value, dict):
        kind = str(value.get("type", "object"))
        block: dict[str, object] = {"type": kind}
        if isinstance(value.get("text"), str):
            block |= {"sha256": _sha(value["text"]), "length": len(value["text"])}
        for name in ("mime_type", "uri"):
            if isinstance(value.get(name), str):
                block[name] = value[name]
        return [block]
    dumped = getattr(value, "model_dump", None)
    if callable(dumped):
        return _describe_input(dumped(mode="json", exclude_none=True))
    return [{"type": type(value).__name__}]


@dataclass
class Reply:
    source: str
    payload: Any


@dataclass
class GeminiReplay:
    queues: dict[Kind, deque[Reply]]
    calls: dict[Kind, list[dict[str, object]]] = field(
        default_factory=lambda: {kind: [] for kind in KINDS}
    )

    @classmethod
    def from_spec(cls, spec: dict[str, Any] | None, case_dir: Path) -> "GeminiReplay":
        spec = spec or {}
        unknown = set(spec) - set(KINDS)
        if unknown:
            raise CaseDefinitionError(f"unknown gemini kinds: {sorted(unknown)}")
        queues: dict[Kind, deque[Reply]] = {}
        for kind in KINDS:
            replies = deque()
            for name in spec.get(kind, []):
                path = (case_dir / name).resolve()
                if case_dir.resolve() not in path.parents or not path.is_file():
                    raise CaseDefinitionError(f"gemini response file not found: {name}")
                replies.append(Reply(name, json.loads(path.read_text(encoding="utf-8"))))
            queues[kind] = replies
        return cls(queues)

    def next(self, kind: Kind, request: dict[str, object]) -> Any:
        self.calls[kind].append(request)
        if not self.queues[kind]:
            raise GeminiQueueError(f"Unexpected Gemini {kind} call: no response left in the case")
        reply = self.queues[kind].popleft()
        payload = reply.payload
        if isinstance(payload, dict) and "__error__" in payload:
            _raise(kind, str(payload["__error__"]))
        if isinstance(payload, dict) and "__before_return_sql__" in payload:
            raise CaseDefinitionError(
                "__before_return_sql__ is reserved for summary cases (Phase B)"
            )
        return payload

    def leftovers(self) -> dict[str, list[str]]:
        return {kind: [r.source for r in queue] for kind, queue in self.queues.items() if queue}


def _raise(kind: Kind, error: str) -> None:
    request = httpx.Request("POST", "https://generativelanguage.googleapis.com/replay")
    if error == "timeout":
        raise httpx.ReadTimeout("replayed timeout", request=request)
    if error == "connection":
        raise httpx.ConnectError("replayed connection error", request=request)
    if error == "api_error":
        raise errors.ServerError(
            500, {"error": {"code": 500, "message": "replayed", "status": "INTERNAL"}}
        )
    if error == "too_large":
        raise errors.ClientError(
            413, {"error": {"code": 413, "message": "replayed", "status": "INVALID_ARGUMENT"}}
        )
    raise CaseDefinitionError(f"unknown gemini __error__ {error!r} for {kind}")


def _as_text(payload: Any) -> str:
    return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)


class _Models:
    def __init__(self, replay: GeminiReplay) -> None:
        self._replay = replay

    def generate_content(self, *, model: str, contents: object, config: object = None) -> object:
        payload = self._replay.next(
            "easy_text", {"model": model, "input": _describe_input(contents)}
        )
        candidate = SimpleNamespace(finish_reason="STOP")
        return SimpleNamespace(prompt_feedback=None, candidates=[candidate], text=_as_text(payload))


class _Interactions:
    def __init__(self, replay: GeminiReplay) -> None:
        self._replay = replay

    def create(self, *, model: str, input: object, **_: object) -> object:
        payload = self._replay.next("summary", {"model": model, "input": _describe_input(input)})
        return SimpleNamespace(status="completed", errors=None, output_text=_as_text(payload))


class FakeClient:
    def __init__(self, replay: GeminiReplay, **_: object) -> None:
        self.models = _Models(replay)
        self.interactions = _Interactions(replay)

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def close(self) -> None:
        return None


def install(monkeypatch: pytest.MonkeyPatch, replay: GeminiReplay) -> None:
    monkeypatch.setattr(genai, "Client", lambda **kwargs: FakeClient(replay, **kwargs))
