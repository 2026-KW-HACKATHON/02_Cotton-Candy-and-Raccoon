"""Exact source preservation and bounded Gemini replacement validation."""

import json
import traceback
from datetime import UTC, datetime
from urllib.parse import quote, quote_plus

import httpx
import pytest
from pydantic import ValidationError

from pipeline.glossary import easy_language
from pipeline.glossary.easy_language import (
    DEFAULT_MODEL,
    PROMPT_VERSION,
    AppliedChange,
    EasyLanguageAPIError,
    EasyLanguageConfigurationError,
    EasyLanguageResponse,
    EasyLanguageResult,
    EasyLanguageValidationError,
    ProposedChange,
    apply_easy_language_changes,
    load_easy_language_prompt,
    simplify_notice,
)
from pipeline.glossary.source import NoticeGlossaryInput, source_hash

GENERATED_AT = datetime(2026, 10, 7, 1, 2, 3, tzinfo=UTC)
API_KEY = "test-secret-do-not-display"


def response_json(*changes: dict[str, str]) -> str:
    return json.dumps({"changes": changes}, ensure_ascii=False)


def proposal(original: str, replacement: str, context: str) -> dict[str, str]:
    return {"original": original, "replacement": replacement, "context": context}


def run_response(source: str, output: str) -> EasyLanguageResult:
    return simplify_notice(
        NoticeGlossaryInput(text=source),
        api_key=API_KEY,
        request=lambda **kwargs: output,
        clock=lambda: GENERATED_AT,
    )


def test_repeated_term_uses_each_exact_context_and_original_offsets() -> None:
    text = "나이 산정 기준\n지원금 산정 방식\n산정의 뜻은 문맥에 따라 확인합니다."
    result = run_response(
        text,
        response_json(
            proposal("산정", "계산", "지원금 산정 방식"),
            proposal("산정", "계산", "나이 산정 기준"),
        ),
    )
    assert (
        result.easy_text == "나이 계산 기준\n지원금 계산 방식\n산정의 뜻은 문맥에 따라 확인합니다."
    )
    assert result.original_text == text
    assert result.source_hash == source_hash(text)
    assert result.model == DEFAULT_MODEL
    assert result.prompt_version == PROMPT_VERSION
    assert result.generated_at == GENERATED_AT
    assert result.attempt_count == 1
    assert [(change.start, change.end) for change in result.changes] == [
        (text.index("산정"), text.index("산정") + 2),
        (
            text.index("산정", text.index("산정") + 1),
            text.index("산정", text.index("산정") + 1) + 2,
        ),
    ]


def test_same_surface_can_have_different_contextual_replacements() -> None:
    text = "민원 접수 안내\n서류 접수 완료"
    result = run_response(
        text,
        response_json(
            proposal("접수", "받기", "민원 접수 안내"),
            proposal("접수", "받음", "서류 접수 완료"),
        ),
    )
    assert result.easy_text == "민원 받기 안내\n서류 받음 완료"
    assert len(result.changes) == 2


def test_contextual_phrase_replaces_only_the_small_needed_span() -> None:
    text = "안내: 제출서류 지참 후 창구에 방문하세요. 신청 마감일은 그대로입니다."
    original = "제출서류 지참 후"
    replacement = "제출서류를 가져온 후"
    result = run_response(text, response_json(proposal(original, replacement, text)))
    assert (
        result.easy_text
        == "안내: 제출서류를 가져온 후 창구에 방문하세요. 신청 마감일은 그대로입니다."
    )
    assert result.changes[0].start == text.index(original)
    assert result.changes[0].end == text.index(original) + len(original)
    assert result.original_text == text


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
    result = run_response(text, response_json(proposal(original, replacement, text)))
    assert result.easy_text == expected
    assert result.changes[0].original == original
    assert result.changes[0].replacement == replacement


