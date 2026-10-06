"""Exact source text, conservative term boundaries and stable occurrence offsets."""

import hashlib
import unicodedata

import pytest
from pydantic import ValidationError

from pipeline.glossary.source import (
    LocatedTerm,
    NoticeGlossaryInput,
    SourceOccurrence,
    TermCandidate,
    locate_candidates,
    notice_content_revision,
    source_hash,
)


def test_notice_revision_preserves_exact_raw_title_and_html():
    revision = notice_content_revision("익일", "<p>안내</p>")
    assert len(revision) == 64
    assert revision == notice_content_revision("익일", "<p>안내</p>")
    assert revision != notice_content_revision("익일", "<div>안내</div>")
    assert notice_content_revision("익일", None) != notice_content_revision("익일", "")
    assert notice_content_revision("a", "b,c") != notice_content_revision("a,b", "c")
    assert notice_content_revision("금회", None) != notice_content_revision("금회", None)


@pytest.mark.parametrize("title, body", [(None, None), (True, None), ("익일", 1)])
def test_notice_revision_rejects_invalid_raw_source(title, body):
    with pytest.raises(ValueError):
        notice_content_revision(title, body)


def test_revision_requires_a_notice_id_and_does_not_change_prepared_source_hash():
    revision = notice_content_revision("익일", "<p>안내</p>")
    with pytest.raises(ValidationError):
        NoticeGlossaryInput(text="익일 안내", notice_revision=revision)
    versioned = NoticeGlossaryInput(notice_id=42, text="익일 안내", notice_revision=revision)
    assert source_hash(versioned) == source_hash("익일 안내")


def candidate(surface: str, query: str | None = None) -> TermCandidate:
    return TermCandidate(surface=surface, query=surface if query is None else query)


def test_source_and_hash_preserve_whitespace_unicode_and_notice_id() -> None:
    text = " \r\n📌 금회\t신청  \n"
    source = NoticeGlossaryInput(notice_id=42, text=text)
    assert source.text == text
    assert source_hash(source) == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert source_hash(source) == source_hash(text)
    assert source_hash(source) == source_hash(NoticeGlossaryInput(notice_id=3, text=text))
    assert source_hash(text) != source_hash(text.strip())
    assert source_hash(text) != source_hash(unicodedata.normalize("NFC", text))


def test_repeated_phrase_occurrences_retain_exact_offsets_and_source_slices() -> None:
    text = "📌 경정  청구 안내\r\n경정  청구는 방문 접수. 경정  청구 완료."
    source = NoticeGlossaryInput(text=text)
    term = candidate("경정  청구", " \n경정 청구\t ")
    (result,) = locate_candidates(source, [term])
    assert result.candidate.surface == "경정  청구"
    assert result.candidate.query == "경정 청구"
    starts = [index for index in range(len(text)) if text.startswith(term.surface, index)]
    assert [occurrence.start for occurrence in result.occurrences] == starts
    for occurrence in result.occurrences:
        assert text[occurrence.start : occurrence.end] == occurrence.text == term.surface
        assert text[occurrence.context_start : occurrence.context_end] == occurrence.context
    assert result.occurrences[0].start == 2  # The emoji is one Python code point.
    assert source.text == text


def test_context_is_a_bounded_original_slice() -> None:
    text = "앞" * 110 + "\n익일\t" + "뒤" * 110
    (result,) = locate_candidates(NoticeGlossaryInput(text=text), [candidate("익일")])
    (occurrence,) = result.occurrences
    assert (occurrence.start, occurrence.end) == (111, 113)
    assert (occurrence.context_start, occurrence.context_end) == (31, 193)
    assert occurrence.context == text[31:193]
    assert len(occurrence.context) == 162


def test_context_clips_at_source_edges() -> None:
    text = "익일 접수 후 익일"
    (result,) = locate_candidates(NoticeGlossaryInput(text=text), [candidate("익일")])
    assert len(result.occurrences) == 2
    assert all(occurrence.context_start == 0 for occurrence in result.occurrences)
    assert all(occurrence.context_end == len(text) for occurrence in result.occurrences)
    assert all(occurrence.context == text for occurrence in result.occurrences)


