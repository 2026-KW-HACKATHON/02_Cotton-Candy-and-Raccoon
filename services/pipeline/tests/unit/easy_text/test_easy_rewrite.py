"""Question-section rewrite: schema, local validation, flattening and stored results (#85)."""

import pytest
from pydantic import ValidationError
from support.easy_rewrite import legacy_result, rewrite_response, sentence

from pipeline.glossary.easy_language import (
    MAX_COPY_RATIO,
    MAX_EVIDENCE_CHARACTERS,
    MIN_EVIDENCE_CHARACTERS,
    REWRITE_CHECK_MIN_BODY_CHARACTERS,
    EasyLanguageResult,
    EasyLanguageValidationError,
    EasyRewrite,
    ReviewedRewrite,
    flatten_easy_rewrite,
    simplify_notice,
    validate_easy_rewrite,
)
from pipeline.glossary.source import NoticeGlossaryInput, StoredNoticeInput, source_hash

BODY = (
    "■ 신청대상 : 노원구 거주 65세 이상 어르신\n"
    "■ 신청기간 : 2026. 10. 1.(목) 09:00 ~ 10. 31.(토) 18:00\n"
    "■ 신청방법 : 동 주민센터 방문 접수\n"
    "■ 제출서류 : 신분증, 신청서 1부\n"
    "※ '청소년 역사·평화·환경 캠프' 참가자는 제외합니다."
)


def section(heading: str, *sentences: dict[str, object], style: str = "paragraph") -> dict:
    return {"heading": heading, "style": style, "sentences": list(sentences)}


def good_rewrite(**overrides) -> dict[str, object]:
    values = {
        "headline": "어르신 신청 안내예요.",
        "intro": [sentence("노원구 어르신이 신청할 수 있어요.", "노원구 거주 65세 이상 어르신")],
        "sections": [
            section(
                "누가 신청하나요?",
                sentence(
                    "65세 이상 노원구 주민이에요.", "■ 신청대상 : 노원구 거주 65세 이상 어르신"
                ),
            ),
            section(
                "어떻게 신청하나요?",
                sentence("신분증과 신청서 1부를 챙겨요.", "■ 제출서류 : 신분증, 신청서 1부"),
                sentence("동 주민센터에 가서 내요.", "■ 신청방법 : 동 주민센터 방문 접수"),
                style="steps",
            ),
            section(
                "언제까지 하나요?",
                sentence(
                    "10월 31일 18시까지예요.",
                    "■ 신청기간 : 2026. 10. 1.(목) 09:00 ~ 10. 31.(토) 18:00",
                ),
            ),
        ],
        "attachment_hint": None,
    }
    return values | overrides


def parse(payload: dict[str, object]) -> EasyRewrite:
    return EasyRewrite.model_validate(payload)


# --- Schema ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"sections": []},
        {"sections": [section("누가 하나요?", sentence("문장이에요.", "노원구"))] * 7},
        {"intro": [sentence("문장이에요.", "노원구")] * 4},
        {"headline": ""},
        {"headline": "두 줄\n제목"},
        {"headline": "가" * 81},
        {"attachment_hint": ""},
        {"sections": [section("누가 신청하나요", sentence("문장이에요.", "노원구"))]},
        {"sections": [section("가" * 25 + "?", sentence("문장이에요.", "노원구"))]},
        {"sections": [section("누가 하나요?")]},
        {"sections": [section("누가 하나요?", *[sentence("문장이에요.", "노원구")] * 9)]},
        {"sections": [section("누가 하나요?", sentence("문장이에요."))]},
        {"sections": [section("누가 하나요?", sentence("가" * 81, "노원구"))]},
        {"sections": [section("누가 하나요?", sentence("문장\n이에요.", "노원구"))]},
        {"sections": [section(
            "누가 하나요?", sentence("문장이에요.", "노" * (MAX_EVIDENCE_CHARACTERS + 1))
        )]},
        {"sections": [{**section("누가 하나요?", sentence("문장이에요.", "노원구")),
                       "style": "list"}]},
        {"extra": "field"},
    ],
)
def test_schema_rejects_out_of_range_structure(change) -> None:
    with pytest.raises(ValidationError):
        parse(good_rewrite(**change))


def test_schema_accepts_limits_and_empty_intro() -> None:
    parsed = parse(good_rewrite(
        intro=[],
        headline="가" * 80,
        sections=[section("가" * 24 + "?", *[sentence("가" * 80, "노원구")] * 8)] * 6,
    ))
    assert len(parsed.sections) == 6 and parsed.intro == ()


# --- Local validation ------------------------------------------------------------


def test_good_rewrite_passes_validation() -> None:
    validate_easy_rewrite(parse(good_rewrite()), BODY)


