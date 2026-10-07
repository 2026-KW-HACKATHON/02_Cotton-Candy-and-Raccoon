"""Three independent audit passes over contracts, counterexamples and captures."""

import hashlib
import json
import unicodedata
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline.storage.summary_deadline import compute_deadline_on
from pipeline.storage.summary_metadata import build_summary_metadata
from pipeline.storage.summary_record import build_summary_record
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.transform import summarize as summarize_module
from pipeline.transform.card_claims import card_claim_review_reasons
from pipeline.transform.grounding import unknown_summary
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.prepared_summary import PreparedSummaryResult
from pipeline.transform.summary_highlights import build_summary_text_highlights
from pipeline.transform.summary_schema import (
    Evidence,
    GeminiNoticeSummary,
    NoticeSummary,
    evidence_reference_valid,
)

TITLE = "성인 안전교육 신청 안내"
AUDIENCE = "월계1동 주민 중 만 19세 이상 성인"
ACTION = "신청자는 신분증 사본을 반드시 제출"
PERIOD = "신청기간: 2026-10-03~2026-10-20"
NOTES = ["참가비: 30,000원", "환불은 불가하나 수업 취소 시 전액 환불"]
FIXTURES = Path(__file__).parent / "fixtures"


def _input(*, waiver=False, clock=False):
    notes = [*NOTES, *(["기초생활수급자는 참가비 무료"] if waiver else [])]
    period = (
        "신청기간: 2026-10-03 14:00~2026-10-20 20:00" if clock else PERIOD
    )
    notice = NoticeInput.model_validate({
        "title": TITLE,
        "body_text": "\n".join([TITLE, "대상: " + AUDIENCE, period, "할 일: " + ACTION, *notes]),
        "reference_datetime": "2026-10-07T12:00:00+09:00",
    })
    raw = unknown_summary(notice).model_dump(mode="json")
    raw.update(
        category="application", category_code=22, summary=TITLE,
        audience=AUDIENCE, audience_scope="conditional",
        action=ACTION, action_requirement="required", status="open", notice_update="new",
        uncertainties=[], notes=notes,
        dates=[{
            "kind": "application", "label": "신청기간", "text": None,
            "start_date": "2026-10-03", "end_date": "2026-10-20",
            "start_time": "14:00" if clock else None,
            "end_time": "20:00" if clock else None,
        }],
        evidence=[
            {"field": field, "excerpt": excerpt, "verification": "text_matched"}
            for field, excerpt in [
                ("summary", TITLE), ("category", TITLE), ("category_code", TITLE),
                ("audience", "대상: " + AUDIENCE), ("action", "할 일: " + ACTION),
                ("action_requirement", "할 일: " + ACTION), ("dates", period),
                *(("notes", note) for note in notes),
            ]
        ],
        card_summaries={
            "audience": AUDIENCE + "이 대상이에요.",
            "deadline": (
                "신청은 10월 3일 오후 2시부터 10월 20일 오후 8시까지예요."
                if clock else "신청 기간은 10월 3일부터 10월 20일까지예요."
            ),
            "action": "신청자는 신분증 사본을 반드시 제출해 주세요.",
            "notes": (
                "참가비는 30,000원이에요; 수업 취소 시 전액 환불돼요."
                + (" 기초생활수급자는 참가비가 무료예요." if waiver else "")
            ),
        },
    )
    return notice, raw


def _record_view(summary, notice):
    metadata = build_summary_metadata(
        body_text=notice.body_text, total_file_count=0, read_file_count=0,
        model="offline-audit", prompt_version="threepass-card-audit",
    )
    record = build_summary_record(
        PreparedSummaryResult(notice_id=2114, summary=summary, warnings=()), metadata,
        deadline_on=compute_deadline_on(summary), generated_at=datetime(2026, 10, 7, tzinfo=UTC),
    )
    return record, build_notice_summary_view(
        status=record.status, result=record.result,
        attachment_status=metadata.attachment_status, notice=notice,
    )


@pytest.mark.parametrize("category_code", ["22", 22.0, True, 28])
def test_pass1_schema_rejects_non_integer_or_unknown_policy_code(category_code):
    _, raw = _input()
    raw["category_code"] = category_code
    with pytest.raises(ValidationError):
        GeminiNoticeSummary.model_validate(raw)


