"""Preserve review content while retaining the verified/unverified distinction."""

import json
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from support.gemini_multimodal import _media, _notice, _prepared

from pipeline import summary_job
from pipeline.storage.summaries import save_notice_summary, save_prepared_summary
from pipeline.storage.summary_record import (
    SummaryMetadata,
    SummaryRecord,
    SummaryRecordError,
    build_summary_record,
)
from pipeline.transform import summarize as summarize_module
from pipeline.transform.gemini_prompt import SUMMARY_PROMPT_VERSION
from pipeline.transform.grounding import unknown_summary
from pipeline.transform.prepared_summary import PreparedSummaryResult
from pipeline.transform.summary_schema import MediaSource, NoticeSummary

GENERATED_AT = datetime(2026, 10, 3, 7, tzinfo=UTC)
DEADLINE = date(2026, 10, 20)


def _metadata() -> SummaryMetadata:
    return SummaryMetadata(
        source_hash="cd" * 32,
        model="gemini-test",
        prompt_version=SUMMARY_PROMPT_VERSION,
        attachment_status="all_read",
    )


def _evidence(field: str = "summary", **changes: Any) -> dict[str, Any]:
    return {
        "field": field,
        "excerpt": "행사 안내",
        "source_type": "text",
        "source_id": None,
        "page": None,
        "verification": "text_matched",
    } | changes


def _summary(**changes: Any) -> NoticeSummary:
    data = unknown_summary(_notice("행사 안내")).model_dump(mode="json")
    data.update(
        category="event",
        category_code=26,
        summary="행사 안내",
        uncertainties=[],
        evidence=[_evidence(), _evidence("category_code")],
    )
    return NoticeSummary.model_validate(data | changes)


def _result(summary: NoticeSummary) -> PreparedSummaryResult:
    return PreparedSummaryResult(
        notice_id=17,
        summary=summary,
        warnings=(),
        media_sources=(MediaSource("media_1", "document"), MediaSource("media_2", "image")),
    )


def _date_entry(label: str = "신청 기간") -> dict[str, Any]:
    return {
        "kind": "application",
        "label": label,
        "text": "2026-10-03~2026-10-20",
        "start_date": "2026-10-03",
        "end_date": "2026-10-20",
        "start_time": None,
        "end_time": None,
    }


def _file_evidence(field: str = "summary", kind: str = "document") -> dict[str, Any]:
    return _evidence(
        field,
        source_type=kind,
        source_id="media_1" if kind == "document" else "media_2",
        page=1 if kind == "document" else None,
        verification="file_reference_only",
    )


def _assert_review_content_preserved(result: PreparedSummaryResult) -> None:
    record = build_summary_record(
        result, _metadata(), deadline_on=DEADLINE, generated_at=GENERATED_AT
    )
    assert record.status == "needs_review"
    assert record.result.model_dump(mode="json") == result.summary.model_dump(mode="json")
    assert record.deadline_on is None
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (17,)
    stored = save_prepared_summary(
        conn, result, _metadata(), deadline_on=DEADLINE, generated_at=GENERATED_AT
    )
    assert stored.status == "needs_review"
    assert stored.deadline_on is None
    assert stored.result.summary.model_dump(mode="json") == result.summary.model_dump(mode="json")
    values = cursor.execute.call_args.args[1]
    assert values[:2] == (17, "needs_review")
    assert values[2].obj == result.summary.model_dump(mode="json")
    assert values[3] == (None if result.summary.category == "unknown" else result.summary.category)
    assert values[4:6] == (result.summary.category_code, None)
    assert values[12] == GENERATED_AT


@pytest.mark.parametrize("kind", ["document", "image"])
def test_file_only_summary_preserves_public_content_with_review_status(kind: str) -> None:
    _assert_review_content_preserved(_result(_summary(evidence=[_file_evidence(kind=kind)])))