@pytest.mark.parametrize(
    "evidence",
    ["■ 신청대상 : 노원구 거주 65세 이상 어르신 ", "■ 신청대상: 노원구", "신청 대상"],
)
def test_evidence_must_be_an_exact_body_quote(evidence) -> None:
    payload = good_rewrite(sections=[section("누가 하나요?", sentence("어르신이에요.", evidence))])
    with pytest.raises(ValueError, match="근거 문장"):
        validate_easy_rewrite(parse(payload), BODY)


@pytest.mark.parametrize(
    ("text", "evidence"),
    [("신청은 10월 1일부터예요.", ("1", "10")), ("누구나 무료로 참여해요.", ("노원구",))],
)
def test_evidence_shorter_than_the_minimum_attests_nothing(text, evidence) -> None:
    assert all(quote in BODY for quote in evidence)
    payload = good_rewrite(sections=[section("무엇인가요?", sentence(text, *evidence))])
    with pytest.raises(ValueError, match="너무 짧습니다"):
        validate_easy_rewrite(parse(payload), BODY)


def test_a_whole_body_shorter_than_the_minimum_is_its_own_evidence() -> None:
    body = "첨부참조"
    assert len(body) < MIN_EVIDENCE_CHARACTERS
    payload = good_rewrite(intro=[], sections=[section(
        "무엇을 보나요?", sentence("첨부파일을 봐요.", body),
    )])
    validate_easy_rewrite(parse(payload), body)


def test_title_is_not_evidence_for_a_body_rewrite() -> None:
    payload = good_rewrite(
        sections=[section("무엇인가요?", sentence("신청 안내예요.", "어르신 신청"))]
    )
    with pytest.raises(ValueError, match="근거 문장"):
        validate_easy_rewrite(parse(payload), "본문에는 없는 말")


@pytest.mark.parametrize(
    ("text", "valid"),
    [
        ("10월 31일 18시까지예요.", True),
        ("10월 1일 9시부터예요.", True),
        ("10월 30일까지예요.", False),
        ("2027년 10월 31일까지예요.", False),
    ],
)
def test_sentence_numbers_must_appear_in_its_own_evidence(text, valid) -> None:
    payload = good_rewrite(sections=[section(
        "언제까지 하나요?",
        sentence(text, "■ 신청기간 : 2026. 10. 1.(목) 09:00 ~ 10. 31.(토) 18:00"),
    )])
    if valid:
        validate_easy_rewrite(parse(payload), BODY)
    else:
        with pytest.raises(ValueError, match="숫자"):
            validate_easy_rewrite(parse(payload), BODY)


def test_number_from_another_sentence_evidence_does_not_count() -> None:
    payload = good_rewrite(sections=[section(
        "무엇을 내나요?",
        sentence("신청서 1부를 내요.", "■ 제출서류 : 신분증, 신청서 1부"),
        sentence("65세 이상이면 돼요.", "■ 신청방법 : 동 주민센터 방문 접수"),
    )])
    with pytest.raises(ValueError, match="숫자"):
        validate_easy_rewrite(parse(payload), BODY)


@pytest.mark.parametrize("field", ["headline", "heading", "attachment_hint"])
def test_numbers_outside_sentences_must_appear_in_the_body(field) -> None:
    payload = good_rewrite()
    if field == "headline":
        payload["headline"] = "99명 모집 안내예요."
    elif field == "heading":
        payload["sections"][0]["heading"] = "99명 누가 하나요?"
    else:
        payload["attachment_hint"] = "첨부 99쪽을 확인해요."
    with pytest.raises(ValueError, match="숫자"):
        validate_easy_rewrite(parse(payload), BODY)
    payload = good_rewrite(headline="65세 이상 어르신 안내예요.")
    validate_easy_rewrite(parse(payload), BODY)


@pytest.mark.parametrize("symbol", ["「캠프」", "역사·평화", "*주의", "※ 주의"])
def test_forbidden_symbols_are_rejected_in_every_written_text(symbol) -> None:
    for payload in (
        good_rewrite(headline=f"{symbol} 안내예요."),
        good_rewrite(attachment_hint=f"{symbol} 첨부를 봐요."),
        good_rewrite(intro=[sentence(f"{symbol} 안내예요.", "노원구 거주 65세 이상 어르신")]),
    ):
        with pytest.raises(ValueError, match="기호"):
            validate_easy_rewrite(parse(payload), BODY)