def test_unicode_whitespace_layout_and_long_source_remain_exact() -> None:
    prefix = "🦝e\u0301\t원문\r\n  "
    suffix = (
        "\r\n\n2026년 10월 7일 10:30 / 02-1234-5678\nhttps://example.kr/notice\n"
        + "기존 문장\n" * 500
    )
    text = prefix + "검토를 부탁합니다." + suffix
    calls = []

    def request(**kwargs: str) -> str:
        calls.append(kwargs)
        return response_json(proposal("검토를", "살펴보기를", "  검토를 부탁합니다."))

    source = NoticeGlossaryInput(notice_id=22, notice_revision="a" * 64, text=text)
    result = simplify_notice(source, api_key=API_KEY, request=request, clock=lambda: GENERATED_AT)
    assert calls[0]["notice_text"] == text
    assert result.original_text == text
    assert result.easy_text == prefix + "살펴보기를 부탁합니다." + suffix
    assert result.changes[0].start == len(prefix)
    assert result.notice_id == 22
    assert result.notice_revision == "a" * 64
    assert (
        result.model_dump_json()
        == EasyLanguageResult.model_validate_json(result.model_dump_json()).model_dump_json()
    )


def test_successful_empty_changes_keeps_original_without_retry() -> None:
    calls = []

    def request(**kwargs: str) -> str:
        calls.append(kwargs)
        return response_json()

    text = "  쉬운 안내\r\n\t신청하세요.\n"
    result = simplify_notice(NoticeGlossaryInput(text=text), api_key=API_KEY, request=request)
    assert result.original_text == result.easy_text == text
    assert result.changes == ()
    assert result.attempt_count == 1
    assert result.generated_at.utcoffset() is not None
    assert len(calls) == 1


def test_legacy_dictionary_field_is_rejected_even_if_changes_are_valid() -> None:
    output = json.dumps(
        {
            "changes": [proposal("산정", "계산", "나이 산정 기준")],
            "dictionary_terms": [],
        },
        ensure_ascii=False,
    )
    with pytest.raises(EasyLanguageValidationError, match="two attempts"):
        run_response("나이 산정 기준", output)


@pytest.mark.parametrize(
    "bad_output",
    [
        "{",
        "```json\n{}\n```",
        "{}",
        '{"changes":null}',
        '{"changes":[{}]}',
        '{"changes":[],"easy_text":"삭제"}',
        '{"changes":[],"dictionary_terms":[]}',
        response_json(proposal("산정", "산정", "나이 산정 기준")),
    ],
)
def test_invalid_response_retries_once_with_same_complete_original(bad_output: str) -> None:
    text = "\t전체 원문\r\n나이 산정 기준\n" + "이 줄도 원문입니다.\n" * 100
    calls = []

    def request(**kwargs: str) -> str:
        calls.append(kwargs)
        return (
            bad_output
            if len(calls) == 1
            else response_json(proposal("산정", "계산", "나이 산정 기준"))
        )

    result = simplify_notice(NoticeGlossaryInput(text=text), api_key=API_KEY, request=request)
    assert result.attempt_count == 2
    assert [call["notice_text"] for call in calls] == [text, text]
    assert calls[0]["model"] == calls[1]["model"] == DEFAULT_MODEL
    assert calls[0]["api_key"] == calls[1]["api_key"] == API_KEY
    assert result.original_text == text
    if '"replacement": "산정"' in bad_output:
        assert "original과 replacement가 같거나 띄어쓰기만 다른" in calls[1]["prompt"]


@pytest.mark.parametrize("replacement", ["소급", "소 급"])
def test_identical_or_spacing_only_change_retries_then_fails(replacement: str) -> None:
    text = "소급 적용 안내"
    calls = []

    def request(**kwargs: str) -> str:
        calls.append(kwargs)
        return response_json(proposal("소급", replacement, text))

    with pytest.raises(EasyLanguageValidationError, match="two attempts"):
        simplify_notice(NoticeGlossaryInput(text=text), api_key=API_KEY, request=request)
    assert len(calls) == 2
    assert [call["notice_text"] for call in calls] == [text, text]
    assert "같거나 띄어쓰기만 다른" in calls[1]["prompt"]


