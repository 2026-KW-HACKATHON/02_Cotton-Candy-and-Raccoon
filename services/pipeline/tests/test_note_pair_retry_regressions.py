"""A retry must not separate a paraphrased restriction from its own exception."""

import json
from copy import deepcopy
from typing import Any

import pytest
from test_gemini_multimodal import PreparedInput, _media

from pipeline.transform import summarize as summarize_module
from pipeline.transform.grounding import REVIEW_NOTE, unknown_summary
from pipeline.transform.notice_input import NoticeInput, render_notice_input
from pipeline.transform.summary_schema import NoticeSummary

TITLE = "문화 프로그램 안내"
RESTRICTION = "취소가 금지됩니다"
EXCEPTION = "미리 연락하면 취소 가능합니다"
PAIR_SOURCE = "취소할 수 없습니다. 다만 사전 연락하면 취소할 수 있습니다."
STANDALONE = ["동반자 등록 필요", "대중교통 이용", "개인 물품 지참", "안내 문자 확인"]


def _notice(source_type: str, rule: str) -> NoticeInput:
    # The text omission and the missing PDF reference each use the same one retry.
    body = "\n".join([TITLE, "참가비: 30,000원", *STANDALONE, rule])
    return NoticeInput.model_validate(
        {
            "title": TITLE,
            "body_text": body if source_type == "text" else "",
            "publisher": "노원구청",
            "reference_datetime": "2026-10-04T19:00:00+09:00",
        }
    )


def _output(
    notice: NoticeInput,
    notes: list[str],
    rule: str,
    source_type: str,
    *,
    initial: bool,
) -> dict[str, Any]:
    reference = {
        "source_type": source_type,
        "source_id": None if initial or source_type == "text" else "media_1",
        "page": 1 if source_type == "document" else None,
    }
    data = unknown_summary(notice, has_media=source_type == "document").model_dump()
    data.update(
        category="event",
        summary=TITLE,
        publisher=notice.publisher,
        notice_update="new",
        notes=notes,
        card_summaries={
            "audience": None, "deadline": None, "action": None,
            "notes": " ".join(f"“{note}”를 확인해 주세요." for note in notes) or None,
        },
        uncertainties=[],
        evidence=[
            {"field": "summary", "excerpt": TITLE, **reference},
            {"field": "notes", "excerpt": rule, **reference},
            *(
                {"field": "notes", "excerpt": note, **reference}
                for note in notes
                if note in STANDALONE
            ),
        ],
    )
    return data


def _summarize_pair(
    monkeypatch: pytest.MonkeyPatch,
    source_type: str,
    rule: str,
    restriction: str,
    correction: str = EXCEPTION,
) -> tuple[NoticeSummary, list[dict[str, Any]]]:
    notice = _notice(source_type, rule)
    outputs = [
        _output(notice, [*STANDALONE, restriction], rule, source_type, initial=True),
        _output(notice, [correction], rule, source_type, initial=False),
    ]
    requests: list[dict[str, Any]] = []

    def generate(**kwargs: Any) -> str:
        index = len(requests)
        requests.append(deepcopy(kwargs))
        assert index < 2, "a note pair correction must share the single retry budget"
        return json.dumps(outputs[index], ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    if source_type == "text":
        result = summarize_module.summarize_notice(notice, api_key="test-key")
    else:
        document = _media("document", b"%PDF original cancellation rule")
        prepared = PreparedInput(
            17, notice, [{"type": "text", "text": render_notice_input(notice)}, document]
        )
        result = summarize_module.summarize_prepared_notice(prepared, api_key="test-key").summary
        assert prepared.calls == ["to_gemini_input"]
        assert [block for block in requests[0]["notice_text"] if block["type"] == "document"] == [
            document
        ]
        assert [block for block in requests[1]["notice_text"] if block["type"] == "document"] == [
            document
        ]
    return result, requests


@pytest.mark.parametrize("source_type", ["text", "document"])
def test_paraphrased_cancellation_pair_is_not_split_when_six_notes_exceed_the_limit(
    monkeypatch: pytest.MonkeyPatch, source_type: str
) -> None:
    result, requests = _summarize_pair(monkeypatch, source_type, PAIR_SOURCE, RESTRICTION)

    assert len(requests) == 2
    assert len(result.notes) <= 5
    assert (RESTRICTION in result.notes) == (EXCEPTION in result.notes)
    assert REVIEW_NOTE in result.uncertainties
    assert result.summary == TITLE
    assert result.publisher == "노원구청"
    assert set(result.notes).issubset({*STANDALONE, RESTRICTION, EXCEPTION})
    if RESTRICTION in result.notes:
        assert any(
            item.field == "notes"
            and item.excerpt == PAIR_SOURCE
            and item.source_type == source_type
            and item.verification
            == ("text_matched" if source_type == "text" else "file_reference_only")
            for item in result.evidence
        )


@pytest.mark.parametrize("source_type", ["text", "document"])
def test_shared_evidence_does_not_link_parking_restriction_to_cancellation_exception(
    monkeypatch: pytest.MonkeyPatch, source_type: str
) -> None:
    unrelated_source = "주차할 수 없습니다. 다만 사전 연락하면 취소할 수 있습니다."
    result, requests = _summarize_pair(monkeypatch, source_type, unrelated_source, "주차 불가")

    assert len(requests) == 2
    # Shared citation and generic restriction words do not make the actions equal.
    assert result.notes == [*STANDALONE, "주차 불가"]
    assert EXCEPTION not in result.notes
    assert REVIEW_NOTE in result.uncertainties
    assert result.summary == TITLE


@pytest.mark.parametrize("source_type", ["text", "document"])
def test_negated_participation_condition_does_not_negate_cancellation_permission(
    monkeypatch: pytest.MonkeyPatch, source_type: str
) -> None:
    rule = "취소할 수 없습니다. 다만 참여할 수 없는 경우 사전 연락하면 취소할 수 있습니다."
    exception = "참여하지 못하면 미리 연락 후 취소 가능합니다"
    result, requests = _summarize_pair(
        monkeypatch, source_type, rule, RESTRICTION, correction=exception
    )

    assert len(requests) == 2
    assert len(result.notes) <= 5
    assert (RESTRICTION in result.notes) == (exception in result.notes)
    assert REVIEW_NOTE in result.uncertainties
    assert result.summary == TITLE


@pytest.mark.parametrize("source_type", ["text", "document"])
def test_negated_cancellation_permission_is_not_linked_as_an_exception(
    monkeypatch: pytest.MonkeyPatch, source_type: str
) -> None:
    negated_permission = "취소는 가능하지 않습니다"
    result, requests = _summarize_pair(
        monkeypatch, source_type, PAIR_SOURCE, RESTRICTION, correction=negated_permission
    )

    assert len(requests) == 2
    assert result.notes == [*STANDALONE, RESTRICTION]
    assert negated_permission not in result.notes
    assert REVIEW_NOTE in result.uncertainties
    assert result.summary == TITLE
