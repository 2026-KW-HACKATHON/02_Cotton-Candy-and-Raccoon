"""Body-scoped proposals retain titles even when excerpts repeat across the boundary."""

import json

import pytest
from pydantic import ValidationError

from pipeline.glossary.easy_language import EasyLanguageResult, simplify_notice
from pipeline.glossary.source import StoredNoticeInput


def _source(title: str, body: str) -> StoredNoticeInput:
    return StoredNoticeInput(
        notice_id=22,
        notice_revision="a" * 64,
        title=title,
        text=title + "\n" + body,
        body_text_present=True,
    )


def test_same_context_in_title_and_body_changes_only_the_body():
    source = _source("익일 안내", "익일 안내")
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        return json.dumps(
            {
                "changes": [
                    {"original": "익일", "replacement": "다음 날", "context": "익일 안내"},
                ],
                "dictionary_candidates": [
                    {"original": "익일", "query_word": "익일", "context": "익일 안내"},
                ],
            },
            ensure_ascii=False,
        )

    result = simplify_notice(source, api_key="fake", request=request)

    assert calls[0]["notice_text"] == "익일 안내"
    assert result.easy_text == "익일 안내\n다음 날 안내"
    assert result.original_title == "익일 안내"
    assert result.changes[0].start == len(source.title) + 1
    assert result.dictionary_candidates[0].start == len(source.title) + 1
    payload = result.model_dump(mode="json")
    payload["dictionary_candidates"][0].update(start=0, end=2)
    with pytest.raises(ValidationError):
        EasyLanguageResult.model_validate(payload)


@pytest.mark.parametrize("field_name", ["changes", "dictionary_candidates"])
def test_retry_keeps_body_only_after_a_proposal_copied_from_the_title(field_name):
    source = _source("익일 안내", "지참하세요.")
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        change = (
            {"original": "익일", "replacement": "다음 날", "context": "익일 안내"}
            if len(calls) == 1
            else {"original": "지참하세요", "replacement": "가져오세요", "context": "지참하세요."}
        )
        response = {"changes": [], "dictionary_candidates": []}
        if field_name == "dictionary_candidates":
            del change["replacement"]
            change["query_word"] = "익일" if len(calls) == 1 else "지참하다"
        response[field_name].append(change)
        return json.dumps(response, ensure_ascii=False)

    result = simplify_notice(source, api_key="fake", request=request)

    assert [item["notice_text"] for item in calls] == ["지참하세요.", "지참하세요."]
    assert result.attempt_count == 2
    assert result.easy_text == (
        "익일 안내\n가져오세요." if field_name == "changes" else source.text
    )
