"""#106 regression cases: complete bodies, literal facts, and short plain sentences."""

import pytest
from support.easy_rewrite import rewrite, rewrite_response, sentence

from pipeline.glossary.easy_language import EasyRewrite, simplify_notice
from pipeline.glossary.rewrite_validation import validate_minimal_rewrite
from pipeline.glossary.source import NoticeGlossaryInput, StoredNoticeInput

BODY = (
    "대상: 노원구 거주 65세 이상 주민\n"
    "신청: 2026년 10월 3일 09:00부터 10월 31일 18:00까지\n"
    "구비서류: 신분증 및 신청서 1부 지참 필수\n"
    "비용: 참가비 30,000원\n"
    "단, 기존 수혜자는 신청할 수 없습니다.\n"
    "문의: 02-1234-5678\n"
    "세부 서식은 첨부 파일 참조"
)
TEXTS = [
    "노원구에 사는 65세 이상 주민이 신청할 수 있어요.",
    "2026년 10월 3일 09:00부터 10월 31일 18:00까지 신청하세요.",
    "신분증과 신청서 1부를 꼭 가져오세요.",
    "참가비는 30,000원이에요.",
    "이미 지원받은 사람은 신청할 수 없어요.",
    "궁금한 점은 02-1234-5678로 물어보세요.",
    "자세한 서식은 첨부 파일에서 확인하세요.",
]


def complete_payload():
    return rewrite(BODY, sections=[{
        "heading": "어떻게 신청하나요?", "style": "paragraph",
        "sentences": [
            sentence(text, line) for text, line in zip(TEXTS, BODY.splitlines(), strict=True)
        ],
    }])


def test_rewrites_all_fields_including_negative_exception_and_attachment_scope():
    result = simplify_notice(
        StoredNoticeInput(notice_id=1, notice_revision="a" * 64,
                          title="신청 안내", text="신청 안내\n" + BODY, body_text_present=True),
        api_key="test-key",
        request=lambda **kwargs: rewrite_response(BODY, rewrite_payload=complete_payload()),
    )
    assert result.original_text == "신청 안내\n" + BODY
    assert result.prompt_version == "easy-rewrite-v3"
    assert result.attachment_content_included is False
    assert result.changes == ()
    assert all(text in result.easy_text for text in TEXTS)
    assert len(result.easy_text) > len(BODY)  # readability must not force lossy compression


@pytest.mark.parametrize("index", range(7))
def test_source_coverage_is_not_a_hard_gate(index):
    payload = complete_payload()
    del payload["sections"][0]["sentences"][index]
    validate_minimal_rewrite(EasyRewrite.model_validate(payload), BODY)


@pytest.mark.parametrize(("index", "old", "new"), [
    (1, "09:00", "19:00"),
    (3, "30,000원", "3,000원"),
    (5, "02-1234-5678", "02-1234-5679"),
])
def test_rejects_new_numbers_in_time_money_and_contacts(index, old, new):
    payload = complete_payload()
    item = payload["sections"][0]["sentences"][index]
    item["text"] = item["text"].replace(old, new)
    with pytest.raises(ValueError, match="원문에 없는 숫자"):
        validate_minimal_rewrite(EasyRewrite.model_validate(payload), BODY)


def test_omission_detection_is_not_a_hard_gate():
    payload = complete_payload()
    payload["sections"][0]["sentences"][1]["text"] = "기간 안에 신청하세요."
    validate_minimal_rewrite(EasyRewrite.model_validate(payload), BODY)


def test_cannot_invent_attachment_contents():
    payload = complete_payload()
    payload["attachment_hint"] = "첨부 파일도 모두 읽었어요."
    with pytest.raises(ValueError, match="첨부 안내"):
        validate_minimal_rewrite(EasyRewrite.model_validate(payload), BODY)


def test_preserves_year_line_break_and_wolgye_dong_without_treating_name_as_month():
    body = "2026\n월계1동 주민 안내"
    validate_minimal_rewrite(EasyRewrite.model_validate(rewrite(body)), body)


@pytest.mark.parametrize(("original", "hint"), [
    ("수혜자", "조사·어미를 떼지 마세요"),
    ("수혜자는:", "콜론, 쉼표, 마침표를 넣지 마세요"),
])
def test_live_model_candidate_mistakes_do_not_discard_rewrite(original, hint):
    body = "기존 수혜자는 신청할 수 없습니다."
    calls = []

    def request(**kwargs):
        calls.append(kwargs["prompt"])
        return rewrite_response(body, candidates=[{
            "original": original if len(calls) == 1 else "수혜자는",
            "query_word": "수혜자", "context": body,
        }])

    result = simplify_notice(NoticeGlossaryInput(text=body), api_key="test-key", request=request)
    assert result.attempt_count == len(calls) == 1
    assert result.dictionary_candidates == ()
    assert result.easy_result is not None