@pytest.mark.parametrize(
    ("bad_change", "fixed_hint"),
    [
        (
            proposal("공종", "공사 종류", "지원 공종을 확대합니다."),
            "이전 응답의 original이 원문 단어의 일부만 포함해 실패했습니다.",
        ),
        (
            proposal("공종을", "공사 종류를", "잘못된 문맥 private-response-marker"),
            "이전 응답의 original 또는 context를 원문에서 한 곳으로 확정하지 못했습니다.",
        ),
    ],
)
def test_source_failure_retry_gives_fixed_reason_and_keeps_exact_original(
    bad_change: dict[str, str], fixed_hint: str
) -> None:
    text = "지원 공종을 확대합니다.\n기존 안내는 유지합니다."
    calls = []

    def request(**kwargs: str) -> str:
        calls.append(kwargs)
        return response_json(
            bad_change
            if len(calls) == 1
            else proposal("공종을", "공사 종류를", "지원 공종을 확대합니다.")
        )

    result = simplify_notice(NoticeGlossaryInput(text=text), api_key=API_KEY, request=request)
    assert result.attempt_count == len(calls) == 2
    assert result.original_text == text
    assert result.easy_text == "지원 공사 종류를 확대합니다.\n기존 안내는 유지합니다."
    assert [call["notice_text"] for call in calls] == [text, text]
    retry_instruction = calls[1]["prompt"][len(calls[0]["prompt"]) :]
    assert fixed_hint in retry_instruction
    assert "private-response-marker" not in retry_instruction
    assert API_KEY not in retry_instruction


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
        ("검토 안내", [{**proposal("검토", "살펴보기", "검토 안내"), "start": 999}]),
    ],
)
def test_invalid_spans_never_apply_or_become_success(
    text: str, changes: list[dict[str, str]]
) -> None:
    calls = []

    def request(**kwargs: str) -> str:
        calls.append(kwargs)
        return response_json(*changes)

    with pytest.raises(EasyLanguageValidationError, match="two attempts"):
        simplify_notice(NoticeGlossaryInput(text=text), api_key=API_KEY, request=request)
    assert len(calls) == 2
    assert all(call["notice_text"] == text for call in calls)


@pytest.mark.parametrize(
    ("text", "change"),
    [
        ("2026년 안내", proposal("2026년", "올해", "2026년 안내")),
        ("산정 안내", proposal("산정", "계산 2", "산정 안내")),
        ("산정 안내", proposal("산정", "계산 ②", "산정 안내")),
        ("월요일 안내", proposal("월요일", "평일", "월요일 안내")),
        ("문의 abc@example.kr", proposal("abc", "가나다", "문의 abc@example.kr")),
        (
            "https://example.kr/검토 안내",
            proposal("검토", "살펴보기", "https://example.kr/검토 안내"),
        ),
        ("산정 안내", proposal("산정", "월요일", "산정 안내")),
        ("산정 안내", proposal("산정", "", "산정 안내")),
        ("산정 안내", proposal("산정", "산정", "산정 안내")),
        ("산정 안내", proposal("산정", " 계산", "산정 안내")),
        ("검토 안내", proposal("검토", "새 문장\n추가", "검토 안내")),
        ("서류를 검토합니다.", proposal("서류를 검토합니다.", "내용 변경", "서류를 검토합니다.")),
    ],
)
def test_protected_data_and_sentence_changes_are_rejected(
    text: str, change: dict[str, str]
) -> None:
    with pytest.raises(EasyLanguageValidationError):
        run_response(text, response_json(change))


@pytest.mark.parametrize(
    "replacement",
    [
        "살펴\t보기",
        "살펴\u2028보기",
        "살펴\u2029보기",
        "살펴\x00보기",
        "살펴\u200b보기",
        "살펴\u00a0보기",
        "살펴보기(확인)",
        "살펴보기[확인]",
        "살펴보기:확인",
    ],
)
def test_term_replacement_cannot_insert_controls_spaces_or_punctuation(replacement: str) -> None:
    with pytest.raises(EasyLanguageValidationError):
        run_response(
            "서류 검토 안내", response_json(proposal("검토", replacement, "서류 검토 안내"))
        )


