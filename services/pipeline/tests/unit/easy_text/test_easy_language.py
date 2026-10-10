"""Exact source preservation, rewrite generation and legacy replacement validation."""

import json
import traceback
from datetime import UTC, datetime
from urllib.parse import quote, quote_plus

import httpx
import pytest
from pydantic import ValidationError
from support.easy_rewrite import (
    LEGACY_PROMPT_VERSION,
    legacy_result,
    rewrite,
    rewrite_response,
    sentence,
)

from pipeline.glossary import easy_language
from pipeline.glossary.easy_language import (
    DEFAULT_MODEL,
    PROMPT_VERSION,
    AppliedChange,
    DictionaryCandidate,
    EasyLanguageAPIError,
    EasyLanguageConfigurationError,
    EasyLanguageResponse,
    EasyLanguageResult,
    EasyLanguageValidationError,
    EasyRewriteResponse,
    ProposedChange,
    ProposedDictionaryCandidate,
    apply_easy_language_changes,
    flatten_easy_rewrite,
    load_easy_language_prompt,
    simplify_notice,
)
from pipeline.glossary.source import NoticeGlossaryInput, source_hash

GENERATED_AT = datetime(2026, 10, 7, 1, 2, 3, tzinfo=UTC)
API_KEY = "test-secret-do-not-display"


def proposal(original: str, replacement: str, context: str) -> dict[str, str]:
    return {"original": original, "replacement": replacement, "context": context}


def candidate(original: str, query_word: str, context: str) -> dict[str, str]:
    return {"original": original, "query_word": query_word, "context": context}


def run_response(source: str, output: str) -> EasyLanguageResult:
    return simplify_notice(
        NoticeGlossaryInput(text=source),
        api_key=API_KEY,
        request=lambda **kwargs: output,
        clock=lambda: GENERATED_AT,
    )


def run_rewrite(text: str, **kwargs) -> EasyLanguageResult:
    return run_response(text, rewrite_response(text, **kwargs))


# --- Legacy replacement rows: still readable and revalidated -------------------


def test_repeated_term_uses_each_exact_context_and_original_offsets() -> None:
    text = "나이 산정 기준\n지원금 산정 방식\n산정의 뜻은 문맥에 따라 확인합니다."
    result = legacy_result(
        text,
        proposal("산정", "계산", "지원금 산정 방식"),
        proposal("산정", "계산", "나이 산정 기준"),
    )
    assert (
        result.easy_text == "나이 계산 기준\n지원금 계산 방식\n산정의 뜻은 문맥에 따라 확인합니다."
    )
    assert result.original_text == text
    assert result.source_hash == source_hash(text)
    assert result.easy_result is None
    assert [(change.start, change.end) for change in result.changes] == [
        (text.index("산정"), text.index("산정") + 2),
        (
            text.index("산정", text.index("산정") + 1),
            text.index("산정", text.index("산정") + 1) + 2,
        ),
    ]


def test_same_surface_can_have_different_contextual_replacements() -> None:
    result = legacy_result(
        "민원 접수 안내\n서류 접수 완료",
        proposal("접수", "받기", "민원 접수 안내"),
        proposal("접수", "받음", "서류 접수 완료"),
    )
    assert result.easy_text == "민원 받기 안내\n서류 받음 완료"
    assert len(result.changes) == 2


def test_contextual_phrase_replaces_only_the_small_needed_span() -> None:
    text = "안내: 제출서류 지참 후 창구에 방문하세요. 신청 마감일은 그대로입니다."
    original = "제출서류 지참 후"
    result = legacy_result(text, proposal(original, "제출서류를 가져온 후", text))
    assert (
        result.easy_text
        == "안내: 제출서류를 가져온 후 창구에 방문하세요. 신청 마감일은 그대로입니다."
    )
    assert result.changes[0].start == text.index(original)
    assert result.changes[0].end == text.index(original) + len(original)


@pytest.mark.parametrize(
    ("text", "original", "replacement", "expected"),
    [
        ("공종이 변경됩니다.", "공종이", "공사 종류가", "공사 종류가 변경됩니다."),
        (
            "서류를 지참하시어 방문하세요.",
            "지참하시어",
            "가져오셔서",
            "서류를 가져오셔서 방문하세요.",
        ),
    ],
)
def test_particle_or_ending_is_included_when_needed_for_natural_korean(
    text: str, original: str, replacement: str, expected: str
) -> None:
    result = legacy_result(text, proposal(original, replacement, text))
    assert result.easy_text == expected
    assert result.changes[0].original == original


