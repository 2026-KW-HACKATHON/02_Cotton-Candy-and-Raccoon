"""Adversarial pipeline boundary checks without provider or database access."""

import json
import sys
from base64 import b64encode
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pipeline import summary_job
from pipeline.storage.summaries import GUARDED_UPSERT_SUMMARY, REGISTER_SUMMARY_EXECUTION
from pipeline.storage.summary_metadata import build_summary_metadata
from pipeline.storage.summary_record import SummaryRecordError, build_summary_record
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.transform import summarize as summarize_module
from pipeline.transform import summary_cli
from pipeline.transform.gemini_prompt import SUMMARY_PROMPT_VERSION, load_summary_prompt
from pipeline.transform.grounding import unknown_summary
from pipeline.transform.notice_input import NoticeInput, render_notice_input
from pipeline.transform.prepared_summary import PreparedSummaryResult


def _notice(*, cost: bool = False) -> NoticeInput:
    return NoticeInput.model_validate({
        "title": "지원 사업 신청",
        "body_text": (
            "지원 사업 신청\n대상: 월계1동 주민\n"
            "신청 기간: 2026-10-03~2026-10-20\n"
            "신청방법: 월계1동 주민센터 방문 신청\n"
            "장소: 월계1동 주민센터\n신분증 지참"
            + ("\n참가비 20,000원" if cost else "")
        ),
        "reference_datetime": "2026-10-07T12:00:00+09:00",
    })


def _response(notice: NoticeInput, *, include_cost: bool = False) -> dict:
    data = unknown_summary(notice).model_dump(mode="json")
    data.update(
        category="application", category_code=27, summary="지원 사업 신청",
        audience="월계1동 주민", audience_scope="conditional",
        action="월계1동 주민센터 방문 신청", action_requirement="optional",
        location="월계1동 주민센터", status="open", notice_update="new",
        dates=[{
            "kind": "application", "label": "신청 기간", "text": None,
            "start_date": "2026-10-03", "end_date": "2026-10-20",
            "start_time": None, "end_time": None,
        }],
        notes=["신분증 지참", *(["참가비 20,000원"] if include_cost else [])],
        uncertainties=[],
        evidence=[
            {"field": field, "excerpt": excerpt}
            for field, excerpt in (
                ("summary", "지원 사업 신청"), ("category", "지원 사업 신청"),
                ("category_code", "지원 사업 신청"),
                ("audience", "대상: 월계1동 주민"),
                ("action", "신청방법: 월계1동 주민센터 방문 신청"),
                ("action_requirement", "신청방법: 월계1동 주민센터 방문 신청"),
                ("location", "장소: 월계1동 주민센터"),
                ("dates", "신청 기간: 2026-10-03~2026-10-20"),
                ("notes", "신분증 지참"),
                *(([("notes", "참가비 20,000원")]) if include_cost else []),
            )
        ],
        card_summaries={
            "audience": "월계1동 주민이 대상이에요.",
            "deadline": "신청은 2026-10-03부터 2026-10-20까지예요.",
            "action": "희망하시면 월계1동 주민센터를 방문해 신청해 주세요.",
            "notes": "신분증을 지참해 주세요.",
        },
    )
    return data


