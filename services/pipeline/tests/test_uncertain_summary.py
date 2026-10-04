"""Preserve uncertain model output without claiming its contents were verified."""

import json
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any

import pytest

from pipeline.storage.summary_record import SummaryMetadata, build_summary_record
from pipeline.transform import summarize as summarize_module
from pipeline.transform.grounding import REVIEW_NOTE, ground_summary, unknown_summary
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.prepared_summary import PreparedSummaryResult
from pipeline.transform.summary_schema import MediaSource, NoticeSummary, SummaryValidationError

TITLE = "주민 문화 프로그램 안내"
BODY = (
    TITLE + "\n대상: 서울 거주 주민\n신청방법: 현장 방문 신청\n"
    "행사: 2026-10-10 10:00~11:00\n참가비 무료\n장소: 월계문화센터"
)


def _notice(*, body: str = BODY, attachment: bool = False) -> NoticeInput:
    return NoticeInput.model_validate(
        {
            "title": TITLE,
            "body_text": "" if attachment else body,
            "attachments": [{"name": "안내.hwp", "text": body}] if attachment else [],
            "publisher": "노원구청",
            "department": "문화정책팀",
            "reference_datetime": "2026-10-04T19:00:00+09:00",
        }
    )


def _output(notice: NoticeInput) -> dict[str, Any]:
    """Every value has valid shape, even where literal grounding is inconclusive."""
    data = unknown_summary(notice).model_dump()
    data.update(
        category="event",
        summary="주민을 위한 문화 체험",
        audience="서울에 사는 주민",
        audience_scope="general",
        action="센터를 방문하여 신청하기",
        action_requirement="optional",
        location="월계동 문화센터",
        dates=[
            {
                "kind": "operation",
                "label": "문화 체험",
                "text": "10월 10일 오전 10시부터 11시",
                "start_date": "2026-10-10",
                "end_date": "2026-10-10",
                "start_time": "10:00",
                "end_time": "11:00",
            }
        ],
        status="upcoming",
        status_detail="참여 신청 안내 확인",
        notes=["참가비를 내지 않고 참여 가능"],
        topics=[{"title": "문화 교실", "category": "event", "summary": "주민과 함께하는 체험"}],
        uncertainties=[],
        evidence=[
            {"field": field, "excerpt": excerpt}
            for field, excerpt in (
                ("summary", TITLE),
                ("audience", "대상: 서울 거주 주민"),
                ("action", "신청방법: 현장 방문 신청"),
                ("location", "장소: 월계문화센터"),
                ("dates", "행사: 2026-10-10 10:00~11:00"),
                ("notes", "참가비 무료"),
                ("topics", "확인되지 않은 문화 교실 인용"),
            )
        ],
    )
    return data


def _validate(data: dict[str, Any], notice: NoticeInput, *, mixed: bool = False) -> NoticeSummary:
    sources = (MediaSource("media_1", "document"),) if mixed else ()
    return summarize_module._validate_summary(
        json.dumps(data, ensure_ascii=False), notice, media_sources=sources
    )


def test_notes_retry_preserves_an_exact_quote_for_a_paraphrased_note() -> None:
    notice = _notice()
    first = _validate(_output(notice), notice)
    retry = first.model_copy(update={"notes": [], "evidence": [], "uncertainties": []})

    result = summarize_module._merge_note_correction(first, retry, notice)

    assert result.notes == ["참가비를 내지 않고 참여 가능"]
    quote = next(item for item in result.evidence if item.field == "notes")
    assert quote.excerpt == "참가비 무료"
    assert quote.verification == "text_matched"


@pytest.mark.parametrize("mixed", (False, True), ids=("text", "mixed"))
def test_wrapper_preserves_schema_valid_title_audience_action_dates_notes_and_topics(
    mixed: bool,
) -> None:
    notice = _notice()
    output = _output(notice)
    original = deepcopy(output)
    sources = (MediaSource("media_1", "document"),) if mixed else ()

    # The diagnostic transform still identifies claims it cannot directly ground.
    strict = ground_summary(NoticeSummary.model_validate(output), notice, media_sources=sources)
    assert strict.summary == REVIEW_NOTE
    assert strict.audience is None
    assert strict.action is None
    assert strict.dates == []
    assert strict.notes == []
    assert strict.topics == []

    result = _validate(output, notice, mixed=mixed)

    for field in (
        "summary",
        "audience",
        "audience_scope",
        "action",
        "action_requirement",
        "location",
        "dates",
        "notes",
        "topics",
        "status",
        "status_detail",
    ):
        assert result.model_dump()[field] == output[field]
    assert result.uncertainties == [REVIEW_NOTE]
    assert output == original
    assert result.evidence[-1].verification is None
    assert all(item.verification == "text_matched" for item in result.evidence[:-1])