def test_equivalent_multiword_terms_preserve_all_characters_outside_span() -> None:
    prefix = "\t(  \x00"
    suffix = "):​ \r\n"
    text = prefix + "나이 산정" + suffix
    result = legacy_result(text, proposal("나이 산정", "나이 계산", text))
    assert result.easy_text == prefix + "나이 계산" + suffix
    assert result.changes[0].start == len(prefix)


def test_ordinary_spaces_can_be_used_in_a_short_equivalent_term() -> None:
    result = legacy_result("리뉴얼 안내", proposal("리뉴얼", "새롭게 고침", "리뉴얼 안내"))
    assert result.easy_text == "새롭게 고침 안내"


@pytest.mark.parametrize(
    ("text", "changes"),
    [
        ("나이 산정 기준", [proposal("없는말", "계산", "나이 산정 기준")]),
        (
            "증빙서류를 제출하세요.",
            [proposal("구비서류를", "준비할 서류를", "증빙서류를 제출하세요.")],
        ),
        ("나이 산정 기준", [proposal("산정", "계산", "나이  산정 기준")]),
        ("나이 산정 기준", [proposal("산정", "계산", "원문에 없는 문맥")]),
        ("산정 안내\n산정 안내", [proposal("산정", "계산", "산정 안내")]),
        ("산정 및 산정 안내", [proposal("산정", "계산", "산정 및 산정 안내")]),
        ("나이 산정 기준", [proposal("산정", "계산", "나이 산정 기준")] * 2),
        (
            "나이 산정 기준",
            [
                proposal("나이 산정", "나이 계산", "나이 산정 기준"),
                proposal("산정", "계산", "나이 산정 기준"),
            ],
        ),
        ("재검토 안내", [proposal("검토", "살펴보기", "재검토 안내")]),
        ("검토합니다", [proposal("검토", "살펴보기", "검토합니다")]),
        ("2026년 안내", [proposal("2026년", "올해", "2026년 안내")]),
        ("산정 안내", [proposal("산정", "계산 2", "산정 안내")]),
        ("월요일 안내", [proposal("월요일", "평일", "월요일 안내")]),
        ("문의 abc@example.kr", [proposal("abc", "가나다", "문의 abc@example.kr")]),
        ("산정 안내", [proposal("산정", "산정", "산정 안내")]),
        ("산정 안내", [proposal("산정", "산 정", "산정 안내")]),
        ("검토 안내", [proposal("검토", "새 문장\n추가", "검토 안내")]),
        ("서류 검토 안내", [proposal("검토", "살펴​보기", "서류 검토 안내")]),
        ("서류 검토 안내", [proposal("검토", "살펴보기(확인)", "서류 검토 안내")]),
        ("서류 검\t토 안내", [proposal("검\t토", "살펴보기", "서류 검\t토 안내")]),
    ],
)
def test_legacy_rows_with_invalid_spans_or_terms_are_rejected(
    text: str, changes: list[dict[str, str]]
) -> None:
    with pytest.raises(ValueError):
        legacy_result(text, *changes)


@pytest.mark.parametrize(
    "alteration",
    ["hash", "original", "easy", "offset", "context", "order", "timestamp", "attempts"],
)
def test_stored_result_revalidates_hash_offsets_context_and_reconstruction(alteration: str) -> None:
    result = legacy_result(
        "나이 산정 기준\n서류 검토 안내",
        proposal("산정", "계산", "나이 산정 기준"),
        proposal("검토", "살펴보기", "서류 검토 안내"),
    )
    payload = result.model_dump(mode="json")
    if alteration == "hash":
        payload["source_hash"] = "0" * 64
    elif alteration == "original":
        payload["original_text"] += "\n"
    elif alteration == "easy":
        payload["easy_text"] = "새로운 내용"
    elif alteration == "offset":
        payload["changes"][0]["start"] += 1
        payload["changes"][0]["end"] += 1
    elif alteration == "context":
        payload["changes"][0]["context"] = "존재하지 않는 문맥"
    elif alteration == "order":
        payload["changes"].reverse()
    elif alteration == "timestamp":
        payload["generated_at"] = "2026-10-07T01:02:03"
    else:
        payload["attempt_count"] = 3
    with pytest.raises(ValidationError):
        EasyLanguageResult.model_validate(payload)


def test_apply_changes_rejects_out_of_range_or_wrong_original() -> None:
    wrong = AppliedChange(
        start=99, end=101, original="검토", replacement="살펴보기", context="검토 안내"
    )
    with pytest.raises(ValueError):
        apply_easy_language_changes("검토 안내", (wrong,))


