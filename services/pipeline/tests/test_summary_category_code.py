"""Policy fields are strict integers independent of notice type and source identity."""

import json
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError
from test_gemini_multimodal import _media, _prepared

from pipeline.transform import summarize as summarize_module
from pipeline.transform.file_only_summary import file_reference_problems
from pipeline.transform.gemini_prompt import SUMMARY_PROMPT_VERSION, load_summary_prompt
from pipeline.transform.grounding import REVIEW_NOTE, ground_summary, unknown_summary
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_schema import (
    CATEGORY_CODE_NAMES,
    MediaSource,
    NoticeSummary,
    SummaryValidationError,
    validate_evidence,
)


def _notice(body: str = "저소득 주민 복지 지원사업 신청") -> NoticeInput:
    return NoticeInput.model_validate(
        {
            "title": "주민 안내",
            "body_text": body,
            "publisher": "노원구청",
            "reference_datetime": "2026-10-06T12:00:00+09:00",
        }
    )


def _output(notice: NoticeInput | None = None, **changes: Any) -> dict[str, Any]:
    notice = notice or _notice()
    data = unknown_summary(notice).model_dump()
    data.update(
        category="application",
        category_code=27,
        summary="복지 지원사업 신청",
        uncertainties=[],
        evidence=[
            {"field": "summary", "excerpt": "복지 지원사업 신청"},
            {"field": "category_code", "excerpt": "저소득 주민 복지 지원사업"},
        ],
    )
    return data | changes


@pytest.mark.parametrize("code", [21, 22, 23, 24, 25, 26, 27, 30])
def test_category_code_accepts_only_the_eight_integer_fields(code: int) -> None:
    data = _output(category_code=code)
    summary = NoticeSummary.model_validate_json(json.dumps(data))
    assert type(summary.category_code) is int
    assert summary.category_code == code
    assert summary.category == "application"


@pytest.mark.parametrize("code", ["27", "복지", 27.0, True, False, 0, 28, 29, 31, [], {}])
def test_category_code_rejects_strings_floats_booleans_and_other_codes(code: Any) -> None:
    data = _output(category_code=code)
    with pytest.raises(ValidationError):
        NoticeSummary.model_validate(data)
    with pytest.raises(ValidationError):
        NoticeSummary.model_validate_json(json.dumps(data))


def test_nullable_code_is_required_in_model_and_gemini_schema() -> None:
    data = _output(category_code=None, evidence=[])
    assert NoticeSummary.model_validate(data).category_code is None
    del data["category_code"]
    with pytest.raises(ValidationError, match="category_code"):
        NoticeSummary.model_validate(data)

    schema = NoticeSummary.model_json_schema()
    assert "category_code" in schema["required"]
    choices = schema["properties"]["category_code"]["anyOf"]
    assert {item["type"] for item in choices} == {"integer", "null"}
    integers = next(item for item in choices if item["type"] == "integer")
    assert integers["enum"] == [21, 22, 23, 24, 25, 26, 27, 30]
    assert CATEGORY_CODE_NAMES == {
        21: "교통", 22: "안전", 23: "주택", 24: "경제", 25: "환경", 26: "문화",
        27: "복지", 30: "행정",
    }


def test_text_field_evidence_is_retained_and_not_inferred_from_other_claims() -> None:
    notice = _notice()
    result = ground_summary(NoticeSummary.model_validate(_output()), notice)
    assert result.category_code == 27
    assert result.category == "application"
    assert result.uncertainties == []
    assert next(item for item in result.evidence if item.field == "category_code").verification == (
        "text_matched"
    )

    data = _output(evidence=[{"field": "summary", "excerpt": "복지 지원사업 신청"}])
    with pytest.raises(SummaryValidationError, match="category_code"):
        validate_evidence(NoticeSummary.model_validate(data), body_text=notice.body_text,
                          attachment_texts=[])
    checked = ground_summary(NoticeSummary.model_validate(data), notice)
    assert checked.category_code is None
    assert checked.uncertainties == [REVIEW_NOTE]
    preserved = summarize_module._validate_summary(json.dumps(data, ensure_ascii=False), notice)
    assert preserved.category_code == 27
    assert preserved.uncertainties == [REVIEW_NOTE]


def test_missing_or_unreadable_fields_never_default_to_administration() -> None:
    notice = _notice("")
    summary = unknown_summary(notice)
    assert summary.category_code is None
    assert summary.category == "unknown"
    with pytest.raises(SummaryValidationError, match="remain unknown"):
        validate_evidence(summary.model_copy(update={"category_code": 30}), body_text="",
                          attachment_texts=[])


def test_partial_headline_keeps_an_independently_verified_policy_field() -> None:
    notice = _notice()
    data = _output(
        category="unknown", summary=REVIEW_NOTE, uncertainties=[REVIEW_NOTE],
        evidence=[{"field": "category_code", "excerpt": "저소득 주민 복지 지원사업"}],
    )
    result = ground_summary(NoticeSummary.model_validate(data), notice)
    assert result.summary == REVIEW_NOTE
    assert result.category_code == 27
    assert [item.field for item in result.evidence] == ["category_code"]