@pytest.mark.parametrize("mixed", (False, True), ids=("text", "mixed"))
def test_missing_all_evidence_marks_review_without_erasing_contents(mixed: bool) -> None:
    notice = _notice()
    output = _output(notice)
    output["evidence"] = []

    result = _validate(output, notice, mixed=mixed)

    assert result.summary == output["summary"]
    assert result.audience == output["audience"]
    assert result.action == output["action"]
    assert result.notes == output["notes"]
    assert result.dates[0].model_dump() == output["dates"][0]
    assert result.evidence == []
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize(
    "reference",
    (
        {"source_type": "text", "source_id": None, "page": None},
        {"source_type": "document", "source_id": "media_99", "page": 1},
        {"source_type": "document", "source_id": "media_1", "page": None},
        {"source_type": "image", "source_id": "media_1", "page": None},
    ),
    ids=("unmatched-text", "wrong-file-id", "missing-pdf-page", "wrong-file-kind"),
)
def test_unmatched_quotes_or_invalid_file_references_remain_unverified(
    reference: dict[str, Any],
) -> None:
    notice = _notice()
    output = _output(notice)
    output["evidence"] = [
        {
            "field": "action",
            "excerpt": "확인되지 않은 신청 내용",
            "verification": "text_matched",
            **reference,
        }
    ]

    result = _validate(output, notice, mixed=True)

    assert result.action == output["action"]
    assert len(result.evidence) == 1
    item = result.evidence[0]
    assert item.excerpt == "확인되지 않은 신청 내용"
    assert item.source_type == reference["source_type"]
    assert item.source_id == reference["source_id"]
    assert item.page == reference["page"]
    assert item.verification is None
    assert result.uncertainties == [REVIEW_NOTE]


def test_mixed_input_valid_file_quote_is_reference_only_without_text_matching() -> None:
    notice = _notice()
    output = _output(notice)
    output["evidence"] = [
        {
            "field": "action",
            "excerpt": "파일에서 읽었다고 반환된 신청 문구",
            "source_type": "document",
            "source_id": "media_1",
            "page": 2,
            "verification": "text_matched",
        }
    ]

    result = _validate(output, notice, mixed=True)

    assert result.action == output["action"]
    assert result.evidence[0].verification == "file_reference_only"
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("metadata", ("title", "department"))
def test_literal_metadata_quotes_are_text_matched_even_when_absent_from_body(
    metadata: str,
) -> None:
    notice = _notice(body="지역 주민 대상 문화교실 참가자를 모집합니다.")
    output = _output(notice)
    excerpt = getattr(notice, metadata)
    assert excerpt not in notice.body_text
    output["evidence"] = [
        {"field": "summary", "excerpt": excerpt, "verification": "file_reference_only"}
    ]

    result = _validate(output, notice)

    assert result.summary == output["summary"]
    assert result.evidence[0].excerpt == excerpt
    assert result.evidence[0].verification == "text_matched"
    assert result.uncertainties == [REVIEW_NOTE]


def test_caller_publisher_metadata_remains_authoritative_without_erasing_other_claims() -> None:
    notice = _notice()
    output = _output(notice)
    output["publisher"] = "서울특별시"
    output["evidence"].append({"field": "publisher", "excerpt": "추측한 발행처"})

    result = _validate(output, notice)

    assert result.publisher == notice.publisher
    assert result.summary == output["summary"]
    assert not any(item.field == "publisher" for item in result.evidence)
    assert result.uncertainties == [REVIEW_NOTE]


def test_extracted_attachment_text_is_a_readable_source_for_preservation_and_quotes() -> None:
    notice = _notice(attachment=True)
    output = _output(notice)

    result = _validate(output, notice)

    assert result.audience == output["audience"]
    assert result.notes == output["notes"]
    assert any(
        item.field == "audience" and item.verification == "text_matched" for item in result.evidence
    )
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("already_review", (False, True))
def test_existing_uncertainties_survive_and_review_instruction_is_not_duplicated(
    already_review: bool,
) -> None:
    notice = _notice()
    output = _output(notice)
    existing = ["일정 확인 필요", *([REVIEW_NOTE] if already_review else [])]
    output["uncertainties"] = existing

    result = _validate(output, notice)

    assert result.uncertainties == ["일정 확인 필요", REVIEW_NOTE]