@pytest.mark.parametrize("original", ["검\t토", "검\u2028토", "검\x00토", "검토(확인)"])
def test_term_span_cannot_remove_source_controls_or_punctuation(original: str) -> None:
    text = f"서류 {original} 안내"
    with pytest.raises(EasyLanguageValidationError):
        run_response(text, response_json(proposal(original, "살펴보기", text)))


def test_equivalent_multiword_terms_preserve_all_characters_outside_span() -> None:
    prefix = "\t(\u2028\u2029\x00"
    suffix = "):\u200b\u00a0\r\n"
    text = prefix + "나이 산정" + suffix
    result = run_response(text, response_json(proposal("나이 산정", "나이 계산", text)))
    assert result.original_text == text
    assert result.easy_text == prefix + "나이 계산" + suffix
    assert result.changes[0].start == len(prefix)
    assert result.changes[0].end == len(prefix) + len("나이 산정")


def test_ordinary_spaces_can_be_used_in_a_short_equivalent_term() -> None:
    result = run_response(
        "리뉴얼 안내", response_json(proposal("리뉴얼", "새롭게 고침", "리뉴얼 안내"))
    )
    assert result.easy_text == "새롭게 고침 안내"


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


def test_invalid_model_output_error_hides_response_and_credentials() -> None:
    with pytest.raises(EasyLanguageValidationError) as captured:
        run_response("검토 안내", response_json(proposal("검토", API_KEY + "\n", "검토 안내")))
    assert API_KEY not in "".join(traceback.format_exception(captured.value))


@pytest.mark.parametrize("encoded", [False, True])
def test_model_credential_echo_retries_once_then_fails_without_public_leak(encoded, caplog) -> None:
    key = "SyntheticGeminiCredential"
    output = response_json(proposal("산정", key, "나이 산정 기준"))
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
    assert [call["notice_text"] for call in calls] == ["나이 산정 기준"] * 2
    assert key not in str(captured.value)
    assert key not in "".join(traceback.format_exception(captured.value))
    assert key not in caplog.text
    assert captured.value.__cause__ is None and captured.value.__suppress_context__
    assert key not in calls[1]["prompt"]
    assert "SyntheticGeminiCredential" not in calls[1]["prompt"]


def test_credential_echo_retry_can_recover_with_a_clean_response() -> None:
    key = "SyntheticGeminiCredential"
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        return response_json(proposal("산정", key if len(calls) == 1 else "계산", "나이 산정 기준"))

    result = simplify_notice(
        NoticeGlossaryInput(text="나이 산정 기준"), api_key=key, request=request
    )
    assert result.attempt_count == len(calls) == 2
    assert result.easy_text == "나이 계산 기준"
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
        return response_json(proposal("산정", encoded, "나이 산정 기준"))

    with pytest.raises(EasyLanguageValidationError) as captured:
        simplify_notice(NoticeGlossaryInput(text="나이 산정 기준"), api_key=key, request=request)
    assert len(calls) == 2
    assert key not in "".join(traceback.format_exception(captured.value))
    assert encoded not in "".join(traceback.format_exception(captured.value))
    assert key not in calls[1]["prompt"] and encoded not in calls[1]["prompt"]


@pytest.mark.parametrize("copied_field", ["original", "context"])
def test_new_unicode_escaped_credential_in_source_fields_is_rejected(copied_field) -> None:
    key = "SyntheticGeminiCredential"
    change = proposal("산정", "계산", "나이 산정 기준")
    change[copied_field] = key
    output = response_json(change)
    output = output.replace(key, "".join(f"\\u{ord(char):04x}" for char in key))
    with pytest.raises(EasyLanguageValidationError) as captured:
        simplify_notice(
            NoticeGlossaryInput(text="나이 산정 기준"),
            api_key=key,
            request=lambda **kwargs: output,
        )
    assert key not in "".join(traceback.format_exception(captured.value))


@pytest.mark.parametrize("encoded", [False, True])
def test_context_can_copy_a_credential_already_in_original_source(encoded) -> None:
    key = "SyntheticGeminiCredential"
    text = f"{key}: 나이 산정 기준"
    output = response_json(proposal("산정", "계산", text))
    if encoded:
        output = output.replace(key, "".join(f"\\u{ord(char):04x}" for char in key))
    result = simplify_notice(
        NoticeGlossaryInput(text=text), api_key=key, request=lambda **kwargs: output
    )
    assert result.attempt_count == 1
    assert result.original_text == text
    assert result.easy_text == f"{key}: 나이 계산 기준"
    assert result.changes[0].context == text


