"""Original-to-easy-text span boundaries for notice dictionary links."""

import json
import unicodedata

import pytest
from support.easy_text_storage import _NOW

from pipeline.glossary.dictionary import DictionaryQuery
from pipeline.glossary.easy_language import simplify_notice
from pipeline.glossary.notice_dictionary import map_dictionary_candidates
from pipeline.glossary.source import NoticeGlossaryInput


def _result(text, changes, candidates):
    payload = {
        "changes": [
            {"original": original, "replacement": replacement, "context": text}
            for original, replacement in changes
        ],
        "dictionary_candidates": [
            {"original": original, "query_word": query, "context": context}
            for original, query, context in candidates
        ],
    }
    return simplify_notice(
        NoticeGlossaryInput(text=text), api_key="test-key",
        request=lambda **_: json.dumps(payload, ensure_ascii=False), clock=lambda: _NOW,
    )


@pytest.mark.parametrize(
    ("changed", "candidate", "expected", "status"),
    [
        ("이행 의무", "이행 의무", "해야 할 일", "replaced"),
        ("의무", "이행 의무 안내", "이행 해야 할 일 안내", "replaced"),
        ("이행 의무", "의무 안내", None, "original_only"),
        ("의무 안내", "이행 의무", None, "original_only"),
        ("이행 의무 안내", "의무", None, "original_only"),
    ],
    ids=["exact", "contained", "crosses-start", "crosses-end", "crosses-both"],
)
def test_replacement_boundaries_map_only_unambiguous_easy_text(
    changed, candidate, expected, status,
):
    text = "📌 이행 의무 안내."
    result = _result(text, [(changed, "해야 할 일")], [(candidate, "의무", text)])

    mapped, = map_dictionary_candidates(result)

    assert {key: mapped[key] for key in ("original", "query_word", "context", "start", "end")} == {
        "original": candidate, "query_word": "의무", "context": text,
        "start": text.index(candidate), "end": text.index(candidate) + len(candidate),
    }
    assert mapped["mapping_status"] == status
    assert mapped["easy_expression"] == expected
    if expected is None:
        assert mapped["easy_start"] is None and mapped["easy_end"] is None
    else:
        assert mapped["easy_start"] == result.easy_text.index(expected)
        assert mapped["easy_end"] == result.easy_text.index(expected) + len(expected)


def test_multiple_length_changes_use_code_points_and_keep_repeated_query_occurrences():
    text = "👩‍💻 이행 의무 안내. 지참 준비. 담당자에게 문의. 담당자 확인."
    result = _result(
        text,
        [("이행", "해야 할 일"), ("의무 안내", "소개"), ("지참", "챙기기")],
        [
            ("이행 의무 안내", "의무", "이행 의무 안내."),
            ("담당자에게", "담당자", "담당자에게 문의."),
            ("담당자", unicodedata.normalize("NFD", "담당자"), "담당자 확인."),
        ],
    )

    mapped = map_dictionary_candidates(result)

    assert result.easy_text == "👩‍💻 해야 할 일 소개. 챙기기 준비. 담당자에게 문의. 담당자 확인."
    expected_expressions = ("해야 할 일 소개", "담당자에게", "담당자")
    expected_starts = (4, 23, 33)
    assert [item["easy_start"] for item in mapped] == list(expected_starts)
    for item, expression, start in zip(mapped, expected_expressions, expected_starts, strict=True):
        assert item["easy_end"] == start + len(expression)
        assert item["easy_expression"] == expression
        assert result.original_text[item["start"]:item["end"]] == item["original"]
    assert [item["mapping_status"] for item in mapped] == ["replaced", "unchanged", "unchanged"]
    assert mapped[1]["cache_key"] == mapped[2]["cache_key"] == DictionaryQuery("담당자").cache_key
    assert mapped[1]["start"] != mapped[2]["start"]


def test_unknown_candidate_extraction_cannot_be_reported_as_empty():
    result = _result("서류 안내.", [], [])
    assert map_dictionary_candidates(result) == []

    with pytest.raises(ValueError, match="사전 후보를 추출한 결과"):
        map_dictionary_candidates(result.model_copy(update={"dictionary_candidates": None}))
