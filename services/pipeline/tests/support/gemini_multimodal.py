"""Helpers shared from test_gemini_multimodal.py."""

from base64 import b64encode
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from google.genai import types

from pipeline.transform import gemini_client
from pipeline.transform.notice_input import NoticeInput, render_notice_input

__all__ = [
    "PreparationIssue",
    "PreparedInput",
    "_media",
    "_mock_sdk",
    "_notice",
    "_prepared",
]


@dataclass(frozen=True)
class PreparationIssue:
    stage: str
    item_id: int
    reason_code: str


@dataclass
class PreparedInput:
    """Match #13's handoff contract without importing or copying its implementation."""

    notice_id: int
    notice: NoticeInput
    blocks: list[dict[str, str]]
    failures: tuple[PreparationIssue, ...] = ()
    warnings: tuple[PreparationIssue, ...] = ()
    calls: list[str] = field(default_factory=list)

    def to_gemini_input(self) -> list[dict[str, str]]:
        self.calls.append("to_gemini_input")
        if self.failures:
            raise ValueError("summary input preparation is incomplete")
        return deepcopy(self.blocks)


def _notice(body: str = "") -> NoticeInput:
    return NoticeInput.model_validate(
        {
            "title": "행사 안내",
            "body_text": body,
            "reference_datetime": "2026-10-03T12:00:00+09:00",
        }
    )


def _media(kind: str, marker: bytes = b"original attachment") -> dict[str, str]:
    return {
        "type": kind,
        "mime_type": "application/pdf" if kind == "document" else "image/png",
        "data": b64encode(marker).decode("ascii"),
    }


def _prepared(
    body: str = "", *media: dict[str, str], attachment_text: bool = False
) -> PreparedInput:
    notice = _notice(body)
    if attachment_text:
        notice = NoticeInput.model_validate(
            notice.model_dump() | {"attachments": [{"name": "안내.hwp", "text": "행사 안내"}]}
        )
    return PreparedInput(
        17,
        notice,
        [{"type": "text", "text": render_notice_input(notice)}, *deepcopy(media)],
    )


def _mock_sdk(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Keep Google's real serializer and response parser behind MockTransport."""
    real_client = gemini_client.genai.Client

    def client_factory(*, api_key: str, http_options: types.HttpOptions) -> Any:
        options = http_options.model_copy(
            update={
                "base_url": "https://gemini.invalid",
                "client_args": {"transport": httpx.MockTransport(handler)},
                "retry_options": types.HttpRetryOptions(attempts=1),
            }
        )
        return real_client(api_key=api_key, http_options=options)

    monkeypatch.setattr(gemini_client.genai, "Client", client_factory)