def test_historical_v4_result_without_removed_field_can_still_be_loaded() -> None:
    historical = legacy_result("소명 안내", prompt_version="easy-language-v4")
    assert historical.prompt_version == "easy-language-v4"
    assert historical.easy_result is None
    assert "dictionary_terms" not in historical.model_dump(mode="json")


def test_stored_result_rejects_removed_dictionary_field() -> None:
    payload = legacy_result("소명 안내").model_dump(mode="json")
    payload["dictionary_terms"] = ["소명"]
    with pytest.raises(ValidationError):
        EasyLanguageResult.model_validate(payload)


def test_dictionary_candidate_can_overlap_a_legacy_change() -> None:
    text = "공종을 확인하세요."
    result = legacy_result(
        text,
        proposal("공종을", "공사 종류를", text),
        candidates=(candidate("공종을", "공종", text),),
    )
    assert result.easy_text == "공사 종류를 확인하세요."
    assert result.dictionary_candidates[0].start == result.changes[0].start


@pytest.mark.parametrize("missing", [False, True])
def test_legacy_result_candidates_remain_unknown_until_new_extraction(missing) -> None:
    payload = legacy_result("소급 적용", prompt_version="easy-language-v7").model_dump(mode="json")
    if missing:
        del payload["dictionary_candidates"]
    else:
        payload["dictionary_candidates"] = None
    assert EasyLanguageResult.model_validate(payload).dictionary_candidates is None
    payload["dictionary_candidates"] = []
    assert EasyLanguageResult.model_validate(payload).dictionary_candidates == ()


# --- Rewrite generation -------------------------------------------------------


def test_rewrite_keeps_exact_original_and_flattens_easy_text() -> None:
    text = "🦝é\t원문\r\n  검토를 부탁합니다.\r\n\n2026년 10월 7일 10:30\n" + "기존 문장\n" * 500
    calls = []

    def request(**kwargs: str) -> str:
        calls.append(kwargs)
        return rewrite_response(text)

    source = NoticeGlossaryInput(notice_id=22, notice_revision="a" * 64, text=text)
    result = simplify_notice(source, api_key=API_KEY, request=request, clock=lambda: GENERATED_AT)
    assert calls[0]["notice_text"] == text
    assert result.original_text == text
    assert result.changes == ()
    assert result.easy_result is not None
    assert result.easy_text == flatten_easy_rewrite(result.easy_result)
    assert (result.notice_id, result.notice_revision) == (22, "a" * 64)
    assert result.model == DEFAULT_MODEL
    assert result.prompt_version == PROMPT_VERSION == "easy-rewrite-v3"
    assert result.generated_at == GENERATED_AT
    assert (
        result.model_dump_json()
        == EasyLanguageResult.model_validate_json(result.model_dump_json()).model_dump_json()
    )


def test_successful_rewrite_with_no_candidates_needs_no_retry() -> None:
    calls = []

    def request(**kwargs: str) -> str:
        calls.append(kwargs)
        return rewrite_response(kwargs["notice_text"])

    result = simplify_notice(
        NoticeGlossaryInput(text="  쉬운 안내\r\n\t신청하세요.\n"), api_key=API_KEY, request=request
    )
    assert result.dictionary_candidates == ()
    assert result.attempt_count == len(calls) == 1
    assert result.generated_at.utcoffset() is not None


def test_direct_text_provenance_stays_unknown_and_old_result_json_remains_readable() -> None:
    result = run_rewrite("공지 제목\n붙여 넣은 본문 또는 추출 텍스트")
    assert result.body_text_present is None
    assert result.attachment_content_included is None
    payload = result.model_dump()
    del payload["body_text_present"]
    del payload["attachment_content_included"]
    assert EasyLanguageResult.model_validate(payload) == result


@pytest.mark.parametrize("body", [False, True])
def test_db_provenance_changes_only_local_result_metadata_not_gemini_input(body) -> None:
    from pipeline.glossary.easy_language import NoNoticeBodyError
    from pipeline.glossary.source import StoredNoticeInput

    source = StoredNoticeInput(
        notice_id=22,
        notice_revision="a" * 64,
        text="공지 제목\n본문 안내" if body else "공지 제목",
        title="공지 제목",
        body_text_present=body,
    )
    request = []

    def call(**kwargs):
        request.append(kwargs)
        return rewrite_response(kwargs["notice_text"])

    if not body:
        with pytest.raises(NoNoticeBodyError):
            simplify_notice(source, api_key=API_KEY, request=call)
        assert request == []
        return
    result = simplify_notice(source, api_key=API_KEY, request=call)
    assert result.body_text_present is True
    assert result.attachment_content_included is False
    assert request[0]["notice_text"] == "본문 안내"
    assert result.original_title == "공지 제목"
    assert result.easy_text == "공지 제목\n" + flatten_easy_rewrite(result.easy_result)
    assert "body_text_present" not in request[0]


