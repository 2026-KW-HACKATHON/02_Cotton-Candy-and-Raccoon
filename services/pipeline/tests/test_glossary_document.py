"""Dictionary evidence, original-coordinate edits and saved-result validation."""

import unicodedata
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pipeline.glossary.client import GlossaryAPIError
from pipeline.glossary.document import (
    RULES_VERSION,
    DocumentTerm,
    NoticeGlossaryResult,
    QueryOutcome,
    TextChange,
    _particle_change,
    apply_changes,
    confirmed_replacement,
    make_changes,
)
from pipeline.glossary.models import GlossaryEntry, GlossaryLookup
from pipeline.glossary.process import process_notice_glossary
from pipeline.glossary.source import NoticeGlossaryInput, TermCandidate, locate_candidates

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


def entry(query: str, easy_terms: tuple[str, ...] = (), sense: str = "1") -> GlossaryEntry:
    return GlossaryEntry(
        provider="opendict",
        entry_id="10",
        sense_id=sense,
        headword=query,
        definition="공식 사전 뜻풀이.",
        easy_terms=easy_terms,
        source_url=f"https://opendict.korean.go.kr/dictionary/view?sense_no={sense}",
    )


def lookup(query: str, entries: tuple[GlossaryEntry, ...] = ()) -> GlossaryLookup:
    return GlossaryLookup(
        query=query,
        status="found" if entries else "not_found",
        entries=entries,
        providers_checked=("opendict",),
        queried_at=NOW,
    )


def sample_result() -> NoticeGlossaryResult:
    source = NoticeGlossaryInput(notice_id=42, text="금회는 익일에 접수")

    def fake(query: str) -> GlossaryLookup:
        refined = {"금회": "이번 회", "익일": "다음 날"}.get(query)
        return lookup(query, (entry(query, (refined,)),) if refined else ())

    return process_notice_glossary(source, fake, clock=lambda: NOW)


@pytest.mark.parametrize(
    "entries,expected",
    [
        ((), None),
        ((entry("익일"),), None),
        ((entry("익일", ("다음 날",)),), "다음 날"),
        ((entry("익일", ("다음 날",)), entry("익일", ("다음 날",), "2")), "다음 날"),
        ((entry("익일", ("다음 날", "내일")),), "다음 날(내일)"),
        ((entry("익일", ("다음 날", "다음 날", "내일")),), "다음 날(내일)"),
        (
            (entry("익일", ("다음 날", "내일")), entry("익일", ("내일", "다음 날"), "2")),
            "다음 날(내일)",
        ),
        ((entry("익일", ("다음 날",)), entry("익일", (), "2")), None),
        ((entry("익일", ("다음 날",)), entry("익일", ("내일",), "2")), None),
    ],
)
def test_meanings_must_agree_on_official_alternatives_preserving_source_order(
    entries, expected
) -> None:
    assert confirmed_replacement(entries) == expected


@pytest.mark.parametrize(
    "particle,replacement,expected",
    [
        ("는", "통신", "통신은"),
        ("은", "자료", "자료는"),
        ("가", "통신", "통신이"),
        ("이", "자료", "자료가"),
        ("를", "통신", "통신을"),
        ("을", "자료", "자료를"),
        ("와", "통신", "통신과"),
        ("과", "자료", "자료와"),
        ("로", "통신", "통신으로"),
        ("으로", "자료", "자료로"),
        ("으로", "길", "길로"),
        ("는", "통신(연락)", "통신(연락)은"),
        ("은", "자료(정보)", "자료(정보)는"),
        ("으로", "길(도로)", "길(도로)로"),
    ],
)
def test_particle_spelling_follows_known_hangul_target_ending(
    particle: str, replacement: str, expected: str
) -> None:
    text = f"피투피{particle} 접수"
    end, changed = _particle_change(text, 3, replacement)
    assert end == 3 + len(particle)
    assert changed == expected
    assert text == f"피투피{particle} 접수"


@pytest.mark.parametrize(
    "particle,primary,expected_particle",
    [("는", "통신", "은"), ("가", "자료", "가"), ("으로", "길", "로")],
)
@pytest.mark.parametrize("parenthetical", [False, True])
def test_decomposed_official_target_keeps_spelling_with_correct_particle(
    particle, primary, expected_particle, parenthetical
) -> None:
    nfd = unicodedata.normalize("NFD", primary)
    target = nfd + ("(다른 표현)" if parenthetical else "")
    original = f"피투피{particle} 접수"
    assert _particle_change(original, 3, target) == (3 + len(particle), target + expected_particle)
    assert target.startswith(nfd)
    assert original == f"피투피{particle} 접수"


