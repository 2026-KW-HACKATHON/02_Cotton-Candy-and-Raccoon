"""Helpers shared from test_easy_text_cache_cas.py."""

from pipeline.glossary.easy_language import simplify_notice
from support.easy_text_storage import _NOW, _request

__all__ = [
    "_alternative_result",
]


def _alternative_result(source, now=_NOW):
    return simplify_notice(
        source,
        api_key="fake",
        request=lambda **kwargs: _request(**kwargs).replace(
            "공지 내용을 알려 드려요.", "공지 내용을 자세히 알려 드려요."
        ),
        clock=lambda: now,
    )