def test_overlapping_repeated_phrases_keep_each_original_interval() -> None:
    source = NoticeGlossaryInput(text="a a a")
    (result,) = locate_candidates(source, [candidate("a a")])
    assert [(occurrence.start, occurrence.end) for occurrence in result.occurrences] == [
        (0, 3),
        (2, 5),
    ]
    assert all(occurrence.text == "a a" for occurrence in result.occurrences)


def test_nfc_query_does_not_change_decomposed_source_surface_or_offsets() -> None:
    decomposed = unicodedata.normalize("NFD", "금회")
    text = f"📌 {decomposed} 신청"
    source = NoticeGlossaryInput(text=text)
    (result,) = locate_candidates(source, [candidate(decomposed)])
    (occurrence,) = result.occurrences
    assert result.candidate.query == "금회"
    assert (occurrence.start, occurrence.end) == (2, 2 + len(decomposed))
    assert occurrence.text == decomposed
    with pytest.raises(ValueError, match="원문"):
        locate_candidates(source, [candidate("금회")])
    assert source.text == text


def test_matching_is_case_sensitive_and_does_not_infer_unattested_surface() -> None:
    source = NoticeGlossaryInput(text="API 안내 및 익일에 접수")
    with pytest.raises(ValueError, match="원문"):
        locate_candidates(source, [candidate("api")])
    with pytest.raises(ValueError, match="원문"):
        locate_candidates(source, [candidate("다음 날", "익일")])


def test_explicit_compound_surface_is_accepted_but_embedded_short_term_is_not() -> None:
    source = NoticeGlossaryInput(text="다문화가정 지원. 가정 방문. 다문화가정에 신청.")
    terms = locate_candidates(source, [candidate("가정"), candidate("다문화가정")])
    assert [occurrence.start for occurrence in terms[0].occurrences] == [10]
    assert [occurrence.start for occurrence in terms[1].occurrences] == [0, 17]


@pytest.mark.parametrize("suffix", ["에", "은", "를", "으로", "에서", "에게", "까지"])
def test_complete_explicit_korean_particles_are_accepted(suffix: str) -> None:
    text = f"익일{suffix} 접수"
    (result,) = locate_candidates(NoticeGlossaryInput(text=text), [candidate("익일")])
    (occurrence,) = result.occurrences
    assert (occurrence.start, occurrence.end, occurrence.text) == (0, 2, "익일")
    assert occurrence.context == text


@pytest.mark.parametrize(
    "text",
    ["다문화가정", "가정지원", "가정도우미", "익일정산", "익일에도", "익일에신청"],
)
def test_embedded_words_and_unlisted_endings_are_rejected(text: str) -> None:
    surface = "가정" if "가정" in text else "익일"
    with pytest.raises(ValueError, match="원문"):
        locate_candidates(NoticeGlossaryInput(text=text), [candidate(surface)])


@pytest.mark.parametrize("text", ["120", "20년", "20만", "제20", "20_접수", "a20", "20a"])
def test_numeric_surfaces_require_word_boundaries(text: str) -> None:
    with pytest.raises(ValueError, match="원문"):
        locate_candidates(NoticeGlossaryInput(text=text), [candidate("20")])


def test_standalone_numbers_and_punctuation_separated_terms_are_accepted() -> None:
    text = "(20), 20 / 익일: 접수"
    number, word = locate_candidates(
        NoticeGlossaryInput(text=text), [candidate("20"), candidate("익일")]
    )
    assert [occurrence.start for occurrence in number.occurrences] == [1, 6]
    assert word.occurrences[0].text == "익일"


def test_combining_marks_and_connector_punctuation_do_not_create_word_boundaries() -> None:
    for text in ("a\u0301", "a_", "_a", "a＿", "가정\u0301"):
        surface = "가정" if text.startswith("가정") else "a"
        with pytest.raises(ValueError, match="원문"):
            locate_candidates(NoticeGlossaryInput(text=text), [candidate(surface)])


