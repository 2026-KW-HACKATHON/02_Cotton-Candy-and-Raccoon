"""Body-scoped proposals retain titles even when excerpts repeat across the boundary."""

import json

from pipeline.glossary.easy_language import simplify_notice
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
                ]
            },
            ensure_ascii=False,
        )

    result = simplify_notice(source, api_key="fake", request=request)

    assert calls[0]["notice_text"] == "익일 안내"
    assert result.easy_text == "익일 안내\n다음 날 안내"
    assert result.original_title == "익일 안내"
    assert result.changes[0].start == len(source.title) + 1


def test_retry_keeps_body_only_after_a_proposal_copied_from_the_title():
    source = _source("익일 안내", "지참하세요.")
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        change = (
            {"original": "익일", "replacement": "다음 날", "context": "익일 안내"}
            if len(calls) == 1
            else {"original": "지참하세요", "replacement": "가져오세요", "context": "지참하세요."}
        )
        return json.dumps({"changes": [change]}, ensure_ascii=False)

    result = simplify_notice(source, api_key="fake", request=request)

    assert [item["notice_text"] for item in calls] == ["지참하세요.", "지참하세요."]
    assert result.attempt_count == 2
    assert result.easy_text == "익일 안내\n가져오세요."