@pytest.mark.parametrize("mixed", (False, True), ids=("text", "mixed"))
@pytest.mark.parametrize("field", ("end_date", "end_time"))
def test_impossible_date_or_time_order_repairs_end_only_and_keeps_other_facts(
    mixed: bool, field: str
) -> None:
    notice = _notice()
    output = _output(notice)
    output["dates"][0][field] = "2026-10-09" if field == "end_date" else "09:00"
    output["evidence"].append({"field": "status_detail", "excerpt": "참여 신청 안내 확인"})

    result = _validate(output, notice, mixed=mixed)

    assert len(result.dates) == 1
    assert result.dates[0].start_date == "2026-10-10"
    assert result.dates[0].start_time == "10:00"
    assert getattr(result.dates[0], field) is None
    if field == "end_date":
        assert result.dates[0].end_time is None
    else:
        assert result.dates[0].end_date == "2026-10-10"
    assert result.dates[0].text == output["dates"][0]["text"]
    assert result.status == "unknown"
    assert result.status_detail is None
    assert result.summary == output["summary"]
    assert result.action == output["action"]
    assert result.notes == output["notes"]
    assert not any(item.field in {"status", "status_detail"} for item in result.evidence)
    assert result.uncertainties == [REVIEW_NOTE]


def test_next_day_earlier_end_clock_is_not_repaired_as_impossible_order() -> None:
    notice = _notice()
    output = _output(notice)
    output["dates"][0].update(
        start_date="2026-10-10", end_date="2026-10-11", start_time="22:00", end_time="01:00"
    )

    result = _validate(output, notice)

    assert result.dates[0].model_dump() == output["dates"][0]
    assert result.status == output["status"]
    assert result.uncertainties == [REVIEW_NOTE]


def test_diagnostic_exception_does_not_reset_schema_valid_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notice = _notice()
    output = _output(notice)

    def inconclusive(*_args: Any, **_kwargs: Any) -> NoticeSummary:
        raise SummaryValidationError("Missing evidence for: action")

    monkeypatch.setattr(summarize_module, "ground_summary", inconclusive)

    result = _validate(output, notice)

    assert result.summary == output["summary"]
    assert result.audience == output["audience"]
    assert result.action == output["action"]
    assert result.dates[0].model_dump() == output["dates"][0]
    assert result.notes == output["notes"]
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("mixed", (False, True), ids=("text", "mixed"))
def test_preserved_uncertain_result_stores_review_without_publishing_a_deadline(
    mixed: bool,
) -> None:
    notice = _notice()
    output = _output(notice)
    summary = _validate(output, notice, mixed=mixed)
    sources = (MediaSource("media_1", "document"),) if mixed else ()
    prepared_result = PreparedSummaryResult(17, summary, (), sources)
    metadata = SummaryMetadata(
        source_hash="cd" * 32,
        model="gemini-3.5-flash-lite",
        prompt_version="summary-review-test",
        attachment_status="all_read" if mixed else "none",
    )

    record = build_summary_record(
        prepared_result,
        metadata,
        deadline_on=date(2026, 10, 10),
        generated_at=datetime(2026, 10, 4, 10, tzinfo=UTC),
    )

    assert record.status == "needs_review"
    assert record.deadline_on is None
    assert record.result is not None
    assert record.result.action == output["action"]
    assert record.result.dates[0].end_date == "2026-10-10"
    assert record.result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("invalid", ("malformed-json", "missing-action", "bad-date", "bad-page"))
def test_preservation_never_turns_a_schema_invalid_response_into_success(invalid: str) -> None:
    notice = _notice()
    output = _output(notice)
    if invalid == "missing-action":
        del output["action"]
    elif invalid == "bad-date":
        output["dates"][0]["start_date"] = "2026-02-30"
    elif invalid == "bad-page":
        output["evidence"][0].update(source_type="document", source_id="media_1", page="1")
    raw = "broken-json" if invalid == "malformed-json" else json.dumps(output, ensure_ascii=False)

    with pytest.raises(SummaryValidationError, match="failed validation"):
        summarize_module._validate_summary(raw, notice)


def test_metadata_alone_does_not_become_a_readable_notice() -> None:
    notice = _notice(body="")
    output = _output(notice)

    result = _validate(output, notice)

    assert result.summary == "공지 확인 불가"
    assert result.action is None
    assert result.dates == []
    assert result.notes == []