def test_unknown_policy_field_does_not_replace_notice_or_topic_types() -> None:
    data = _output(category="mixed", category_code=26, topics=[
        {"title": "공연", "category": "event", "summary": "주민 공연"},
        {"title": "강사", "category": "application", "summary": "문화 강사 모집"},
    ], dates=[{
        "kind": "submission", "label": "서류 제출", "text": None,
        "start_date": None, "end_date": "2026-10-20", "start_time": None, "end_time": None,
    }])
    result = NoticeSummary.model_validate(data)
    assert result.category_code == 26
    assert result.category == "mixed"
    assert [item.category for item in result.topics] == ["event", "application"]
    assert result.dates[0].kind == "submission"


def test_file_only_policy_evidence_remains_a_file_reference() -> None:
    prepared = _prepared("", _media("document"))
    data = _output(prepared.notice, category="news", summary="복지 지원 안내", evidence=[
        {"field": field, "excerpt": "복지 지원 안내", "source_type": "document",
         "source_id": "media_1", "page": 1}
        for field in ("summary", "category_code")
    ])
    media = (MediaSource("media_1", "document"),)
    result = summarize_module._validate_summary(
        json.dumps(data, ensure_ascii=False), prepared.notice, media_sources=media
    )
    assert result.category_code == 27
    assert next(item for item in result.evidence if item.field == "category_code").verification == (
        "file_reference_only"
    )
    data["evidence"] = data["evidence"][:1]
    assert "evidence.category_code: missing_reference" in file_reference_problems(
        NoticeSummary.model_validate(data), prepared.notice, media
    )


def test_invalid_code_retry_corrects_only_that_field_and_its_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notice = _notice()
    first = _output(category_code="27")
    retry = _output(summary="지원사업 신청", category_code=27)
    responses = iter([json.dumps(first, ensure_ascii=False), json.dumps(retry, ensure_ascii=False)])
    requests = []

    def generate(**kwargs: Any) -> str:
        requests.append(deepcopy(kwargs))
        return next(responses)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    result = summarize_module.summarize_notice(notice, api_key="test-key")
    assert len(requests) == 2
    assert "category_code" in requests[1]["notice_text"]
    assert result.category_code == 27
    assert result.summary == first["summary"]
    assert result.uncertainties == []


@pytest.mark.parametrize("code", ["27", 27.0, True, 28])
def test_repeated_invalid_code_becomes_unknown_without_erasing_valid_fields(
    monkeypatch: pytest.MonkeyPatch, code: Any,
) -> None:
    data = _output(category_code=code)
    requests = []

    def generate(**kwargs: Any) -> str:
        requests.append(kwargs)
        return json.dumps(data, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    result = summarize_module.summarize_notice(_notice(), api_key="test-key")
    assert len(requests) == 2
    assert result.category_code is None
    assert result.category == "application"
    assert result.summary == data["summary"]
    assert result.uncertainties == [REVIEW_NOTE]
    assert not any(item.field == "category_code" for item in result.evidence)


def test_file_retry_does_not_silently_drop_the_previous_policy_field() -> None:
    prepared = _prepared("", _media("image"))
    data = _output(prepared.notice, category="news", summary="복지 지원 안내", evidence=[
        {"field": field, "excerpt": "복지 지원 안내", "source_type": "image",
         "source_id": "media_1", "page": None}
        for field in ("summary", "category_code")
    ])
    media = (MediaSource("media_1", "image"),)
    first = summarize_module._validate_summary(
        json.dumps(data, ensure_ascii=False), prepared.notice, media_sources=media
    )
    data["category_code"] = None
    data["evidence"] = data["evidence"][:1]
    retry = summarize_module._validate_summary(
        json.dumps(data, ensure_ascii=False), prepared.notice, media_sources=media
    )
    merged = summarize_module._merge_file_reference_correction(first, retry, prepared.notice, media)
    assert merged.category_code == 27
    assert any(item.field == "category_code" for item in merged.evidence)
    assert merged.uncertainties == [REVIEW_NOTE]


def test_prompt_describes_separate_fields_integer_encoding_and_no_fallback_code() -> None:
    prompt = load_summary_prompt()
    assert SUMMARY_PROMPT_VERSION == "notice-summary-v2-category-code"
    assert '"category_code": null' in prompt
    assert (
        "category_code는 category, topics의 category, dates의 kind를 대체하거나 바꾸지 않는다"
        in prompt
    )
    assert "분야를 모르겠다는 뜻으로 30을 사용하지 않는다" in prompt
    assert "반환값은 큰따옴표 없는 JSON 정수" in prompt
    for code, name in CATEGORY_CODE_NAMES.items():
        assert f"- {code}: {name}" in prompt
