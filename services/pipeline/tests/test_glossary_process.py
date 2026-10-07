"""Offline dictionary processing, conservative decisions and resumable query limits."""

import unicodedata
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pipeline.glossary.client import GlossaryAPIError
from pipeline.glossary.config import GlossaryConfigurationError
from pipeline.glossary.document import NoticeGlossaryResult
from pipeline.glossary.models import GlossaryEntry, GlossaryLookup, NormInfo
from pipeline.glossary.process import process_notice_glossary
from pipeline.glossary.source import NoticeGlossaryInput, source_hash

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


def test_resume_of_same_plain_text_does_not_reuse_a_different_collected_revision():
    old = NoticeGlossaryInput(notice_id=42, text="익일", notice_revision="a" * 64)
    previous = process_notice_glossary(old, lambda query: result(query), clock=lambda: NOW)
    current = NoticeGlossaryInput(notice_id=42, text="익일", notice_revision="b" * 64)
    calls = []

    def lookup(query):
        calls.append(query)
        return result(query, (entry(query, ("다음 날",)),))

    returned = process_notice_glossary(current, lookup, previous=previous, clock=lambda: NOW)
    assert calls == ["익일"]
    assert returned.easy_text == "다음 날"
    assert returned.notice_revision == current.notice_revision


def result(
    query: str, entries: tuple[GlossaryEntry, ...] = (), cached: bool = False
) -> GlossaryLookup:
    return GlossaryLookup(
        query=query,
        status="found" if entries else "not_found",
        entries=entries,
        providers_checked=("opendict",),
        queried_at=NOW,
        from_cache=cached,
    )


def test_skipped_easy_words_do_not_consume_lookup_budget_or_leave_failed_work() -> None:
    source = NoticeGlossaryInput(text="만 외 달 말 전 등 후 그리고 있는 대상 익일에 증빙서류")
    calls = []

    def lookup(query: str) -> GlossaryLookup:
        assert query in {"대상", "익일에", "익일", "증빙서류"}
        calls.append(query)
        return result(query, (entry(query, ("다음 날",)),) if query == "익일" else ())

    processed = process_notice_glossary(source, lookup, max_queries=4, clock=lambda: NOW)
    assert calls == ["대상", "익일에", "익일", "증빙서류"]
    assert processed.status == "completed"
    assert processed.pending_queries == ()
    assert processed.new_query_count == 4
    assert processed.original_text == source.text
    assert processed.easy_text == "만 외 달 말 전 등 후 그리고 있는 대상 다음 날에 증빙서류"


def historical_krdict_lookup(*, providers=("onterm", "opendict", "krdict")):
    historical = GlossaryEntry(
        provider="krdict",
        entry_id="123",
        sense_id="1",
        headword="익일",
        definition="다음 날.",
        easy_terms=("다음 날",),
        source_url="https://krdict.korean.go.kr/word/123",
    )
    return GlossaryLookup(
        query="익일",
        status="found",
        entries=(historical,),
        providers_checked=providers,
        queried_at=NOW,
        from_cache=True,
    )


def test_old_fallback_cache_reuses_empty_active_sources_without_excluded_definition():
    cached = historical_krdict_lookup()
    fake = FakeLookup()
    processed = process_notice_glossary(
        NoticeGlossaryInput(text="익일"), fake, cached_lookup=lambda query: cached
    )
    assert fake.calls == []
    assert processed.new_query_count == 0
    assert processed.easy_text == processed.original_text == "익일"
    assert processed.terms[0].status == "not_found"
    assert processed.terms[0].entries == ()
    assert processed.queries[0].lookup.providers_checked == ("onterm", "opendict")
    assert cached.entries[0].provider == "krdict"


@pytest.mark.parametrize("providers", [("krdict",), ("onterm", "krdict", "opendict")])
def test_unproven_active_empty_searches_query_active_sources_again(providers):
    cached = historical_krdict_lookup(providers=providers)
    fake = FakeLookup({"익일": result("익일", (entry("익일", ("다음 날",)),))})
    processed = process_notice_glossary(
        NoticeGlossaryInput(text="익일"), fake, cached_lookup=lambda query: cached
    )
    assert fake.calls == ["익일"]
    assert processed.easy_text == "다음 날"
    assert processed.terms[0].entries[0].provider == "opendict"