def test_nfd_official_target_roundtrips_exact_source_and_original_change_offsets() -> None:
    nfd_target = unicodedata.normalize("NFD", "통신")
    source = NoticeGlossaryInput(text=" 자료는 자료는! ")
    processed = process_notice_glossary(
        source,
        lambda query: lookup(query, (entry(query, (nfd_target,)),) if query == "자료" else ()),
        clock=lambda: NOW,
    )
    assert processed.original_text == source.text
    assert processed.easy_text == f" {nfd_target}은 {nfd_target}은! "
    assert [(item.start, item.end, item.original) for item in processed.changes] == [
        (1, 4, "자료는"),
        (5, 8, "자료는"),
    ]
    assert processed.terms[0].entries[0].easy_terms == (nfd_target,)
    assert NoticeGlossaryResult.model_validate_json(processed.model_dump_json()) == processed


@pytest.mark.parametrize(
    "text,replacement",
    [("피투피에게", "통신"), ("피투피는지원", "통신"), ("피투피은", "API")],
)
def test_particle_adjustment_does_not_guess_unknown_ending_or_word_suffix(
    text, replacement
) -> None:
    assert _particle_change(text, 3, replacement) == (3, replacement)


def test_length_changes_keep_later_offsets_in_the_original_source() -> None:
    result = sample_result()
    assert result.original_text == "금회는 익일에 접수"
    assert result.easy_text == "이번 회는 다음 날에 접수"
    assert [(change.start, change.end) for change in result.changes] == [(0, 3), (4, 6)]
    assert [change.original for change in result.changes] == ["금회는", "익일"]
    assert apply_changes(result.original_text, result.changes) == result.easy_text
    assert result.terms[1].occurrences[0].start == 4


def test_apply_changes_rejects_stale_slices_overlap_and_wrong_order() -> None:
    source = "익일 금회"
    first = TextChange(start=0, end=2, original="익일", replacement="다음 날", query="익일")
    second = TextChange(start=3, end=5, original="금회", replacement="이번 회", query="금회")
    assert apply_changes(source, (first, second)) == "다음 날 이번 회"
    for changes in ((second, first), (first, first)):
        with pytest.raises(ValueError):
            apply_changes(source, changes)
    with pytest.raises(ValueError):
        apply_changes("금회 익일", (first,))


def test_make_changes_rejects_overlapping_replacement_terms() -> None:
    source = NoticeGlossaryInput(text="익일")
    (located,) = locate_candidates(source, [TermCandidate(surface="익일", query="익일")])
    term = DocumentTerm(
        word="익일",
        surface="익일",
        query="익일",
        status="replaced",
        occurrences=located.occurrences,
        entries=(entry("익일", ("다음 날",)),),
        replacement="다음 날",
    )
    with pytest.raises(ValueError, match="겹"):
        make_changes(source.text, (term, term))


def test_saved_result_roundtrips_all_evidence_and_original_offsets() -> None:
    result = sample_result()
    restored = NoticeGlossaryResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.original_text == "금회는 익일에 접수"
    assert restored.terms[0].entries[0].source_url.startswith("https://opendict.korean.go.kr/")


def test_new_refinement_rules_rebuild_previous_completed_results() -> None:
    source = NoticeGlossaryInput(text="금회")
    previous = process_notice_glossary(source, lambda query: lookup(query), clock=lambda: NOW)
    previous = previous.model_copy(update={"rules_version": "dictionary-replacement-v3"})
    calls = []

    def updated(query: str) -> GlossaryLookup:
        calls.append(query)
        return lookup(query, (entry(query, ("이번 회",)),))

    current = process_notice_glossary(source, updated, previous=previous, clock=lambda: NOW)
    assert current.rules_version == RULES_VERSION == "dictionary-replacement-v4"
    assert calls == ["금회"]
    assert current.original_text == source.text
    assert current.easy_text == "이번 회"


def test_notice_revision_roundtrips_without_changing_original_hash_or_coordinates() -> None:
    processed = sample_result()
    payload = processed.model_dump(mode="json")
    payload["notice_revision"] = "a" * 64
    restored = NoticeGlossaryResult.model_validate(payload)
    assert NoticeGlossaryResult.model_validate_json(restored.model_dump_json()) == restored
    assert restored.notice_revision == "a" * 64
    assert restored.original_text == processed.original_text
    assert restored.source_hash == processed.source_hash
    assert restored.changes == processed.changes
    payload.pop("notice_revision")
    assert NoticeGlossaryResult.model_validate(payload).notice_revision is None


@pytest.mark.parametrize(
    "notice_id,revision", [(None, "a" * 64), (42, "a" * 63), (42, "A" * 64), (42, "revision")]
)
def test_notice_revision_requires_notice_identity_and_sha256_shape(notice_id, revision) -> None:
    payload = sample_result().model_dump(mode="json")
    payload.update(notice_id=notice_id, notice_revision=revision)
    with pytest.raises(ValidationError):
        NoticeGlossaryResult.model_validate(payload)