@pytest.mark.parametrize(
    "metadata",
    [
        {"body_text_present": False},
        {"attachment_content_included": False},
        {"body_text_present": "false", "attachment_content_included": False},
    ],
)
def test_partial_or_coerced_provenance_cannot_be_presented_as_a_known_result(metadata) -> None:
    payload = run_rewrite("원문").model_dump()
    with pytest.raises(ValidationError):
        EasyLanguageResult.model_validate({**payload, **metadata})


@pytest.mark.parametrize("extra", ["body_text_present", "dictionary_terms", "changes", "easy_text"])
def test_model_cannot_add_fields_outside_the_response_schema(extra) -> None:
    output = json.loads(rewrite_response("공지 본문"))
    output[extra] = [] if extra != "body_text_present" else True
    with pytest.raises(EasyLanguageValidationError, match="two attempts"):
        run_response("공지 본문", json.dumps(output, ensure_ascii=False))


TEXT = "\t전체 원문\r\n나이 산정 기준\n" + "이 줄도 원문입니다.\n" * 100


@pytest.mark.parametrize(
    "bad_output",
    [
        "{",
        "```json\n{}\n```",
        "{}",
        json.dumps({"rewrite": rewrite(TEXT)}, ensure_ascii=False),
        '{"dictionary_candidates":[]}',
        '{"rewrite":null,"dictionary_candidates":[]}',
        json.dumps({"rewrite": rewrite(TEXT), "dictionary_candidates": None}, ensure_ascii=False),
        '{"changes":[],"dictionary_candidates":[]}',
        rewrite_response(TEXT, rewrite_payload=rewrite(TEXT, sections=[])),
    ],
)
def test_invalid_response_retries_once_with_same_complete_original(bad_output: str) -> None:
    calls = []

    def request(**kwargs: str) -> str:
        calls.append(kwargs)
        return bad_output if len(calls) == 1 else rewrite_response(TEXT)

    result = simplify_notice(NoticeGlossaryInput(text=TEXT), api_key=API_KEY, request=request)
    assert result.attempt_count == 2
    assert [call["notice_text"] for call in calls] == [TEXT, TEXT]
    assert calls[0]["model"] == calls[1]["model"] == DEFAULT_MODEL
    assert calls[0]["api_key"] == calls[1]["api_key"] == API_KEY
    assert "rewrite와 dictionary_candidates를 모두 반환" in calls[1]["prompt"]


@pytest.mark.parametrize(
    ("bad_sentence", "fixed_hint"),
    [
        (
            sentence("공종을 넓혀요.", "지원 공종을 확대 private-response-marker"),
            "evidence 중 원문과 글자가 다른 구절이 있었습니다.",
        ),
        (
            sentence("공종 3개를 넓혀요.", "지원 공종을 확대합니다."),
            "원문에 없는 숫자를 추가하지 마세요.",
        ),
        (
            sentence("https://example.test", "지원 공종을 확대합니다."),
            "URL을 원문 그대로 별도 text 항목에 쓰고",
        ),
    ],
)
def test_semantic_checks_are_delegated_without_local_retry(
    bad_sentence: dict[str, object], fixed_hint: str
) -> None:
    text = "지원 공종을 확대합니다.\n기존 안내는 유지합니다."
    bad = rewrite(text, sections=[{
        "heading": "무엇이 바뀌나요?", "style": "paragraph", "sentences": [bad_sentence],
    }])
    calls = []

    def request(**kwargs: str) -> str:
        calls.append(kwargs)
        return rewrite_response(text, rewrite_payload=bad if len(calls) == 1 else None)

    result = simplify_notice(NoticeGlossaryInput(text=text), api_key=API_KEY, request=request)
    assert result.attempt_count == len(calls) == 1
    assert result.original_text == text
    assert result.easy_result.review.status == "passed"