@pytest.mark.parametrize("missing", ["audience", "deadline", "action", "notes"])
def test_pass1_fresh_output_requires_each_named_slot(missing):
    _, raw = _input()
    raw["card_summaries"].pop(missing)
    with pytest.raises(ValidationError):
        GeminiNoticeSummary.model_validate(raw)


@pytest.mark.parametrize("field", ["audience", "action", "dates", "notes"])
def test_pass1_one_unverified_field_reference_prevents_clean_publication(field):
    notice, raw = _input()
    next(item for item in raw["evidence"] if item["field"] == field)["verification"] = None
    summary = NoticeSummary.model_validate(raw)
    before = summary.model_dump(mode="json")
    record, view = _record_view(summary, notice)
    assert record.status == view.status == "needs_review"
    assert record.deadline_on is None
    assert view.message == "원문 확인 요함"
    assert view.content.cards.model_dump() == build_notice_summary_view(
        status="needs_review", result=summary, attachment_status="none"
    ).content.cards.model_dump()
    assert summary.model_dump(mode="json") == before


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_pass1_pending_failed_cannot_leak_caller_supplied_result(status):
    notice, raw = _input()
    view = build_notice_summary_view(
        status=status, result=raw, attachment_status="none", notice=notice
    )
    assert view.content is None
    assert view.text_highlights is None


@pytest.mark.parametrize("kind", ["event", "operation", "effective", "result", "other"])
def test_pass1_non_deadline_date_kinds_cannot_be_used_for_sorting(kind):
    _, raw = _input()
    raw["dates"][0]["kind"] = kind
    assert compute_deadline_on(NoticeSummary.model_validate(raw)) is None


MUTATIONS = [
    pytest.param(
        "audience", "공릉1동 주민 중 만 19세 이상 성인이 대상이에요.", False, False,
        "card_audience_resident_area_added", id="resident-region-substituted",
    ),
    pytest.param(
        "audience", "만 19세 이상 성인이 대상이에요.", False, False,
        "card_audience_resident_area_omitted", id="residence-condition-dropped",
    ),
    pytest.param(
        "action", "신분증 사본 제출은 선택이며 주민등록등본 제출은 필수예요.", False, False,
        "card_action_required_weakened", id="different-required-item-masks-optional-document",
    ),
    pytest.param(
        "notes", "참가비는 30,000원이에요; 모든 참가자는 무료예요; 수업 취소 시 전액 환불돼요.",
        True, False, "card_notes_false_free", id="conditional-waiver-made-universal",
    ),
    pytest.param(
        "deadline", "신청 기간은 10월 3일부터 10월 20일까지예요.", False, True,
        "card_deadline_end_time_omitted", id="explicit-end-time-dropped",
    ),
]


@pytest.mark.parametrize(("slot", "text", "waiver", "clock", "reason"), MUTATIONS)
def test_pass2_mutations_are_detected_and_immutable(slot, text, waiver, clock, reason):
    notice, raw = _input(waiver=waiver, clock=clock)
    raw["card_summaries"][slot] = text
    summary = NoticeSummary.model_validate(raw)
    before = summary.model_dump(mode="json")
    assert reason in card_claim_review_reasons(summary, notice)
    assert summary.model_dump(mode="json") == before


