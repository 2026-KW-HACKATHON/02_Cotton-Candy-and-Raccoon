"""Replay captured real notice evidence without live API or database access."""

import json

import pytest
from support.paths import FIXTURES_DIR

from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_highlights import build_summary_text_highlights
from pipeline.transform.summary_schema import NoticeSummary

FIXTURE = FIXTURES_DIR / "card_text_highlights.json"


@pytest.mark.parametrize("case", [
    "moss_exhibition", "online_english", "library_committee", "library_committee_pdf_only",
])
def test_captured_notice_locations_are_exact_original_ranges(case: str) -> None:
    capture = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"][case]
    summary = NoticeSummary.model_validate(capture["summary"])
    notice = NoticeInput.model_validate(capture["notice_input"])
    before = summary.model_dump()
    highlights = build_summary_text_highlights(summary, notice)
    sources = {source.key: source.text for source in highlights.sources}
    references = []
    for card in (
        highlights.cards.audience, highlights.cards.deadline,
        highlights.cards.action, highlights.cards.notes,
    ):
        references.extend(card.references)
        for span in card.ranges:
            encoded = sources[span.source_key].encode("utf-16-le")
            assert encoded[span.start * 2:span.end * 2].decode("utf-16-le") == span.text
        assert all(len(item.candidates) == 1 for item in card.references
                   if item.status == "matched")
        assert all(item.candidates == [] for item in card.references
                   if item.reference.evidence.source_type != "text")
    assert references
    assert summary.model_dump() == before
    if capture["pdf_only"]:
        assert sources == {}
        assert all(item.status == "file_reference" for item in references)
        assert all(card.ranges == [] for card in (
            highlights.cards.audience, highlights.cards.deadline,
            highlights.cards.action, highlights.cards.notes,
        ))
    if capture["case"] == "moss_exhibition":
        assert any(item.status == "matched" for item in references)