def test_failed_rewrite_twice_is_not_saved_as_partial_success() -> None:
    text = "지원 공종을 확대합니다."
    bad = rewrite(text, sections=[{
        "heading": "무엇이 바뀌나요?",
        "style": "paragraph",
        "sentences": [
            sentence("공종을 넓혀요.", "지원 공종을 확대합니다."),
            sentence("", "원문에 없는 문장"),
        ],
    }])
    calls = []

    def request(**kwargs: str) -> str:
        calls.append(kwargs)
        return rewrite_response(text, rewrite_payload=bad)

    with pytest.raises(EasyLanguageValidationError, match="two attempts"):
        simplify_notice(NoticeGlossaryInput(text=text), api_key=API_KEY, request=request)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "error", [RuntimeError(API_KEY), EasyLanguageAPIError(API_KEY), httpx.ReadTimeout(API_KEY)]
)
def test_request_errors_are_redacted_and_not_retried(error: Exception) -> None:
    calls = []

    def request(**kwargs: str) -> str:
        calls.append(kwargs)
        raise error

    with pytest.raises(EasyLanguageAPIError) as captured:
        simplify_notice(NoticeGlossaryInput(text="산정 안내"), api_key=API_KEY, request=request)
    assert len(calls) == 1
    assert API_KEY not in str(captured.value)
    assert API_KEY not in "".join(traceback.format_exception(captured.value))
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__