@pytest.mark.parametrize(("slot", "text", "waiver", "clock", "reason"), MUTATIONS)
def test_pass2_generation_record_public_view_marks_the_same_mutation_for_review(
    monkeypatch, slot, text, waiver, clock, reason
):
    notice, raw = _input(waiver=waiver, clock=clock)
    raw["card_summaries"][slot] = text
    requests = []

    def provider(**kwargs):
        requests.append(kwargs)
        return json.dumps(raw, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", provider)
    summary = summarize_module.summarize_notice(notice, api_key="offline-audit")
    assert requests
    record, view = _record_view(summary, notice)
    assert record.status == view.status == "needs_review"
    assert record.deadline_on is None
    assert getattr(view.content.cards, slot).text == text
    assert reason in card_claim_review_reasons(summary, notice)
    assert summary.audience == raw["audience"]
    assert summary.notes == raw["notes"]


@pytest.mark.parametrize(("slot", "text", "waiver", "clock"), [
    ("audience", "월계1동에 거주하며 만 19세 이상인 성인이 신청할 수 있어요.", False, False),
    ("action", "전화 문의는 선택이에요; 신청자는 신분증 사본을 반드시 제출해 주세요.",
     False, False),
    ("notes", "참가비는 3만원이에요; 기초생활수급자는 참가비가 무료예요; "
     "수업 취소 시 전액 환불돼요.", True, False),
    ("deadline", "신청은 10월 3일 14시부터 10월 20일 20시까지예요.", False, True),
])
def test_pass2_clear_valid_paraphrases_do_not_require_review(slot, text, waiver, clock):
    notice, raw = _input(waiver=waiver, clock=clock)
    raw["card_summaries"][slot] = text
    assert card_claim_review_reasons(NoticeSummary.model_validate(raw), notice) == ()


def test_pass2_explicit_full_fee_exemption_allows_a_qualified_free_paraphrase():
    notice, raw = _input(waiver=True)
    notice.body_text = notice.body_text.replace(
        "기초생활수급자는 참가비 무료", "기초생활수급자는 참가비 면제"
    )
    raw["notes"][-1] = "기초생활수급자는 참가비 면제"
    raw["evidence"][-1]["excerpt"] = raw["notes"][-1]
    assert card_claim_review_reasons(NoticeSummary.model_validate(raw), notice) == ()


def test_pass2_seoul_formal_and_short_resident_names_are_equivalent():
    notice, raw = _input()
    formal = "서울특별시 주민 중 만 19세 이상 성인"
    notice.body_text = notice.body_text.replace(AUDIENCE, formal)
    raw["audience"] = formal
    next(item for item in raw["evidence"] if item["field"] == "audience")["excerpt"] = formal
    raw["card_summaries"]["audience"] = "서울시 주민 중 만 19세 이상 성인이 대상이에요."
    assert card_claim_review_reasons(NoticeSummary.model_validate(raw), notice) == ()


def _relation_input(kind):
    notice, raw = _input()
    if kind == "fee":
        quotes = ["청년(19~39세) 참가비: 30,000원", "어르신(65세 이상) 참가비: 10,000원"]
        notice.body_text = notice.body_text.replace(NOTES[0], "\n".join(quotes))
        raw["notes"] = [*quotes, NOTES[1]]
        raw["evidence"] = [item for item in raw["evidence"] if item["field"] != "notes"] + [
            {"field": "notes", "excerpt": quote, "verification": "text_matched"}
            for quote in raw["notes"]
        ]
        raw["card_summaries"]["notes"] = (
            "청년(19~39세) 참가비는 30,000원이고 어르신(65세 이상) 참가비는 10,000원이에요; "
            "수업 취소 시 전액 환불돼요."
        )
    else:
        quote = "행사기간: 2026-11-01~2026-11-03"
        notice.body_text += "\n" + quote
        raw["dates"].append({
            "kind": "event", "label": "행사기간", "text": None,
            "start_date": "2026-11-01", "end_date": "2026-11-03",
            "start_time": None, "end_time": None,
        })
        raw["evidence"].append({
            "field": "dates", "excerpt": quote, "verification": "text_matched",
        })
        raw["card_summaries"]["deadline"] = (
            "신청은 10월 3일부터 10월 20일까지이고 행사는 11월 1일부터 11월 3일까지예요."
        )
    return notice, raw


RELATION_MUTATIONS = [
    pytest.param(
        "fee", "notes",
        "청년(19~39세) 참가비는 10,000원이고 어르신(65세 이상) 참가비는 30,000원이에요; "
        "수업 취소 시 전액 환불돼요.", "card_notes_group_amount_changed",
        id="unchanged-amount-set-with-recipient-price-swap",
    ),
    pytest.param(
        "dates", "deadline",
        "행사는 10월 3일부터 10월 20일까지이며 신청은 11월 1일부터 11월 3일까지예요.",
        "card_deadline_role_changed", id="unchanged-date-set-with-schedule-role-swap",
    ),
]


@pytest.mark.parametrize(("kind", "slot", "text", "reason"), RELATION_MUTATIONS)
def test_pass2_same_numeric_set_does_not_validate_a_swapped_relation(kind, slot, text, reason):
    notice, raw = _relation_input(kind)
    canonical = NoticeSummary.model_validate(raw)
    assert card_claim_review_reasons(canonical, notice) == ()
    raw["card_summaries"][slot] = text
    changed = NoticeSummary.model_validate(raw)
    before = changed.model_dump(mode="json")
    assert reason in card_claim_review_reasons(changed, notice)
    assert changed.model_dump(mode="json") == before


@pytest.mark.parametrize(("kind", "slot", "text", "reason"), RELATION_MUTATIONS)
def test_pass2_public_generation_warns_for_a_swapped_relation(
    monkeypatch, kind, slot, text, reason
):
    notice, raw = _relation_input(kind)
    raw["card_summaries"][slot] = text
    calls = []

    def provider(**kwargs):
        calls.append(kwargs)
        return json.dumps(raw, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", provider)
    summary = summarize_module.summarize_notice(notice, api_key="offline-audit")
    record, view = _record_view(summary, notice)
    assert calls
    assert record.status == view.status == "needs_review"
    assert view.message == "원문 확인 요함"
    assert record.deadline_on is None
    assert getattr(view.content.cards, slot).text == text
    assert reason in card_claim_review_reasons(summary, notice)
    assert summary.notes == raw["notes"]
    assert [entry.model_dump(mode="json") for entry in summary.dates] == raw["dates"]


@pytest.mark.parametrize(("kind", "slot", "text"), [
    ("fee", "notes", "청년(19~39세) 참가비는 3만원이고 어르신(65세 이상) 참가비는 1만원이에요; "
     "수업 취소 시 전액 환불돼요."),
    ("fee", "notes", "어르신(65세 이상)은 참가비가 1만원이에요; 청년(19~39세)은 참가비가 "
     "3만원이에요; 수업 취소 시 전액 환불돼요."),
    ("fee", "notes", "만 19~39세 청년의 참가비는 30천원이고 만 65세 이상 어르신의 참가비는 "
     "10천원이에요; 수업 취소 시 전액 환불돼요."),
    ("fee", "notes", "청년(19~39세)의 수강료는 30,000원이에요; 어르신(65세 이상)의 "
     "수강료는 10,000원이에요; 수업 취소 시 전액 환불돼요."),
    ("dates", "deadline", "신청 기간은 10월 3일부터 10월 20일까지이고 행사 일정은 "
     "11월 1일부터 11월 3일까지예요."),
    ("dates", "deadline", "행사는 2026년 11월 1일부터 11월 3일까지예요; 접수는 "
     "2026년 10월 3일부터 10월 20일까지예요."),
    ("dates", "deadline", "신청기간: 2026-10-03~2026-10-20이고 행사기간: "
     "2026-11-01~2026-11-03이에요."),
    ("dates", "deadline", "10월 3일부터 10월 20일까지 신청하고 11월 1일부터 11월 3일까지 "
     "행사에 참여할 수 있어요."),
])
def test_pass2_normal_multiple_relations_and_paraphrases_remain_clean(kind, slot, text):
    notice, raw = _relation_input(kind)
    raw["card_summaries"][slot] = text
    summary = NoticeSummary.model_validate(raw)
    assert card_claim_review_reasons(summary, notice) == ()
    record, view = _record_view(summary, notice)
    assert record.status == view.status == "summarized"
    assert view.message is None


@pytest.mark.parametrize("kind", ["fee", "dates"])
def test_pass2_generation_keeps_correct_multiple_relations_summarized(monkeypatch, kind):
    notice, raw = _relation_input(kind)
    monkeypatch.setattr(
        summarize_module,
        "generate_summary_json",
        lambda **kwargs: json.dumps(raw, ensure_ascii=False),
    )
    summary = summarize_module.summarize_notice(notice, api_key="offline-audit")
    record, view = _record_view(summary, notice)
    assert summary.uncertainties == []
    assert record.status == view.status == "summarized"
    assert view.message is None


def test_pass2_explicit_multi_schedule_role_cannot_reverse_its_own_period():
    notice, raw = _relation_input("dates")
    raw["card_summaries"]["deadline"] = (
        "신청은 10월 20일부터 10월 3일까지이고 행사는 11월 1일부터 11월 3일까지예요."
    )
    assert "card_deadline_range_reversed" in card_claim_review_reasons(
        NoticeSummary.model_validate(raw), notice
    )


@pytest.mark.parametrize("separator", [" ", "\t", "\n", "\u3000", "\u00a0"])
def test_pass2_whitespace_location_has_exact_original_utf16_span_without_promotion(separator):
    notice, raw = _input()
    quote = "대상 월계1동 주민"
    body = "🦝 " + separator.join(quote.split())
    notice.body_text = body
    summary = NoticeSummary.model_validate(raw)
    summary.evidence = [Evidence(field="audience", excerpt=quote, verification=None)]
    highlights = build_summary_text_highlights(summary, notice)
    located = highlights.cards.audience.references[0]
    assert located.status == "matched"
    assert located.reference.evidence.verification is None
    span = located.candidates[0]
    assert span.start == 3
    assert body.encode("utf-16-le")[span.start * 2:span.end * 2].decode("utf-16-le") == span.text
    assert span.text == separator.join(quote.split())


def test_pass2_nfc_only_equivalence_does_not_approve_a_near_quote():
    notice, raw = _input()
    quote = "대상 월계1동 주민"
    notice.body_text = unicodedata.normalize("NFD", quote)
    summary = NoticeSummary.model_validate(raw)
    summary.evidence = [Evidence(field="audience", excerpt=quote)]
    locations = build_summary_text_highlights(summary, notice).cards.audience
    assert locations.status == "not_found"
    assert locations.ranges == []


def test_pass2_many_duplicate_quotes_never_choose_a_first_occurrence():
    notice, raw = _input()
    notice.body_text = "🦝 " + ("월계1동 주민\n" * 1200)
    summary = NoticeSummary.model_validate(raw)
    summary.evidence = [Evidence(field="audience", excerpt="월계1동 주민")]
    located = build_summary_text_highlights(summary, notice).cards.audience
    assert located.status == "ambiguous"
    assert located.ranges == []
    assert len(located.references[0].candidates) == 1200


def test_pass2_plain_text_quotes_cannot_claim_an_html_node_offset():
    notice, raw = _input()
    notice.body_text = "월계1동 <b>주민</b>"
    summary = NoticeSummary.model_validate(raw)
    summary.evidence = [Evidence(field="audience", excerpt="월계1동 주민")]
    assert build_summary_text_highlights(summary, notice).cards.audience.ranges == []


def test_pass2_file_quote_is_never_promoted_when_same_words_exist_in_text():
    notice, raw = _input()
    summary = NoticeSummary.model_validate(raw)
    summary.evidence = [Evidence(
        field="audience", excerpt=AUDIENCE, source_type="document", source_id="media_1",
        page=1, verification="file_reference_only",
    )]
    highlights = build_summary_text_highlights(summary, notice)
    assert highlights.cards.audience.status == "file_only"
    assert highlights.cards.audience.ranges == []
    assert not evidence_reference_valid(summary.evidence[0], sources=[notice.body_text])


@pytest.mark.parametrize("case_name", [
    "moss_exhibition", "online_english", "library_committee", "library_committee_pdf_only",
])
def test_pass3_captured_record_public_view_and_locations_preserve_source_contract(case_name):
    capture = json.loads((FIXTURES / "card_text_highlights.json").read_text("utf-8"))["cases"]
    case = capture[case_name]
    notice = NoticeInput.model_validate(case["notice_input"])
    summary = NoticeSummary.model_validate(case["summary"])
    before = deepcopy(case["summary"])
    record, view = _record_view(summary, notice)
    assert view.content is not None
    assert record.result.model_dump(mode="json") == before
    assert view.content.cards.audience.text == (
        summary.card_summaries.audience if summary.card_summaries.audience is not None
        else (None if summary.audience else "원문을 확인해 주세요")
    )
    assert record.status == view.status
    if record.status == "needs_review":
        assert record.deadline_on is None
        assert view.message == "원문 확인 요함"
    highlights = view.text_highlights
    sources = {source.key: source.text for source in highlights.sources}
    for source in highlights.sources:
        assert source.sha256 == hashlib.sha256(source.text.encode("utf-8")).hexdigest()
    for card in (highlights.cards.audience, highlights.cards.deadline,
                 highlights.cards.action, highlights.cards.notes):
        for span in card.ranges:
            assert sources[span.source_key].encode("utf-16-le")[
                span.start * 2:span.end * 2
            ].decode("utf-16-le") == span.text
        assert all(reference.candidates == [] for reference in card.references
                   if reference.reference.evidence.source_type != "text")
    if case["pdf_only"]:
        assert record.status == "needs_review"
        assert sources == {}
    assert summary.model_dump(mode="json") == before


@pytest.mark.parametrize("case_name", ["camp_text", "gifted_text", "camp_pdf"])
def test_pass3_historical_response_bytes_and_original_urls_are_unchanged(case_name):
    capture = json.loads((FIXTURES / "real_notice_grounding.json").read_text("utf-8"))
    case = capture["cases"][case_name]
    assert case["source_url"].startswith("https://www.nowon.kr/")
    for response in case["responses"]:
        assert hashlib.sha256(response["raw"].encode("utf-8")).hexdigest() == response["sha256"]