@pytest.mark.parametrize(("source_day", "output_day"), [
    ("(목)", "목요일"), ("목요일", "(목)"), ("（월）", "월요일"),
])
def test_equivalent_weekday_notation_preserves_schedule(source_day, output_day):
    body = f"일정: 2026. 10. 22.{source_day} 14:00 ~ 16:00"
    payload = rewrite(body, sections=[{
        "heading": "언제 하나요?", "style": "paragraph",
        "sentences": [sentence(f"2026. 10. 22.{output_day} 14:00 ~ 16:00에 진행해요.", body)],
    }])
    validate_minimal_rewrite(EasyRewrite.model_validate(payload), body)


@pytest.mark.parametrize("day", ["금요일", "(금)", ""])
def test_weekday_semantics_are_not_claimed_by_minimal_validation(day):
    body = "2026. 10. 22.(목) 14:00 ~ 16:00"
    payload = rewrite(body, sections=[{
        "heading": "언제 하나요?", "style": "paragraph",
        "sentences": [sentence(body.replace("(목)", day), body)],
    }])
    validate_minimal_rewrite(EasyRewrite.model_validate(payload), body)


@pytest.mark.parametrize("suffix", ["입니다.", "?extra=1", "/other"])
def test_url_changes_are_not_relaxed_to_allow_weekday_normalization(suffix):
    body = "신청 링크: https://forms.gle/example123"
    payload = rewrite(body, sections=[{
        "heading": "어떻게 신청하나요?", "style": "paragraph",
        "sentences": [sentence("https://forms.gle/example123" + suffix, body)],
    }])
    with pytest.raises(ValueError):
        validate_minimal_rewrite(EasyRewrite.model_validate(payload), body)


def test_standalone_url_and_its_digits_are_preserved():
    body = "신청 링크: https://forms.gle/example123"
    payload = rewrite(body, sections=[{
        "heading": "어떻게 신청하나요?", "style": "paragraph",
        "sentences": [sentence("아래 링크에서 신청하세요.", body),
                      sentence("https://forms.gle/example123", body)],
    }])
    validate_minimal_rewrite(EasyRewrite.model_validate(payload), body)


def test_real_academy_response_retains_schedule_final_conditions_and_dictionary():
    import json
    from pathlib import Path

    fixture = json.loads((Path(__file__).parents[2] / "fixtures" /
                          "easy_rewrite_real_academy.json").read_text(encoding="utf-8"))
    original = fixture["original_title"] + "\n" + fixture["body"]
    result = simplify_notice(
        StoredNoticeInput(notice_id=1, notice_revision="a" * 64,
                          title=fixture["original_title"], text=original, body_text_present=True),
        api_key="test-key", request=lambda **kwargs: json.dumps(fixture["response"]),
    )
    assert result.attempt_count == 1
    assert "https://forms.gle/cVFgia6JPMs4XR8z7" in result.easy_text.splitlines()
    assert "교육 4회에 모두 참석해야 해요." in result.easy_text
    assert "교육이 끝나기 전에 나가면 안 돼요." in result.easy_text
    assert "선택 실습" in result.easy_text
    assert "기존 참여자와 교육을 끝까지 마친 사람도 참여할 수 있습니다." in result.easy_text
    assert result.dictionary_candidates


@pytest.mark.parametrize(("body", "text"), [
    ("19~39세 신청", "19세부터 39세까지 신청해요."),
    ("2026. 10. 22.(목) 09:00 ~ 16:00", "2026년 10월 22일 목요일 9시부터 16시까지예요."),
    ("14:00 ~ 16:00", "14:00부터 16:00까지예요."),
    ("신청(https://forms.gle/example123)", "https://forms.gle/example123"),
    ("내용 " * 100, "내용을 안내해요."),
])
def test_minimal_validation_allows_readable_formatting_and_long_quotes(body, text):
    payload = rewrite(body, sections=[{
        "heading": "어떤 내용인가요?", "style": "paragraph",
        "sentences": [sentence(text, body)],
    }])
    validate_minimal_rewrite(EasyRewrite.model_validate(payload), body)


def test_fabricated_evidence_is_still_rejected():
    payload = rewrite("안내", sections=[{
        "heading": "어떤 내용인가요?", "style": "paragraph",
        "sentences": [sentence("안내해요.", "없는 근거")],
    }])
    with pytest.raises(ValueError, match="근거 문장"):
        validate_minimal_rewrite(EasyRewrite.model_validate(payload), "안내")
