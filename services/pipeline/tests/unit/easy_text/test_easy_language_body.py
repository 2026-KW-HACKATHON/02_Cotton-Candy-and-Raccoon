"""Body-scoped proposals retain titles even when excerpts repeat across the boundary."""

import json

import pytest
from pydantic import ValidationError
from support.easy_rewrite import rewrite, sentence

from pipeline.glossary.easy_language import (
    EasyLanguageResult,
    flatten_easy_rewrite,
    simplify_notice,
)
from pipeline.glossary.source import StoredNoticeInput


def _source(title: str, body: str) -> StoredNoticeInput:
    return StoredNoticeInput(
        notice_id=22,
        notice_revision="a" * 64,
        title=title,
        text=title + "\n" + body,
        body_text_present=True,
    )


def test_same_context_in_title_and_body_rewrites_only_the_body():
    source = _source("익일 안내", "익일 안내")
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        return json.dumps(
            {
                "rewrite": rewrite("익일 안내"),
                "dictionary_candidates": [
                    {"original": "익일", "query_word": "익일", "context": "익일 안내"},
                ],
            },
            ensure_ascii=False,
        )

    result = simplify_notice(source, api_key="fake", request=request)

    assert calls[0]["notice_text"] == "익일 안내"
    assert result.easy_text == "익일 안내\n" + flatten_easy_rewrite(result.easy_result)
    assert result.original_title == "익일 안내"
    assert result.dictionary_candidates[0].start == len(source.title) + 1
    payload = result.model_dump(mode="json")
    payload["dictionary_candidates"][0].update(start=0, end=2)
    with pytest.raises(ValidationError):
        EasyLanguageResult.model_validate(payload)


@pytest.mark.parametrize("field_name", ["evidence", "dictionary_candidates"])
def test_retry_keeps_body_only_after_a_quote_copied_from_the_title(field_name):
    source = _source("익일 안내", "지참하세요.")
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        first = len(calls) == 1
        quote = "익일 안내" if first and field_name == "evidence" else "지참하세요."
        payload = rewrite("지참하세요.", sections=[{
            "heading": "무엇을 하나요?", "style": "paragraph",
            "sentences": [sentence("챙겨 오세요.", quote)],
        }])
        candidates = []
        if field_name == "dictionary_candidates":
            candidates.append(
                {"original": "익일", "query_word": "익일", "context": "익일 안내"}
                if first
                else {"original": "지참하세요", "query_word": "지참하다", "context": "지참하세요."}
            )
        return json.dumps(
            {"rewrite": payload, "dictionary_candidates": candidates}, ensure_ascii=False
        )

    result = simplify_notice(source, api_key="fake", request=request)

    assert [item["notice_text"] for item in calls] == ["지참하세요.", "지참하세요."]
    assert result.attempt_count == 2
    assert result.easy_text.startswith("익일 안내\n")