@pytest.mark.parametrize("query,headword", [("중위소득", "중위^소득"), ("원어민", "원어-민")])
def test_db_json_roundtrip_keeps_marked_headword_and_exact_original_source(query, headword) -> None:
    source = NoticeGlossaryInput(notice_id=42, text=f" {query}\r\n{query} ")
    official_entry = entry(headword, sense="001")
    result = process_notice_glossary(
        source, lambda current: lookup(current, (official_entry,)), clock=lambda: NOW
    )
    # This is the JSON model payload used by notice_glossary storage's Jsonb parameter.
    payload = result.model_dump(mode="json")
    assert payload["terms"][0]["entries"][0]["headword"] == headword
    assert payload["queries"][0]["lookup"]["entries"][0]["headword"] == headword
    restored = NoticeGlossaryResult.model_validate(payload)
    assert NoticeGlossaryResult.model_validate_json(result.model_dump_json()) == restored == result
    assert restored.original_text == restored.easy_text == source.text
    assert restored.terms[0].entries == restored.queries[0].lookup.entries == (official_entry,)
    assert [item.text for item in restored.terms[0].occurrences] == [query, query]
    assert restored.changes == ()


@pytest.mark.parametrize("headword", ["中位^소득", "중위^소득률", "다른-표제어"])
def test_saved_document_rejects_unrelated_marked_dictionary_headword(headword) -> None:
    source = NoticeGlossaryInput(text="중위소득")
    accepted_entry = entry("중위^소득", sense="001")
    result = process_notice_glossary(
        source, lambda current: lookup(current, (accepted_entry,)), clock=lambda: NOW
    )
    payload = result.model_dump(mode="json")
    payload["terms"][0]["entries"][0]["headword"] = headword
    payload["queries"][0]["lookup"]["entries"][0]["headword"] = headword
    with pytest.raises(ValidationError):
        NoticeGlossaryResult.model_validate(payload)


@pytest.mark.parametrize("field", ["original_text", "easy_text", "source_hash", "changes"])
def test_saved_result_rejects_tampering_with_original_or_derived_text(field: str) -> None:
    values = sample_result().model_dump(mode="json")
    if field == "original_text":
        values[field] = "익일은 금회에 접수"
    elif field == "easy_text":
        values[field] += " 신청 가능"
    elif field == "source_hash":
        values[field] = "0" * 64
    else:
        values[field][0]["replacement"] = "아무 표현"
    with pytest.raises(ValidationError):
        NoticeGlossaryResult.model_validate(values)


def test_saved_result_rejects_tampered_occurrence_context() -> None:
    values = sample_result().model_dump(mode="json")
    values["terms"][0]["occurrences"][0]["context"] = "x" * len(values["original_text"])
    with pytest.raises(ValidationError):
        NoticeGlossaryResult.model_validate(values)


@pytest.mark.parametrize("tamper", ["missing_queries", "wrong_term_query", "wrong_headword"])
def test_saved_result_rejects_missing_or_mismatched_dictionary_evidence(tamper: str) -> None:
    values = sample_result().model_dump(mode="json")
    if tamper == "missing_queries":
        values["queries"] = []
    elif tamper == "wrong_term_query":
        values["terms"][0]["query"] = "다른 용어"
        values["changes"][0]["query"] = "다른 용어"
    else:
        values["terms"][0]["entries"][0]["headword"] = "다른 용어"
    with pytest.raises(ValidationError):
        NoticeGlossaryResult.model_validate(values)


def test_saved_result_cannot_bypass_a_found_whole_word_with_stripped_evidence() -> None:
    source = NoticeGlossaryInput(text="가정이")
    processed = process_notice_glossary(
        source,
        lambda query: lookup(query, (entry(query),)),
        clock=lambda: NOW,
    )
    values = processed.model_dump()
    base_lookup = lookup("가정", (entry("가정", ("집",)),))
    term = values["terms"][0]
    term.update(
        surface="가정",
        query="가정",
        status="replaced",
        replacement="집",
        entries=base_lookup.model_dump()["entries"],
    )
    term["occurrences"][0].update(end=2, text="가정")
    values["queries"] += ({"query": "가정", "lookup": base_lookup.model_dump()},)
    values["changes"] = (
        {
            "start": 0,
            "end": 3,
            "original": "가정이",
            "replacement": "집이",
            "query": "가정",
        },
    )
    values["easy_text"] = "집이"
    with pytest.raises(ValidationError):
        NoticeGlossaryResult.model_validate(values)


def test_saved_result_requires_successful_empty_queries_before_a_stripped_candidate() -> None:
    values = sample_result().model_dump()
    values["queries"] = tuple(item for item in values["queries"] if item["query"] != "금회는")
    with pytest.raises(ValidationError):
        NoticeGlossaryResult.model_validate(values)