@pytest.mark.parametrize(
    "name", ["'청소년 역사·평화·환경 캠프'", "‘청소년 역사·평화·환경 캠프’"]
)
def test_official_name_in_single_quotes_may_keep_its_symbols(name) -> None:
    payload = good_rewrite(sections=[section(
        "누가 빠지나요?",
        sentence(f"{name} 참가자는 빠져요.", "※ '청소년 역사·평화·환경 캠프' 참가자는 제외합니다."),
    )])
    validate_easy_rewrite(parse(payload), BODY)
    unquoted = good_rewrite(sections=[section(
        "누가 빠지나요?",
        sentence("청소년 역사·평화·환경 캠프 참가자는 빠져요.",
                 "※ '청소년 역사·평화·환경 캠프' 참가자는 제외합니다."),
    )])
    with pytest.raises(ValueError, match="기호"):
        validate_easy_rewrite(parse(unquoted), BODY)


def _long_body() -> str:
    lines = [
        f"제{number}조 신청 대상은 노원구 주민이며 서류를 내야 합니다." for number in range(1, 13)
    ]
    body = "\n".join(lines)
    assert len(body) >= REWRITE_CHECK_MIN_BODY_CHARACTERS
    return body


def test_copying_a_long_body_is_rejected() -> None:
    body = _long_body()
    copied = [sentence(line, line) for line in body.splitlines()[:8]]
    rest = [sentence(line, line) for line in body.splitlines()[8:]]
    payload = good_rewrite(intro=[], sections=[section("무엇인가요?", *copied),
                                                section("또 무엇인가요?", *rest)])
    with pytest.raises(ValueError, match="그대로"):
        validate_easy_rewrite(parse(payload), body)
    assert MAX_COPY_RATIO == 0.8


def test_rewrite_longer_than_a_long_body_is_rejected() -> None:
    body = _long_body()
    line = body.splitlines()[0]
    filler = [sentence("노원구 주민이면 서류를 준비해서 신청할 수 있어요. 꼭 확인해요.", line)] * 8
    headings = ["누가 하나요?", "무엇을 하나요?", "어디서 하나요?", "언제 하나요?",
                "어떻게 하나요?", "왜 하나요?"]
    payload = good_rewrite(intro=[], sections=[section(h, *filler) for h in headings])
    with pytest.raises(ValueError, match="원문보다"):
        validate_easy_rewrite(parse(payload), body)


def _large_body() -> str:
    lines = [
        f"{number}번 항목은 노원구 주민센터 복지팀 창구에서 방문 접수로 신청을 받습니다."
        for number in range(1, 201)
    ]
    return "\n".join(lines)


def test_copying_sentences_from_a_large_body_is_rejected_despite_a_low_ratio() -> None:
    # 48 copied lines are a small share of the body, so the whole-text ratio stays low.
    body = _large_body()
    headings = ["누가 하나요?", "무엇을 하나요?", "어디서 하나요?", "언제 하나요?",
                "어떻게 하나요?", "왜 하나요?"]
    lines = iter(body.splitlines())
    payload = good_rewrite(intro=[], sections=[
        section(h, *[sentence(line, line) for line, _ in zip(lines, range(8), strict=False)])
        for h in headings
    ])
    with pytest.raises(ValueError, match="그대로"):
        validate_easy_rewrite(parse(payload), body)


@pytest.mark.parametrize(
    ("text", "evidence"),
    [
        # Verbatim body text, attested by a different short quote.
        (
            "항목은 노원구 주민센터 복지팀 창구에서 방문 접수로 신청을 받습니다.",
            "주민센터 복지팀 창구",
        ),
        # A one-word change from its own evidence.
        (
            "2번 항목은 노원구 주민센터 복지팀 창구에서 방문 접수로 신청을 받아요.",
            "2번 항목은 노원구 주민센터 복지팀 창구에서 방문 접수로 신청을 받습니다.",
        ),
    ],
)
def test_one_copied_sentence_rejects_the_whole_rewrite(text, evidence) -> None:
    body = _large_body()
    payload = good_rewrite(intro=[], sections=[section(
        "어디서 신청하나요?",
        sentence("주민센터 복지팀에 가서 신청해요.", body.splitlines()[0]),
        sentence(text, evidence),
    )])
    with pytest.raises(ValueError, match="그대로"):
        validate_easy_rewrite(parse(payload), body)


def test_rephrased_sentences_of_a_large_body_pass() -> None:
    body = _large_body()
    payload = good_rewrite(intro=[], sections=[section(
        "어디서 신청하나요?",
        sentence("1번은 주민센터 복지팀에 가서 신청해요.", body.splitlines()[0]),
    )])
    validate_easy_rewrite(parse(payload), body)