def _generate(monkeypatch: pytest.MonkeyPatch, outputs: list[dict]) -> list[dict]:
    pending = iter(outputs)
    requests = []

    def generate(**kwargs):
        requests.append(deepcopy(kwargs))
        return json.dumps(next(pending), ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    return requests


def _prepared(notice: NoticeInput):
    blocks = [
        {"type": "text", "text": render_notice_input(notice)},
        {"type": "document", "mime_type": "application/pdf",
         "data": b64encode(b"%PDF-original-not-replaced").decode("ascii")},
        {"type": "image", "mime_type": "image/png",
         "data": b64encode(b"original-image-not-replaced").decode("ascii")},
    ]
    return SimpleNamespace(
        notice_id=17, notice=notice, failures=(), warnings=(),
        blocks=blocks, to_gemini_input=lambda: deepcopy(blocks),
    )


def test_job_shape_retry_preserves_source_payload_facts_and_persisted_v5_cards(monkeypatch):
    notice = _notice()
    prepared = _prepared(notice)
    before = deepcopy(prepared.blocks)
    first = _response(notice)
    first["card_summaries"]["action"] = "방문 신청해야 합니다."
    retry = _response(notice)
    retry["audience"] = "다른 지역 주민"
    requests = _generate(monkeypatch, [first, retry])
    metadata = build_summary_metadata(
        body_text=notice.body_text, total_file_count=2, read_file_count=2,
        model="gemini-3.5-flash-lite",
    )
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (17,)
    stored = summary_job.summarize_and_save_prepared_notice(
        conn, prepared, metadata, api_key="audit-only-mocked-key"
    )

    assert len(requests) == 2
    assert requests[0]["prompt"] == requests[1]["prompt"] == load_summary_prompt()
    assert requests[1]["notice_text"][:-1] == requests[0]["notice_text"]
    assert prepared.blocks == before
    execution_calls = cursor.execute.call_args_list
    assert execution_calls[0].args == (REGISTER_SUMMARY_EXECUTION, (17,))
    assert execution_calls[-1].args[0] == GUARDED_UPSERT_SUMMARY
    guarded_values = execution_calls[-1].args[1]
    assert guarded_values[:2] == (17, 17)  # Notice ID and the mocked registration token.
    values = guarded_values[2:]
    persisted = values[2].obj
    assert values[9] == SUMMARY_PROMPT_VERSION == "notice-summary-v6-card-grounding"
    assert persisted["audience"] == first["audience"]
    assert persisted["action"] == first["action"]
    assert persisted["card_summaries"] == retry["card_summaries"]
    view = build_notice_summary_view(
        status=stored.status, result=persisted, attachment_status="all_read"
    )
    assert view.content is not None
    assert view.content.cards.action.text == persisted["card_summaries"]["action"]
    conn.commit.assert_not_called()


def test_prompt_upgrade_identity_requires_v5_metadata_before_any_generation(monkeypatch):
    notice = _notice()
    metadata = build_summary_metadata(
        body_text=notice.body_text, total_file_count=0, read_file_count=0,
        model="gemini-3.5-flash-lite",
    )
    old = replace(metadata, prompt_version="notice-summary-v4-card-summaries")
    assert old.source_hash == metadata.source_hash
    requests = _generate(monkeypatch, [_response(notice)])
    conn = MagicMock()
    with pytest.raises(SummaryRecordError, match="prompt_version_mismatch"):
        summary_job.summarize_and_save_prepared_notice(conn, _prepared(notice), old, api_key="test")
    assert requests == []
    conn.cursor.assert_not_called()


def test_cli_runs_v5_generation_and_returns_ai_result_without_a_persisted_view(
    monkeypatch, tmp_path, capsys
):
    notice = _notice()
    input_path = tmp_path / "notice.json"
    input_path.write_text(notice.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["summarize-notice", str(input_path)])
    monkeypatch.setattr(summarize_module, "load_gemini_api_key", lambda: "audit-mocked-key")
    requests = _generate(monkeypatch, [_response(notice)])

    assert summary_cli.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert len(requests) == 1
    assert requests[0]["prompt"] == load_summary_prompt()
    assert output["card_summaries"] == _response(notice)["card_summaries"]
    assert output["audience"] == "월계1동 주민"
    assert "content" not in output  # The CLI emits AI JSON, not an app's persisted-row view.


def test_empty_card_guidance_is_redundant_data_without_changing_the_stored_null():
    notice = _notice().model_copy(update={"title": "자료 없음", "body_text": ""})
    summary = unknown_summary(notice)
    before = summary.model_dump(mode="json")
    view = build_notice_summary_view(
        status="needs_review", result=summary, attachment_status="none"
    )
    assert view.content is not None
    for key in ("audience", "deadline", "action", "notes"):
        card = getattr(view.content.cards, key)
        assert card.availability == "not_provided"
        assert card.items == []
        assert card.text == card.guidance == "원문을 확인해 주세요"
        assert getattr(summary.card_summaries, key) is None
    assert summary.model_dump(mode="json") == before


def test_notes_retry_preserves_uncertain_card_with_review_and_no_sorting_deadline(monkeypatch):
    """Preserve uncertain AI text without publishing the omission as verified.

    A missing fee in the retained card must mark both the public view and stored
    record for review. Original notes stay available, and review content cannot
    contribute a sorting deadline; no local rewrite of AI prose is required.
    """
    notice = _notice(cost=True)
    first = _response(notice)
    retry = _response(notice, include_cost=True)
    # Fresh responses must include prose for their nonempty notes. Keeping the
    # first incomplete prose still exercises the semantic review boundary.
    retry["card_summaries"]["notes"] = first["card_summaries"]["notes"]
    requests = _generate(monkeypatch, [first, retry])
    summary = summarize_module.summarize_notice(notice, api_key="test")
    view = build_notice_summary_view(
        status="summarized", result=summary, attachment_status="none"
    )
    assert len(requests) == 2
    assert summary.notes == ["신분증 지참", "참가비 20,000원"]
    assert view.content is not None
    assert view.content.cards.notes.text == first["card_summaries"]["notes"]
    assert view.status == "needs_review"
    assert view.message == "원문 확인 요함"
    assert view.content.headline.text == summary.summary
    metadata = build_summary_metadata(
        body_text=notice.body_text, total_file_count=0, read_file_count=0,
        model="gemini-3.5-flash-lite",
    )
    record = build_summary_record(
        PreparedSummaryResult(notice_id=17, summary=summary, warnings=()), metadata,
        deadline_on=date(2026, 10, 20), generated_at=datetime(2026, 10, 7, tzinfo=UTC),
    )
    assert record.status == "needs_review"
    assert record.deadline_on is None
    assert record.result is not None
    assert record.result.notes == summary.notes
    assert record.result.card_summaries.notes == first["card_summaries"]["notes"]


def test_style_valid_card_cannot_add_age_restriction_with_clean_public_status(monkeypatch):
    notice = _notice()
    data = _response(notice)
    data["card_summaries"]["audience"] = "만 65세 이상 월계1동 주민만 대상이에요."
    requests = _generate(monkeypatch, [data])
    summary = summarize_module.summarize_notice(notice, api_key="test")
    view = build_notice_summary_view(
        status="summarized", result=summary, attachment_status="none"
    )
    assert len(requests) == 1
    assert summary.audience == "월계1동 주민"
    assert view.content is not None
    assert view.status == "needs_review" or "65세" not in view.content.cards.audience.text