def test_saved_result_rejects_forged_original_word_with_valid_substring_offsets() -> None:
    values = sample_result().model_dump(mode="json")
    values["terms"][0]["word"] = "다른 단어"
    with pytest.raises(ValidationError):
        NoticeGlossaryResult.model_validate(values)


def test_saved_result_rejects_omitted_unprocessed_word() -> None:
    result = sample_result()
    values = result.model_dump(mode="json")
    values["terms"] = values["terms"][:-1]
    with pytest.raises(ValidationError):
        NoticeGlossaryResult.model_validate(values)


def test_query_outcomes_cannot_confuse_failure_empty_success_or_query_identity() -> None:
    assert QueryOutcome(query="익일", lookup=lookup("익일")).error_code is None
    assert QueryOutcome(query="익일", error_code="api").lookup is None
    for values in (
        {"query": "익일"},
        {"query": "익일", "lookup": lookup("익일"), "error_code": "api"},
        {"query": "익일", "lookup": lookup("금회")},
    ):
        with pytest.raises(ValidationError):
            QueryOutcome.model_validate(values)


@pytest.mark.parametrize(
    "code",
    [
        "configuration",
        "authentication",
        "timeout",
        "transport",
        "rate_limit",
        "http",
        "invalid_response",
        "response_too_large",
        "too_many_results",
        "api",
    ],
)
def test_query_and_term_models_roundtrip_known_failure_codes_without_empty_success(code) -> None:
    source = NoticeGlossaryInput(text="익일")
    (located,) = locate_candidates(source, [TermCandidate(surface="익일", query="익일")])
    outcome = QueryOutcome(query="익일", error_code=code)
    term = DocumentTerm(
        word="익일",
        surface="익일",
        query="익일",
        status="failed",
        occurrences=located.occurrences,
        error_code=code,
    )
    assert QueryOutcome.model_validate_json(outcome.model_dump_json()) == outcome
    assert DocumentTerm.model_validate_json(term.model_dump_json()) == term
    assert outcome.lookup is None
    assert term.entries == ()
    assert term.replacement is None
    with pytest.raises(ValidationError):
        QueryOutcome.model_validate({**outcome.model_dump(), "lookup": lookup("익일")})
    with pytest.raises(ValidationError):
        DocumentTerm.model_validate({**term.model_dump(), "status": "not_found"})


@pytest.mark.parametrize("code", ["api", "authentication", "timeout", "invalid_response"])
def test_saved_failure_json_keeps_exact_original_and_matching_query_term_code(code) -> None:
    source = NoticeGlossaryInput(notice_id=42, text=" 익일\r\n익일! ")

    def fail(query: str) -> GlossaryLookup:
        raise GlossaryAPIError("opendict", code)

    processed = process_notice_glossary(source, fail, clock=lambda: NOW)
    payload = processed.model_dump(mode="json")
    # This is the same JSON payload passed to notice storage. The historical api
    # code remains readable alongside the new diagnostic codes without a version bump.
    restored = NoticeGlossaryResult.model_validate(payload)
    assert NoticeGlossaryResult.model_validate_json(processed.model_dump_json()) == restored
    assert restored.original_text == restored.easy_text == source.text
    assert restored.status == "partial"
    assert restored.changes == ()
    assert restored.queries[0].lookup is None
    assert restored.queries[0].error_code == restored.terms[0].error_code == code
    assert restored.terms[0].status == "failed"
    assert [item.start for item in restored.terms[0].occurrences] == [1, 5]
    assert restored.pending_queries == ("익일",)


@pytest.mark.parametrize("field", ["queries", "terms"])
def test_saved_failure_rejects_unrecognized_or_inconsistent_error_codes(field) -> None:
    def fail(query: str) -> GlossaryLookup:
        raise GlossaryAPIError("opendict", "timeout")

    processed = process_notice_glossary(NoticeGlossaryInput(text="익일"), fail, clock=lambda: NOW)
    for code in ("authentication", "timeout key=private-secret"):
        payload = processed.model_dump(mode="json")
        payload[field][0]["error_code"] = code
        with pytest.raises(ValidationError) as caught:
            NoticeGlossaryResult.model_validate(payload)
        assert "private-secret" not in str(caught.value)


def test_saved_result_models_are_frozen_and_forbid_extra_fields() -> None:
    result = sample_result()
    for model, field, value in (
        (result, "easy_text", "변경"),
        (result.terms[0], "surface", "변경"),
        (result.queries[0], "query", "변경"),
        (result.changes[0], "replacement", "변경"),
    ):
        with pytest.raises(ValidationError):
            setattr(model, field, value)
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(), "extra": "value"})