@pytest.mark.parametrize("credential_already_in_source", [False, True])
def test_separate_replacements_cannot_assemble_a_new_credential(
    credential_already_in_source,
) -> None:
    key = "Private-Secret"
    text = f"{key}\nalpha-beta" if credential_already_in_source else "alpha-beta"
    output = response_json(
        proposal("alpha", "Private", "alpha-beta"),
        proposal("beta", "Secret", "alpha-beta"),
    )
    assert key not in output
    with pytest.raises(EasyLanguageValidationError) as captured:
        simplify_notice(
            NoticeGlossaryInput(text=text), api_key=key, request=lambda **kwargs: output
        )
    assert key not in "".join(traceback.format_exception(captured.value))


def test_existing_source_credential_remains_valid_when_earlier_edits_shift_its_position() -> None:
    key = "SyntheticGeminiCredential"
    text = f"산정 안내\n{key}"
    result = simplify_notice(
        NoticeGlossaryInput(text=text),
        api_key=key,
        request=lambda **kwargs: response_json(proposal("산정", "계산하기", "산정 안내")),
    )
    assert result.easy_text == f"계산하기 안내\n{key}"
    assert result.attempt_count == 1


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


def test_prompt_keeps_contextual_minimal_changes_without_dictionary_selection() -> None:
    prompt = load_easy_language_prompt()
    assert PROMPT_VERSION == "easy-language-v6"
    assert "문맥" in prompt
    assert "조사" in prompt and "어미" in prompt
    assert "최소" in prompt
    assert "원문" in prompt
    assert "dictionary_terms" not in prompt


def test_models_are_immutable_and_require_all_response_fields() -> None:
    change = ProposedChange(**proposal("산정", "계산", "나이 산정 기준"))
    with pytest.raises(ValidationError):
        change.replacement = "산꼭대기"
    with pytest.raises(ValidationError):
        EasyLanguageResponse.model_validate({})
    assert EasyLanguageResponse.model_validate({"changes": []}).changes == ()
    with pytest.raises(ValidationError):
        EasyLanguageResponse.model_validate({"dictionary_terms": []})
    with pytest.raises(ValidationError):
        EasyLanguageResponse.model_validate({"changes": [], "dictionary_terms": []})
    with pytest.raises(ValidationError):
        ProposedChange.model_validate({**change.model_dump(), "reason": "쉬운 말"})


@pytest.mark.parametrize(
    "alteration",
    ["hash", "original", "easy", "offset", "context", "order", "timestamp", "attempts"],
)
def test_stored_result_revalidates_hash_offsets_context_and_reconstruction(alteration: str) -> None:
    result = run_response(
        "나이 산정 기준\n서류 검토 안내",
        response_json(
            proposal("산정", "계산", "나이 산정 기준"),
            proposal("검토", "살펴보기", "서류 검토 안내"),
        ),
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
    payload = run_response("소명 안내", response_json()).model_dump(mode="json")
    payload["prompt_version"] = "easy-language-v4"
    historical = EasyLanguageResult.model_validate(payload)
    assert historical.prompt_version == "easy-language-v4"
    assert "dictionary_terms" not in historical.model_dump(mode="json")


def test_stored_result_rejects_removed_dictionary_field() -> None:
    payload = run_response("소명 안내", response_json()).model_dump(mode="json")
    payload["dictionary_terms"] = ["소명"]
    with pytest.raises(ValidationError):
        EasyLanguageResult.model_validate(payload)


def test_custom_model_is_kept_with_result() -> None:
    model = "custom-gemini-model"
    calls = []

    def request(**kwargs: str) -> str:
        calls.append(kwargs)
        return response_json()

    result = simplify_notice(
        NoticeGlossaryInput(text="공지"), api_key=API_KEY, model=model, request=request
    )
    assert calls[0]["model"] == result.model == model