def test_failed_retry_after_invalid_json_remains_an_api_failure() -> None:
    calls = 0

    def request(**kwargs: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return "{"
        raise RuntimeError(API_KEY)

    with pytest.raises(EasyLanguageAPIError):
        simplify_notice(NoticeGlossaryInput(text="검토 안내"), api_key=API_KEY, request=request)
    assert calls == 2


def test_retry_scheduling_metadata_survives_easy_language_error_redaction() -> None:
    from pipeline.gemini_execution import GeminiExecutionError

    def request(**kwargs: str) -> str:
        raise GeminiExecutionError(
            API_KEY, failure_kind="deferred", retryable=True, retry_at=GENERATED_AT,
            status_code=429,
        )

    with pytest.raises(EasyLanguageAPIError) as captured:
        simplify_notice(NoticeGlossaryInput(text="검토 안내"), api_key=API_KEY, request=request)
    assert captured.value.failure_kind == "deferred"
    assert captured.value.retry_at == GENERATED_AT
    assert captured.value.retryable is True
    assert captured.value.status_code == 429
    assert API_KEY not in "".join(traceback.format_exception(captured.value))


def test_correction_uses_the_original_deadline_and_rejects_late_valid_response(monkeypatch) -> None:
    from pipeline import gemini_execution

    now = [0.0]
    monkeypatch.setattr(gemini_execution.time, "monotonic", lambda: now[0])
    budget = gemini_execution.ExecutionBudget(timeout_seconds=10)
    calls = []

    def request(**kwargs: str) -> str:
        calls.append(kwargs)
        now[0] += 6.0
        return "{" if len(calls) == 1 else rewrite_response(kwargs["notice_text"])

    with pytest.raises(EasyLanguageAPIError) as captured:
        simplify_notice(
            NoticeGlossaryInput(text="검토 안내"), api_key=API_KEY, request=request, budget=budget
        )
    assert len(calls) == 2
    assert captured.value.reason_code == "api_timeout"
    assert captured.value.failure_kind == "deadline"


def test_expired_second_validation_is_reported_as_deadline_not_permanent_failure(monkeypatch):
    from pipeline import gemini_execution

    now = [0.0]
    monkeypatch.setattr(gemini_execution.time, "monotonic", lambda: now[0])
    budget = gemini_execution.ExecutionBudget(timeout_seconds=10)
    validations = []

    def invalid_rewrite(*args):
        validations.append(True)
        if len(validations) == 2:
            now[0] = 11.0
        raise ValueError("invalid rewrite")

    monkeypatch.setattr(easy_language, "_parse_rewrite_response", invalid_rewrite)
    with pytest.raises(EasyLanguageAPIError) as captured:
        simplify_notice(
            NoticeGlossaryInput(text="검토 안내"), api_key=API_KEY,
            request=lambda **kwargs: rewrite_response(kwargs["notice_text"]), budget=budget,
        )
    assert len(validations) == 2
    assert captured.value.failure_kind == "deadline"
    assert captured.value.retryable is True


def _with_text(text: str, written: str) -> str:
    return rewrite_response(text, rewrite_payload=rewrite(text, headline=written))


def test_invalid_model_output_error_hides_response_and_credentials() -> None:
    with pytest.raises(EasyLanguageValidationError) as captured:
        run_response("검토 안내", _with_text("검토 안내", API_KEY + "\n"))
    assert API_KEY not in "".join(traceback.format_exception(captured.value))


@pytest.mark.parametrize("encoded", [False, True])
def test_model_credential_echo_retries_once_then_fails_without_public_leak(encoded, caplog) -> None:
    key = "SyntheticGeminiCredential"
    output = _with_text("나이 산정 기준", key)
    if encoded:
        output = output.replace(key, "".join(f"\\u{ord(char):04x}" for char in key))
        assert key not in output
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        return output

    with pytest.raises(EasyLanguageValidationError) as captured:
        simplify_notice(
            NoticeGlossaryInput(text="나이 산정 기준"), api_key=f"  {key}  ", request=request
        )
    assert len(calls) == 2
    assert key not in str(captured.value)
    assert key not in "".join(traceback.format_exception(captured.value))
    assert key not in caplog.text
    assert captured.value.__cause__ is None and captured.value.__suppress_context__
    assert key not in calls[1]["prompt"]


def test_credential_echo_retry_can_recover_with_a_clean_response() -> None:
    key = "SyntheticGeminiCredential"
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        text = kwargs["notice_text"]
        return _with_text(text, key) if len(calls) == 1 else rewrite_response(text)

    result = simplify_notice(
        NoticeGlossaryInput(text="나이 산정 기준"), api_key=key, request=request
    )
    assert result.attempt_count == len(calls) == 2
    assert key not in result.model_dump_json()
    assert key not in calls[1]["prompt"]


@pytest.mark.parametrize("encode", [quote, quote_plus])
def test_url_encoded_credential_echo_is_rejected_without_reflecting_its_value(encode) -> None:
    key = "Synthetic Credential/Secret"
    encoded = encode(key, safe="")
    assert encoded != key
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        return _with_text("나이 산정 기준", encoded)

    with pytest.raises(EasyLanguageValidationError) as captured:
        simplify_notice(NoticeGlossaryInput(text="나이 산정 기준"), api_key=key, request=request)
    assert len(calls) == 2
    assert key not in "".join(traceback.format_exception(captured.value))
    assert encoded not in "".join(traceback.format_exception(captured.value))
    assert key not in calls[1]["prompt"] and encoded not in calls[1]["prompt"]


def test_new_unicode_escaped_credential_in_evidence_is_rejected() -> None:
    key = "SyntheticGeminiCredential"
    payload = rewrite("나이 산정 기준", sections=[{
        "heading": "무엇인가요?", "style": "paragraph",
        "sentences": [sentence("나이를 계산해요.", key)],
    }])
    output = rewrite_response("나이 산정 기준", rewrite_payload=payload)
    output = output.replace(key, "".join(f"\\u{ord(char):04x}" for char in key))
    with pytest.raises(EasyLanguageValidationError) as captured:
        simplify_notice(
            NoticeGlossaryInput(text="나이 산정 기준"), api_key=key, request=lambda **kw: output
        )
    assert key not in "".join(traceback.format_exception(captured.value))


def test_evidence_can_quote_a_credential_already_in_the_source_but_text_cannot() -> None:
    key = "SyntheticGeminiCredential"
    text = f"{key}: 나이 산정 기준"
    quoted = rewrite(text, sections=[{
        "heading": "무엇인가요?", "style": "paragraph",
        "sentences": [sentence("나이 계산 기준이에요.", text)],
    }])
    result = simplify_notice(
        NoticeGlossaryInput(text=text), api_key=key,
        request=lambda **kwargs: rewrite_response(text, rewrite_payload=quoted),
    )
    assert result.attempt_count == 1
    assert key not in result.easy_text
    with pytest.raises(EasyLanguageValidationError):
        simplify_notice(
            NoticeGlossaryInput(text=text), api_key=key, request=lambda **kw: _with_text(text, key)
        )


@pytest.mark.parametrize(
    ("key", "model"), [("", DEFAULT_MODEL), (" ", DEFAULT_MODEL), (API_KEY, " ")]
)
def test_missing_settings_fail_before_request(key: str, model: str) -> None:
    def request(**kwargs: str) -> str:
        pytest.fail("invalid settings must not send a request")

    with pytest.raises(EasyLanguageConfigurationError):
        simplify_notice(NoticeGlossaryInput(text="공지"), api_key=key, model=model, request=request)


def test_prompt_loads_exact_text_and_rejects_missing_or_empty_file(tmp_path, monkeypatch) -> None:
    path = tmp_path / "prompt.md"
    monkeypatch.setattr(easy_language, "PROMPT_PATH", path)
    with pytest.raises(EasyLanguageConfigurationError):
        load_easy_language_prompt()
    path.write_text("  \n", encoding="utf-8")
    with pytest.raises(EasyLanguageConfigurationError):
        load_easy_language_prompt()
    path.write_bytes(b"\xef\xbb\xbf" + "  지시\n".encode())
    assert load_easy_language_prompt() == "  지시\n"


def test_prompt_asks_for_question_sections_evidence_and_dictionary_candidates() -> None:
    prompt = load_easy_language_prompt()
    assert PROMPT_VERSION == "easy-rewrite-v3"
    assert easy_language.PROMPT_PATH.name == "gemini_easy_rewrite.md"
    for phrase in ("rewrite", "evidence", "dictionary_candidates", "query_word", "?"):
        assert phrase in prompt
    assert "changes" not in prompt and "dictionary_terms" not in prompt


def test_models_are_immutable_and_require_all_response_fields() -> None:
    change = ProposedChange(**proposal("산정", "계산", "나이 산정 기준"))
    with pytest.raises(ValidationError):
        change.replacement = "산꼭대기"
    with pytest.raises(ValidationError):
        ProposedChange.model_validate({**change.model_dump(), "reason": "쉬운 말"})
    response = EasyLanguageResponse.model_validate({"changes": [], "dictionary_candidates": []})
    assert response.changes == response.dictionary_candidates == ()
    rewrite_payload = rewrite("나이 산정 기준")
    parsed = EasyRewriteResponse.model_validate(
        {"rewrite": rewrite_payload, "dictionary_candidates": []}
    )
    with pytest.raises(ValidationError):
        parsed.rewrite = None
    for incomplete in ({}, {"rewrite": rewrite_payload}, {"dictionary_candidates": []}):
        with pytest.raises(ValidationError):
            EasyRewriteResponse.model_validate(incomplete)


# --- Dictionary candidates from the same rewrite response ----------------------


def test_dictionary_candidates_keep_distinct_occurrences_and_deduplicate_same_position() -> None:
    text = "🦝é 소급 적용 안내\n소급 지급 안내"
    first = candidate("소급", "소급", "소급 적용 안내")
    second = candidate("소급", "소급", "소급 지급 안내")
    result = run_rewrite(text, candidates=(second, first, {**first, "context": "🦝é 소급 적용"}))
    assert result.original_text == text
    assert result.changes == ()
    assert [(item.start, item.end, item.query_word) for item in result.dictionary_candidates] == [
        (text.index("소급"), text.index("소급") + 2, "소급"),
        (text.rindex("소급"), text.rindex("소급") + 2, "소급"),
    ]
    assert EasyLanguageResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "original,query_word",
    [
        ("공종을", "공종"),
        ("지참하시어", "지참하다"),
        ("상이한", "상이하다"),
        ("역", "역"),
        ("e-메일", "e-메일"),
        ("가압류·가처분", "가압류·가처분"),
    ],
)
def test_dictionary_candidates_allow_inflections_single_characters_and_lexical_punctuation(
    original: str, query_word: str
) -> None:
    text = f"안내: {original} 확인"
    result = run_rewrite(text, candidates=(candidate(original, query_word, text),))
    assert result.dictionary_candidates[0].query_word == query_word