def test_short_body_skips_copy_and_length_checks() -> None:
    body = "노원구 주민은 서류를 내야 합니다."
    assert len(body) < REWRITE_CHECK_MIN_BODY_CHARACTERS
    payload = good_rewrite(intro=[], sections=[section(
        "무엇을 하나요?", sentence(body, body), sentence("서류를 꼭 내야 해요.", body),
    )])
    validate_easy_rewrite(parse(payload), body)
    result = simplify_notice(
        StoredNoticeInput(
            notice_id=1, notice_revision="a" * 64, text="안내\n" + body, title="안내",
            body_text_present=True,
        ),
        api_key="fake",
        request=lambda **kwargs: rewrite_response(body, rewrite_payload=payload),
    )
    assert result.easy_result is not None


# --- Flatten ----------------------------------------------------------------------


def test_flatten_writes_headline_intro_and_numbered_steps() -> None:
    flat = flatten_easy_rewrite(parse(good_rewrite(attachment_hint="자세한 내용은 첨부를 봐요.")))
    assert flat == (
        "어르신 신청 안내예요.\n"
        "노원구 어르신이 신청할 수 있어요.\n"
        "\n"
        "누가 신청하나요?\n"
        "65세 이상 노원구 주민이에요.\n"
        "\n"
        "어떻게 신청하나요?\n"
        "1. 신분증과 신청서 1부를 챙겨요.\n"
        "2. 동 주민센터에 가서 내요.\n"
        "\n"
        "언제까지 하나요?\n"
        "10월 31일 18시까지예요.\n"
        "\n"
        "자세한 내용은 첨부를 봐요."
    )


def test_flatten_without_intro_starts_sections_after_a_blank_line() -> None:
    flat = flatten_easy_rewrite(parse(good_rewrite(intro=[])))
    assert flat.startswith("어르신 신청 안내예요.\n\n누가 신청하나요?\n")
    assert not flat.endswith("\n")


# --- Two stored result formats ------------------------------------------------------


def _stored_rewrite() -> EasyLanguageResult:
    # Historical v1 rows must remain readable under their original validator.
    from datetime import UTC, datetime

    original = "어르신 신청 안내\n" + BODY
    result = parse(good_rewrite())
    return EasyLanguageResult(
        notice_id=7, notice_revision="b" * 64,
        original_text=original, original_title="어르신 신청 안내",
        easy_text="어르신 신청 안내\n" + flatten_easy_rewrite(result),
        source_hash=source_hash(original), model="gemini-2.5-flash",
        prompt_version="easy-rewrite-v1", generated_at=datetime(2026, 10, 9, tzinfo=UTC),
        changes=(), dictionary_candidates=(), attempt_count=1, easy_result=result,
    )


def test_rewrite_result_easy_text_is_title_plus_flattened_rewrite() -> None:
    result = _stored_rewrite()
    assert result.changes == ()
    assert result.easy_text == "어르신 신청 안내\n" + flatten_easy_rewrite(result.easy_result)
    assert EasyLanguageResult.model_validate(result.model_dump(mode="json")) == result


@pytest.mark.parametrize("alteration", ["easy_text", "changes", "evidence", "number", "symbol"])
def test_stored_rewrite_is_revalidated(alteration) -> None:
    payload = _stored_rewrite().model_dump(mode="json")
    first = payload["easy_result"]["sections"][0]["sentences"][0]
    if alteration == "easy_text":
        payload["easy_text"] += "\n추가"
    elif alteration == "changes":
        payload["changes"] = [{
            "start": 0, "end": 2, "original": "어르", "replacement": "노인", "context": "어르신",
        }]
    elif alteration == "evidence":
        first["evidence"] = ["원문에 없는 구절"]
    elif alteration == "number":
        first["text"] = "70세 이상 노원구 주민이에요."
    else:
        first["text"] = "※ 노원구 주민이에요."
    if alteration in {"evidence", "number", "symbol"}:
        payload["easy_text"] = "어르신 신청 안내\n" + flatten_easy_rewrite(
            ReviewedRewrite.model_validate(payload["easy_result"])
        )
    with pytest.raises(ValidationError):
        EasyLanguageResult.model_validate(payload)


def test_legacy_and_rewrite_results_validate_side_by_side() -> None:
    legacy = legacy_result(
        "나이 산정 기준", {"original": "산정", "replacement": "계산", "context": "나이 산정 기준"}
    )
    rewritten = _stored_rewrite()
    assert legacy.easy_result is None and legacy.changes
    assert rewritten.easy_result is not None and not rewritten.changes
    for result in (legacy, rewritten):
        assert EasyLanguageResult.model_validate_json(result.model_dump_json()) == result


def test_failed_validation_on_both_attempts_is_not_stored_as_success() -> None:
    bad = good_rewrite(headline="")
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        return rewrite_response(BODY, rewrite_payload=bad)

    with pytest.raises(EasyLanguageValidationError):
        simplify_notice(NoticeGlossaryInput(text=BODY), api_key="fake", request=request)
    assert len(calls) == 2
