"""Helpers shared from test_prepared_summary_storage.py."""

from typing import Any

from pipeline.storage.summary_record import (
    SummaryMetadata,
)
from pipeline.transform.gemini_client import DEFAULT_MODEL
from pipeline.transform.gemini_prompt import SUMMARY_PROMPT_VERSION
from pipeline.transform.grounding import unknown_summary
from support.gemini_multimodal import _media, _notice, _prepared

__all__ = [
    "_metadata",
    "_prepared_source",
    "_response",
]


def _metadata(attachment_status: str = "all_read") -> SummaryMetadata:
    return SummaryMetadata(
        source_hash="cd" * 32,
        model=DEFAULT_MODEL,
        prompt_version=SUMMARY_PROMPT_VERSION,
        attachment_status=attachment_status,
    )


def _response(kind: str) -> dict[str, Any]:
    data = unknown_summary(_notice("행사 안내")).model_dump(mode="json")
    data.update(category="event", category_code=26, summary="행사 안내", uncertainties=[])
    evidence: dict[str, Any] = {"field": "summary", "excerpt": "행사 안내"}
    if kind in {"pdf", "image"}:
        evidence.update(
            source_type="document" if kind == "pdf" else "image",
            source_id="media_1",
            page=1 if kind == "pdf" else None,
        )
    data["evidence"] = [evidence, evidence | {"field": "category_code"}]
    if kind == "mixed":
        data["dates"] = [
            {
                "kind": "event",
                "label": "행사",
                "text": "2026-10-03~2026-10-20",
                "start_date": "2026-10-03",
                "end_date": "2026-10-20",
                "start_time": None,
                "end_time": None,
            }
        ]
        data["notes"] = ["참가비 무료"]
        data["card_summaries"]["deadline"] = "2026-10-03부터 2026-10-20까지 행사예요."
        data["card_summaries"]["notes"] = "참가비는 무료예요."
        data["evidence"].extend(
            [
                {
                    "field": "dates",
                    "excerpt": "행사: 2026-10-03~2026-10-20",
                    "source_type": "document",
                    "source_id": "media_2",
                    "page": 2,
                },
                {
                    "field": "notes",
                    "excerpt": "참가비 무료",
                    "source_type": "image",
                    "source_id": "media_1",
                    "page": None,
                },
            ]
        )
    return data


def _prepared_source(kind: str) -> Any:
    if kind == "text":
        return _prepared("행사 안내")
    if kind == "hwp":
        return _prepared("", attachment_text=True)
    if kind == "pdf":
        return _prepared("", _media("document"))
    if kind == "image":
        return _prepared("", _media("image"))
    return _prepared("행사 안내", _media("image"), _media("document"), attachment_text=True)