@pytest.mark.parametrize(
    "text,candidates",
    [
        ("소급 적용", (candidate("없는말", "없는말", "소급 적용"),)),
        ("소급 적용", (candidate("소급", "소급", "새 문맥"),)),
        ("소급 적용\n소급 적용", (candidate("소급", "소급", "소급 적용"),)),
        ("소급 및 소급", (candidate("소급", "소급", "소급 및 소급"),)),
        ("공종을 확인", (candidate("공종", "공종", "공종을 확인"),)),
        ("재검토 안내", (candidate("검토", "검토", "재검토 안내"),)),
        (
            "소급 적용",
            (candidate("소급", "소급", "소급 적용"), candidate("소급", "소급하다", "소급 적용")),
        ),
        (
            "소급 지급 안내",
            (
                candidate("소급 지급", "소급 지급", "소급 지급 안내"),
                candidate("소급", "소급", "소급 지급 안내"),
            ),
        ),
        ("소급 적용", ({**candidate("소급", "소급", "소급 적용"), "start": 0},)),
        ("월요일 안내", (candidate("월요일", "요일", "월요일 안내"),)),
        ("문의 abc@example.kr", (candidate("abc", "abc", "문의 abc@example.kr"),)),
        (
            "https://example.kr/소급 안내",
            (candidate("소급", "소급", "https://example.kr/소급 안내"),),
        ),
    ],
)
def test_invalid_dictionary_candidate_spans_are_excluded_without_retry(text, candidates) -> None:
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        return rewrite_response(text, candidates=candidates)

    result = simplify_notice(NoticeGlossaryInput(text=text), api_key=API_KEY, request=request)
    assert [item["notice_text"] for item in calls] == [text]
    assert result.attempt_count == 1
    assert len(result.dictionary_candidates) == (1 if len(candidates) == 2 else 0)
    if result.dictionary_candidates:
        assert result.dictionary_candidates[0].original == candidates[0]["original"]
    assert result.easy_result is not None