@pytest.mark.parametrize("include_text_dates", [False, True], ids=["file-dates", "mixed-dates"])
def test_file_application_dates_require_review_even_when_other_claims_match_text(
    monkeypatch: pytest.MonkeyPatch, include_text_dates: bool
) -> None:
    dates = [_date_entry()]
    evidence = [_evidence(), _file_evidence("dates")]
    if include_text_dates:
        dates.insert(0, _date_entry("첫 신청 기간"))
        evidence.insert(1, _evidence("dates"))
    result = _result(_summary(category="application", dates=dates, evidence=evidence))
    _assert_review_content_preserved(result)
    monkeypatch.setattr(summary_job, "summarize_prepared_notice", lambda *_args, **_kwargs: result)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (17,)
    resolver = MagicMock(side_effect=AssertionError("unverified dates must not reach the resolver"))
    stored = summary_job.summarize_and_save_prepared_notice(
        conn, _prepared("행사 안내"), _metadata(), deadline_resolver=resolver,
        expected_source_revision=1,
    )
    assert stored.status == "needs_review"
    resolver.assert_not_called()


@pytest.mark.parametrize(
    "evidence",
    [
        [_evidence(verification=None)],
        [_file_evidence() | {"verification": "text_matched"}],
        [],
    ],
    ids=["unverified-text", "file-claiming-text-match", "missing-summary-evidence"],
)
def test_unverified_or_missing_evidence_cannot_claim_summarized_status(
    evidence: list[dict[str, Any]],
) -> None:
    summary = _summary(evidence=evidence)
    _assert_review_content_preserved(_result(summary))
    with pytest.raises(SummaryRecordError, match="summary_requires_review"):
        SummaryRecord(
            notice_id=17,
            status="summarized",
            metadata=_metadata(),
            result=summary,
            generated_at=GENERATED_AT,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("applicable_area", "월계1동"),
        ("audience", "월계1동 주민"),
        ("action", "방문 신청"),
        ("location", "주민센터"),
        ("dates", [_date_entry()]),
        ("notes", ["신분증 지참"]),
        ("topics", [{"title": "지원 사업", "category": "event", "summary": "행사 안내"}]),
    ],
)
def test_each_populated_claim_requires_its_own_text_evidence(field: str, value: Any) -> None:
    result = _result(_summary(**{field: value}))
    _assert_review_content_preserved(result)
    matched = _result(
        _summary(
            **{field: value}, evidence=[_evidence(), _evidence("category_code"), _evidence(field)]
        )
    )
    record = build_summary_record(
        matched, _metadata(), deadline_on=DEADLINE, generated_at=GENERATED_AT
    )
    assert record.status == "summarized"
    assert record.deadline_on == DEADLINE


def test_media_attachments_do_not_require_review_when_all_claims_have_text_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared("행사 안내", _media("document"), _media("image"))
    monkeypatch.setattr(
        summarize_module,
        "generate_summary_json",
        lambda **_kwargs: json.dumps(_summary().model_dump(mode="json")),
    )
    result = summarize_module.summarize_prepared_notice(prepared, api_key="test-key")
    assert len(result.media_sources) == 2
    record = build_summary_record(
        result, _metadata(), deadline_on=DEADLINE, generated_at=GENERATED_AT
    )
    assert record.status == "summarized"
    assert record.result is not None
    assert record.deadline_on == DEADLINE


@pytest.mark.parametrize("mutation", ["verification", "file-source", "remove-evidence"])
def test_mutating_an_approved_record_cannot_bypass_review_before_sql(mutation: str) -> None:
    record = SummaryRecord(
        notice_id=17,
        status="summarized",
        metadata=_metadata(),
        result=_summary(),
        generated_at=GENERATED_AT,
    )
    assert record.result is not None
    if mutation == "verification":
        record.result.evidence[0].verification = None
    elif mutation == "file-source":
        record.result.evidence[0].source_type = "document"
        record.result.evidence[0].source_id = "media_1"
        record.result.evidence[0].page = 1
    else:
        record.result.evidence.clear()
    conn = MagicMock()
    with pytest.raises(SummaryRecordError, match="summary_requires_review"):
        save_notice_summary(conn, record)
    conn.cursor.assert_not_called()


def test_mutating_review_record_to_schema_invalid_content_is_rejected_before_sql() -> None:
    record = build_summary_record(
        _result(_summary(evidence=[_file_evidence()])),
        _metadata(),
        deadline_on=DEADLINE,
        generated_at=GENERATED_AT,
    )
    record.result.category_code = "27"
    conn = MagicMock()
    with pytest.raises(SummaryRecordError, match="invalid_summary_result"):
        save_notice_summary(conn, record)
    conn.cursor.assert_not_called()