def test_identical_candidates_collapse_in_first_supplied_order() -> None:
    source = NoticeGlossaryInput(text="금회 접수는 익일 마감. 익일 안내")
    terms = locate_candidates(
        source,
        [candidate("익일"), candidate("금회"), candidate("익일", " 익일\n")],
    )
    assert [term.candidate.surface for term in terms] == ["익일", "금회"]
    assert len(terms[0].occurrences) == 2
    assert locate_candidates(source, []) == ()


def test_same_surface_with_conflicting_queries_is_rejected() -> None:
    with pytest.raises(ValueError, match="서로 다른"):
        locate_candidates(
            NoticeGlossaryInput(text="익일 접수"),
            [candidate("익일"), candidate("익일", "다음 날")],
        )


def test_unattested_candidate_fails_the_contract_even_after_valid_candidate() -> None:
    with pytest.raises(ValueError, match="원문"):
        locate_candidates(
            NoticeGlossaryInput(text="익일 접수"), [candidate("익일"), candidate("금회")]
        )
    with pytest.raises(TypeError, match="TermCandidate"):
        locate_candidates(NoticeGlossaryInput(text="익일 접수"), ["익일"])


@pytest.mark.parametrize(
    "text",
    ["", " \r\n\t ", "x" * 100_001, None, 3],
    ids=["empty", "whitespace", "over-limit", "null", "number"],
)
def test_source_rejects_blank_nontext_and_over_limit_input(text) -> None:
    with pytest.raises(ValidationError):
        NoticeGlossaryInput(text=text)


def test_source_accepts_exact_limit_and_optional_positive_notice_id() -> None:
    assert len(NoticeGlossaryInput(text="x" * 100_000).text) == 100_000
    assert NoticeGlossaryInput(text="안내").notice_id is None
    assert NoticeGlossaryInput(text="안내", notice_id=1).notice_id == 1


@pytest.mark.parametrize("notice_id", [0, -1, True, 1.5, "1"])
def test_notice_id_must_be_a_positive_integer(notice_id) -> None:
    with pytest.raises(ValidationError):
        NoticeGlossaryInput(text="안내", notice_id=notice_id)


@pytest.mark.parametrize("surface", ["", " \n\t ", None, 12])
def test_candidate_rejects_blank_or_nontext_surfaces(surface) -> None:
    with pytest.raises(ValidationError):
        TermCandidate(surface=surface, query="익일")


@pytest.mark.parametrize("query", ["", " \n\t ", "x" * 201, "a\x00b", None, 12])
def test_candidate_rejects_invalid_queries(query) -> None:
    with pytest.raises(ValidationError):
        TermCandidate(surface="익일", query=query)


def test_models_are_frozen_and_forbid_unknown_fields() -> None:
    source = NoticeGlossaryInput(text="익일")
    term = candidate("익일")
    (located,) = locate_candidates(source, [term])
    occurrence = located.occurrences[0]
    for model, field, value in (
        (source, "text", "금회"),
        (term, "surface", "금회"),
        (occurrence, "start", 1),
        (located, "candidate", candidate("금회")),
    ):
        with pytest.raises(ValidationError):
            setattr(model, field, value)
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(), "unexpected": "value"})


@pytest.mark.parametrize(
    "changes",
    [
        {"start": -1},
        {"end": 1},
        {"context_start": 1},
        {"context_end": 3},
        {"text": "금회"},
        {"context": "금회"},
        {"start": True},
    ],
)
def test_occurrence_rejects_inconsistent_indices_and_slices(changes) -> None:
    with pytest.raises(ValidationError):
        SourceOccurrence.model_validate(
            {
                "start": 0,
                "end": 2,
                "text": "익일",
                "context_start": 0,
                "context_end": 2,
                "context": "익일",
                **changes,
            }
        )


def test_located_term_rejects_empty_duplicates_and_surface_mismatch() -> None:
    (located,) = locate_candidates(NoticeGlossaryInput(text="익일"), [candidate("익일")])
    with pytest.raises(ValidationError):
        LocatedTerm(candidate=located.candidate, occurrences=())
    with pytest.raises(ValidationError):
        LocatedTerm(candidate=located.candidate, occurrences=located.occurrences * 2)
    with pytest.raises(ValidationError):
        LocatedTerm(candidate=candidate("금회"), occurrences=located.occurrences)