@pytest.mark.parametrize(
    "query_word",
    [
        "", " 소급", "소급 ", "소급\n적용", "소급\t적용", "소​급", "소급 적용",
        "https://example.kr", "소급&key=secret", "소급?start=1", "소급*", "site:소급", "-소급",
        '"소급"', "소급 OR 공종", "소급_적용", "소급%20적용", "2026년",
    ],
)
def test_dictionary_query_rejects_formats_operators_and_hidden_characters(query_word) -> None:
    result = run_rewrite("소급 적용", candidates=(candidate("소급", query_word, "소급 적용"),))
    assert result.dictionary_candidates == ()
    assert result.easy_result is not None and result.attempt_count == 1


@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_candidate_query_credentials_are_rejected_even_if_source_contains_them(
    encoded, existing, caplog
) -> None:
    key = "SyntheticGeminiCredential"
    text = f"소급 적용\n{key}" if existing else "소급 적용"
    output = rewrite_response(text, candidates=(candidate("소급", key, "소급 적용"),))
    if encoded:
        output = output.replace(key, "".join(f"\\u{ord(char):04x}" for char in key))
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        return output

    with pytest.raises(EasyLanguageValidationError) as caught:
        simplify_notice(NoticeGlossaryInput(text=text), api_key=key, request=request)
    assert len(calls) == 2
    assert key not in "".join(traceback.format_exception(caught.value))
    assert key not in calls[1]["prompt"] and key not in caplog.text


def test_candidate_context_may_attest_an_existing_credential_but_not_select_it() -> None:
    key = "SyntheticGeminiCredential"
    text = f"{key}: 소급 적용"
    result = run_rewrite(text, candidates=(candidate("소급", "소급", text),))
    assert result.dictionary_candidates[0].context == text
    with pytest.raises(EasyLanguageValidationError):
        simplify_notice(
            NoticeGlossaryInput(text=text), api_key=key,
            request=lambda **kw: rewrite_response(text, candidates=(candidate(key, "인증", text),)),
        )


@pytest.mark.parametrize("alteration", ["offset", "original", "context", "order", "duplicate"])
def test_stored_candidates_revalidate_source_and_offsets_independently(alteration) -> None:
    result = run_rewrite(
        "소급 적용\n직권처리 안내",
        candidates=(
            candidate("소급", "소급", "소급 적용"),
            candidate("직권처리", "직권처리", "직권처리 안내"),
        ),
    )
    payload = result.model_dump(mode="json")
    entries = payload["dictionary_candidates"]
    if alteration == "offset":
        entries[0]["start"] += 1
        entries[0]["end"] += 1
    elif alteration == "original":
        entries[0]["original"] = "익일"
    elif alteration == "context":
        entries[0]["context"] = "원문에 없는 문맥"
    elif alteration == "order":
        entries.reverse()
    else:
        entries.append(entries[0].copy())
    with pytest.raises(ValidationError):
        EasyLanguageResult.model_validate(payload)


def test_candidate_models_reject_coercion_extra_fields_and_invalid_offsets() -> None:
    proposal_data = candidate("소급", "소급", "소급 적용")
    proposed = ProposedDictionaryCandidate(**proposal_data)
    with pytest.raises(ValidationError):
        proposed.query_word = "소급하다"
    for extra in ({"replacement": "쉬운말"}, {"start": 0}, {"query_word": 123}):
        with pytest.raises(ValidationError):
            ProposedDictionaryCandidate.model_validate({**proposal_data, **extra})
    for start, end in ((True, 2), (0, 1), (2, 0), (-1, 1)):
        with pytest.raises(ValidationError):
            DictionaryCandidate(**proposal_data, start=start, end=end)


def test_custom_model_is_kept_with_result() -> None:
    model = "custom-gemini-model"
    calls = []

    def request(**kwargs: str) -> str:
        calls.append(kwargs)
        return rewrite_response(kwargs["notice_text"])

    result = simplify_notice(
        NoticeGlossaryInput(text="공지"), api_key=API_KEY, model=model, request=request
    )
    assert calls[0]["model"] == result.model == model


def test_legacy_prompt_version_constant_is_not_the_current_generation() -> None:
    assert LEGACY_PROMPT_VERSION != PROMPT_VERSION