def test_historical_cache_preserves_active_definitions_and_excludes_inactive_senses():
    cached = historical_krdict_lookup().model_copy(
        update={"entries": (entry("익일", ("이튿날",)), *historical_krdict_lookup().entries)}
    )
    fake = FakeLookup()
    processed = process_notice_glossary(
        NoticeGlossaryInput(text="익일"), fake, cached_lookup=lambda query: cached
    )
    assert fake.calls == []
    assert processed.easy_text == "이튿날"
    assert {item.provider for item in processed.terms[0].entries} == {"opendict"}


def test_new_excluded_source_response_is_a_failure_not_a_successful_empty_search():
    fake = FakeLookup({"익일": historical_krdict_lookup()})
    processed = process_notice_glossary(NoticeGlossaryInput(text="익일"), fake)
    assert processed.status == "partial"
    assert processed.easy_text == "익일"
    assert processed.terms[0].error_code == "invalid_response"
    assert processed.queries[0].lookup is None


class FakeLookup:
    def __init__(self, answers: dict[str, GlossaryLookup | Exception] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[str] = []

    def __call__(self, query: str) -> GlossaryLookup:
        self.calls.append(query)
        answer = self.answers.get(query, result(query))
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_only_confirmed_terms_change_original_and_protected_details_remain_exact() -> None:
    text = (
        " 금회는 익일에 접수하지 않습니다.\r\n2026-10-06까지 100만원; 제외: 예외 대상은 신청 불가! "
    )
    source = NoticeGlossaryInput(notice_id=42, text=text)
    fake = FakeLookup(
        {
            "금회": result("금회", (entry("금회", ("이번 회",)),)),
            "익일": result("익일", (entry("익일", ("다음 날",)),)),
        }
    )
    processed = process_notice_glossary(source, fake, clock=lambda: NOW)
    assert processed.original_text == source.text == text
    assert processed.easy_text == text.replace("금회는", "이번 회는").replace("익일에", "다음 날에")
    assert processed.source_hash == source_hash(text)
    assert [change.original for change in processed.changes] == ["금회는", "익일"]
    assert processed.generated_at == NOW
    assert processed.status == "completed"


@pytest.mark.parametrize(
    "entries,status,easy_text",
    [
        ((entry("익일"),), "explained", "익일"),
        ((entry("익일", ("다음 날", "내일")),), "replaced", "다음 날(내일)"),
        ((entry("익일", ("다음 날",)), entry("익일", ("내일",), "2")), "ambiguous", "익일"),
        ((entry("익일", ("다음 날",)), entry("익일", (), "2")), "ambiguous", "익일"),
        ((entry("익일", ("다음 날",)), entry("익일", ("다음 날",), "2")), "replaced", "다음 날"),
        ((entry("익일", ("익일",)),), "explained", "익일"),
    ],
)
def test_sense_agreement_controls_replacement_without_generated_expressions(
    entries, status, easy_text
) -> None:
    processed = process_notice_glossary(
        NoticeGlossaryInput(text="익일"),
        FakeLookup(
            {
                "익일": result("익일", entries),
            }
        ),
    )
    assert processed.terms[0].status == status
    assert processed.easy_text == easy_text
    assert processed.terms[0].entries == entries


def test_repeated_exact_word_has_one_query_and_all_original_occurrences() -> None:
    fake = FakeLookup({"금회": result("금회", (entry("금회", ("이번 회",)),))})
    processed = process_notice_glossary(NoticeGlossaryInput(text="금회 금회 금회"), fake)
    assert fake.calls == ["금회"]
    assert processed.new_query_count == 1
    assert processed.easy_text == "이번 회 이번 회 이번 회"
    assert [(change.start, change.end) for change in processed.changes] == [(0, 2), (3, 5), (6, 8)]


def test_saved_official_parallel_use_relation_is_reinterpreted_without_new_lookup() -> None:
    # This official note previously had no parsed easy_terms in the live cache.
    official = GlossaryEntry(
        provider="opendict",
        entry_id="462416",
        sense_id="001",
        headword="선착-순",
        definition="먼저 와 닿는 차례.",
        norm_info=(
            NormInfo(
                type="순화",
                role="행정 용어 순화 편람(1993년 2월 12일)",
                description="‘선착순’과 ‘먼저 온 차례’, ‘온 차례’를 함께 쓸 수 있다고 되어 있다.",
            ),
        ),
        source_url="https://opendict.korean.go.kr/dictionary/view?sense_no=462416",
    )
    cached = result("선착순", (official,), cached=True)
    network = FakeLookup()
    original = "선착순 선착순"
    processed = process_notice_glossary(
        NoticeGlossaryInput(text=original),
        network,
        cached_lookup=lambda query: cached if query == "선착순" else None,
    )
    assert network.calls == []
    assert processed.new_query_count == 0
    assert processed.original_text == original
    assert processed.easy_text == "먼저 온 차례(온 차례) 먼저 온 차례(온 차례)"
    assert processed.queries[0].lookup.from_cache
    assert processed.terms[0].entries[0].norm_info == official.norm_info
    assert processed.terms[0].entries[0].easy_terms == ("먼저 온 차례", "온 차례")
    assert cached.entries[0].easy_terms == ()
    assert NoticeGlossaryResult.model_validate_json(processed.model_dump_json()) == processed


def test_found_whole_noun_blocks_stripped_lookup_and_unrelated_occurrence_changes() -> None:
    fake = FakeLookup(
        {
            "가정이": result("가정이", (entry("가정이"),)),
            "가정": result("가정", (entry("가정", ("집",)),)),
        }
    )
    processed = process_notice_glossary(NoticeGlossaryInput(text="가정이 가정에 가정이"), fake)
    assert fake.calls == ["가정이", "가정에", "가정"]
    assert processed.easy_text == "가정이 집에 가정이"
    assert processed.terms[0].status == "explained"
    assert [occurrence.start for occurrence in processed.terms[0].occurrences] == [0, 8]
    assert len(processed.changes) == 1


def test_whole_word_not_found_enables_literal_particle_alternatives() -> None:
    fake = FakeLookup({"가정": result("가정", (entry("가정", ("집",)),))})
    processed = process_notice_glossary(NoticeGlossaryInput(text="가정으로"), fake)
    assert fake.calls == ["가정으로", "가정"]
    assert processed.easy_text == "집으로"
    assert processed.terms[0].word == "가정으로"
    assert processed.terms[0].surface == "가정"
    assert processed.changes[0].original == "가정으로"


@pytest.mark.parametrize(
    "error,code",
    [
        (GlossaryConfigurationError("private-secret"), "configuration"),
        *[
            (GlossaryAPIError("opendict", code), code)
            for code in (
                "authentication",
                "timeout",
                "transport",
                "rate_limit",
                "http",
                "invalid_response",
                "response_too_large",
                "too_many_results",
                "api",
            )
        ],
        (GlossaryAPIError("opendict", "private-secret"), "api"),
        (GlossaryAPIError("opendict", "timeout key=private-secret"), "api"),
        (GlossaryAPIError("opendict", "https://example.test/?key=private-secret"), "api"),
    ],
)
def test_expected_lookup_failures_keep_term_safe_code_and_successful_other_terms(
    error, code, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    # The processing boundary must persist only the allowlisted code, even if an
    # exception message unexpectedly contains request details or a response body.
    error.args = ("private-secret request details and remote response text",)
    fake = FakeLookup(
        {
            "금회": error,
            "익일": result("익일", (entry("익일", ("다음 날",)),)),
        }
    )
    processed = process_notice_glossary(
        NoticeGlossaryInput(text="금회 익일"),
        fake,
        cached_lookup=(
            lambda query: (
                result(query, (entry(query, ("다음 날",)),), cached=True)
                if query == "익일"
                else None
            )
        ),
    )
    assert processed.original_text == "금회 익일"
    assert processed.easy_text == "금회 다음 날"
    assert processed.status == "partial"
    assert processed.terms[0].status == "failed"
    assert processed.terms[0].error_code == code
    assert processed.terms[0].entries == ()
    assert processed.queries[0].error_code == code
    assert processed.queries[0].lookup is None
    assert processed.terms[1].status == "replaced"
    assert processed.pending_queries == ("금회",)
    saved = processed.model_dump_json()
    assert "private-secret" not in saved
    assert NoticeGlossaryResult.model_validate_json(saved) == processed
    captured = capsys.readouterr()
    assert "private-secret" not in captured.out + captured.err + caplog.text


@pytest.mark.parametrize("code", ["authentication", "timeout", "invalid_response", "transport"])
def test_failed_full_word_is_not_treated_as_empty_search_for_a_stripped_candidate(code) -> None:
    fake = FakeLookup(
        {
            "익일에": GlossaryAPIError("opendict", code),
            "익일": result("익일", (entry("익일", ("다음 날",)),)),
        }
    )
    processed = process_notice_glossary(NoticeGlossaryInput(text="익일에"), fake)
    assert fake.calls == ["익일에"]
    assert processed.original_text == processed.easy_text == "익일에"
    assert processed.terms[0].status == "failed"
    assert processed.terms[0].error_code == processed.queries[0].error_code == code
    assert processed.queries[0].lookup is None
    assert processed.changes == ()
    assert processed.pending_queries == ("익일에",)


@pytest.mark.parametrize("provider", ["onterm", "opendict"])
def test_rate_limit_defers_new_queries_but_keeps_cached_changes_and_can_resume(provider) -> None:
    source = NoticeGlossaryInput(text="금회 익일 안내 익일에")
    cache = {
        "익일": result("익일", (entry("익일", ("다음 날",)),), cached=True),
        "익일에": result("익일에", cached=True),
    }
    blocked = FakeLookup({"금회": GlossaryAPIError(provider, "rate_limit")})
    partial = process_notice_glossary(source, blocked, cached_lookup=cache.get)
    assert blocked.calls == ["금회"]
    assert partial.new_query_count == 1
    assert partial.easy_text == "금회 다음 날 안내 다음 날에"
    assert partial.original_text == source.text
    assert partial.status == "partial"
    assert [term.status for term in partial.terms] == ["failed", "replaced", "pending", "replaced"]
    assert partial.pending_queries == ("금회", "안내")
    assert all(outcome.query != "안내" for outcome in partial.queries)
    assert NoticeGlossaryResult.model_validate_json(partial.model_dump_json()) == partial

    resumed_lookup = FakeLookup()
    completed = process_notice_glossary(source, resumed_lookup, previous=partial)
    assert resumed_lookup.calls == ["안내", "금회"]
    assert completed.status == "completed"
    assert completed.easy_text == partial.easy_text
    assert completed.changes == partial.changes


def test_rate_limit_preserves_success_from_before_limit_without_more_requests() -> None:
    fake = FakeLookup(
        {
            "익일": result("익일", (entry("익일", ("다음 날",)),)),
            "금회": GlossaryAPIError("onterm", "rate_limit"),
        }
    )
    partial = process_notice_glossary(NoticeGlossaryInput(text="익일 금회 안내"), fake)
    assert fake.calls == ["익일", "금회"]
    assert partial.easy_text == "다음 날 금회 안내"
    assert partial.new_query_count == 2
    assert partial.terms[-1].status == "pending"


@pytest.mark.parametrize(
    "bad_answer",
    [
        None,
        {"status": "not_found"},
        result("금회"),
        result("익일", (entry("금회", ("올해",)),)),
    ],
)
def test_invalid_lookup_identity_or_return_type_cannot_replace_source(bad_answer) -> None:
    processed = process_notice_glossary(NoticeGlossaryInput(text="익일"), lambda _: bad_answer)
    assert processed.easy_text == "익일"
    assert processed.terms[0].status == "failed"
    assert processed.terms[0].error_code == "invalid_response"


@pytest.mark.parametrize("error_type", [RuntimeError, TypeError, ValueError])
def test_unexpected_lookup_programming_errors_propagate(error_type) -> None:
    def broken(query: str) -> GlossaryLookup:
        raise error_type("programming defect")

    with pytest.raises(error_type, match="programming defect"):
        process_notice_glossary(NoticeGlossaryInput(text="익일"), broken)


def test_default_budget_is_one_hundred_distinct_uncached_queries() -> None:
    source = NoticeGlossaryInput(text=" ".join(f"용어{index}" for index in range(101)))
    fake = FakeLookup()
    processed = process_notice_glossary(source, fake)
    assert len(fake.calls) == processed.new_query_count == 100
    assert len(set(fake.calls)) == 100
    assert processed.pending_queries == ("용어100",)
    assert processed.terms[-1].status == "pending"
    assert processed.status == "partial"
    assert processed.easy_text == source.text


def test_cache_hits_do_not_use_uncached_query_budget() -> None:
    fake = FakeLookup()
    cached = {query: result(query, (entry(query),), cached=True) for query in ("금회", "익일")}
    processed = process_notice_glossary(
        NoticeGlossaryInput(text="안내 금회 익일"),
        fake,
        cached_lookup=cached.get,
        max_queries=1,
    )
    assert fake.calls == ["안내"]
    assert processed.new_query_count == 1
    assert processed.status == "completed"
    assert all(outcome.lookup.from_cache for outcome in processed.queries[-2:])


@pytest.mark.parametrize(
    "query,headword,entry_id",
    [("중위소득", "중위^소득", "728512"), ("원어민", "원어-민", "596274")],
)
@pytest.mark.parametrize("cached", [False, True])
def test_official_headword_markers_match_source_without_changing_saved_spelling(
    query, headword, entry_id, cached
) -> None:
    official_entry = GlossaryEntry(
        provider="opendict",
        entry_id=entry_id,
        sense_id="001",
        headword=headword,
        definition="공식 사전 뜻풀이.",
        source_url=f"https://opendict.korean.go.kr/dictionary/view?sense_no={entry_id}",
    )
    found = result(query, (official_entry,), cached=cached)
    fake = FakeLookup({query: found})
    cache = {query: found} if cached else {}
    source = NoticeGlossaryInput(notice_id=42, text=f"{query} {query}")
    processed = process_notice_glossary(
        source, fake, cached_lookup=cache.get, max_queries=1, clock=lambda: NOW
    )
    assert fake.calls == ([] if cached else [query])
    assert processed.new_query_count == (0 if cached else 1)
    assert processed.status == "completed"
    assert processed.original_text == processed.easy_text == source.text
    assert processed.terms[0].status == "explained"
    assert len(processed.terms[0].occurrences) == 2
    assert processed.terms[0].entries == processed.queries[0].lookup.entries == (official_entry,)
    assert processed.queries[0].lookup.from_cache is cached
    assert official_entry.headword == headword


@pytest.mark.parametrize(
    "query,headword",
    [("중위소득", "중위^소득률"), ("원어민", "원어-민영"), ("중위소득", "다른^표제어")],
)
@pytest.mark.parametrize("cached", [False, True])
def test_marked_unrelated_headword_remains_invalid_for_live_and_cached_results(
    query, headword, cached
) -> None:
    found = result(query, (entry(headword, ("아무 표현",)),), cached=cached)
    fake = FakeLookup({query: found})
    cache = {query: found} if cached else {}
    processed = process_notice_glossary(
        NoticeGlossaryInput(text=query), fake, cached_lookup=cache.get, clock=lambda: NOW
    )
    assert processed.status == "partial"
    assert processed.original_text == processed.easy_text == query
    assert processed.terms[0].status == "failed"
    assert processed.terms[0].error_code == "invalid_response"
    assert processed.terms[0].entries == ()
    assert processed.queries[0].lookup is None
    assert fake.calls == ([] if cached else [query])


def test_corrupt_cached_headword_cannot_provide_replacement_evidence() -> None:
    cached = result("익일", (entry("금회", ("올해",)),), cached=True)
    processed = process_notice_glossary(
        NoticeGlossaryInput(text="익일"),
        FakeLookup(),
        cached_lookup=lambda _: cached,
    )
    assert processed.easy_text == "익일"
    assert processed.terms[0].status == "failed"
    assert processed.terms[0].error_code == "invalid_response"


def test_successful_queries_resume_without_repeat_until_completed() -> None:
    source = NoticeGlossaryInput(notice_id=42, text="알파 베타 감마")
    fake = FakeLookup()
    previous = None
    for expected in ("알파", "베타", "감마"):
        before = len(fake.calls)
        previous = process_notice_glossary(source, fake, previous=previous, max_queries=1)
        assert fake.calls[before:] == [expected]
        assert previous.new_query_count == 1
    assert previous.status == "completed"
    completed = process_notice_glossary(source, fake, previous=previous, max_queries=1)
    assert fake.calls == ["알파", "베타", "감마"]
    assert completed.new_query_count == 0
    assert completed.status == "completed"


def test_prior_failures_wait_until_all_unseen_words_have_a_turn_across_resumes() -> None:
    source = NoticeGlossaryInput(text="알파 베타 감마")
    fake = FakeLookup({"알파": GlossaryAPIError("opendict", "timeout")})
    previous = None
    for expected in ("알파", "베타", "감마", "알파"):
        before = len(fake.calls)
        previous = process_notice_glossary(source, fake, previous=previous, max_queries=1)
        assert fake.calls[before:] == [expected]
        assert [term.word for term in previous.terms] == ["알파", "베타", "감마"]
    assert previous.status == "partial"


@pytest.mark.parametrize("change", ["source", "rules", "notice_id"])
def test_changed_source_rules_or_notice_identity_invalidates_prior_successes(change: str) -> None:
    source = NoticeGlossaryInput(notice_id=42, text="알파 베타")
    fake = FakeLookup()
    previous = process_notice_glossary(source, fake)
    if change == "source":
        source = NoticeGlossaryInput(notice_id=42, text="알파 베타 감마")
    elif change == "rules":
        previous = previous.model_copy(update={"rules_version": "dictionary-replacement-old"})
    else:
        source = NoticeGlossaryInput(notice_id=43, text=source.text)
    before = len(fake.calls)
    processed = process_notice_glossary(source, fake, previous=previous)
    assert fake.calls[before:][:2] == ["알파", "베타"]
    assert processed.original_text == source.text


def test_unicode_equivalent_queries_deduplicate_without_normalizing_original_tokens() -> None:
    nfd = unicodedata.normalize("NFD", "금회")
    source = NoticeGlossaryInput(text=f"{nfd} 금회")
    fake = FakeLookup({"금회": result("금회", (entry("금회", ("이번 회",)),))})
    processed = process_notice_glossary(source, fake)
    assert fake.calls == ["금회"]
    assert processed.new_query_count == 1
    assert processed.original_text == source.text
    assert processed.easy_text == "이번 회 이번 회"
    assert [term.surface for term in processed.terms] == [nfd, "금회"]


def test_partial_output_roundtrip_can_resume_with_successful_evidence_intact() -> None:
    source = NoticeGlossaryInput(text="금회 익일")
    fake = FakeLookup({"금회": result("금회", (entry("금회", ("이번 회",)),))})
    first = process_notice_glossary(source, fake, max_queries=1)
    restored = NoticeGlossaryResult.model_validate_json(first.model_dump_json())
    second = process_notice_glossary(source, fake, previous=restored, max_queries=1)
    assert fake.calls == ["금회", "익일"]
    assert first.easy_text == second.easy_text == "이번 회 익일"
    assert first.status == "partial"
    assert second.status == "completed"


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, "1"])
def test_query_budget_requires_a_positive_integer(budget) -> None:
    with pytest.raises(ValueError):
        process_notice_glossary(NoticeGlossaryInput(text="익일"), FakeLookup(), max_queries=budget)


def test_processing_does_not_construct_a_gemini_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("dictionary processing attempted a Gemini call")

    monkeypatch.setattr("google.genai.Client", forbidden)
    processed = process_notice_glossary(NoticeGlossaryInput(text="익일"), FakeLookup())
    assert processed.easy_text == "익일"


def test_clock_needs_timezone_and_saved_results_reject_inconsistent_status() -> None:
    with pytest.raises(ValidationError):
        process_notice_glossary(
            NoticeGlossaryInput(text="익일"),
            FakeLookup(),
            clock=lambda: datetime(2026, 10, 6),
        )
    processed = process_notice_glossary(NoticeGlossaryInput(text="익일"), FakeLookup())
    values = processed.model_dump()
    values["status"] = "partial"
    with pytest.raises(ValidationError):
        NoticeGlossaryResult.model_validate(values)
