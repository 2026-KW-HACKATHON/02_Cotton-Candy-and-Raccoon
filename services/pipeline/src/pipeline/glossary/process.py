"""Probe original words without an LLM, preserve failures and resume bounded work."""

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import ValidationError

from pipeline.glossary.client import GlossaryAPIError, reparse_refinements
from pipeline.glossary.config import GlossaryConfigurationError
from pipeline.glossary.document import (
    GLOSSARY_FAILURE_CODES,
    RULES_VERSION,
    DocumentTerm,
    NoticeGlossaryResult,
    QueryOutcome,
    apply_changes,
    confirmed_replacement,
    make_changes,
)
from pipeline.glossary.models import (
    ACTIVE_PROVIDERS,
    GlossaryLookup,
    active_cached_lookup,
    canonical_headword,
)
from pipeline.glossary.source import NoticeGlossaryInput, SourceOccurrence, source_hash
from pipeline.glossary.tokenize import WordProbe, tokenize_notice

Lookup = Callable[[str], GlossaryLookup]
CachedLookup = Callable[[str], GlossaryLookup | None]


def _occurrences(probe: WordProbe, surface: str) -> tuple[SourceOccurrence, ...]:
    return tuple(
        SourceOccurrence(
            start=item.start,
            end=item.start + len(surface),
            text=surface,
            context_start=item.context_start,
            context_end=item.context_end,
            context=item.context,
        )
        for item in probe.occurrences
    )


def _validated_outcome(query: str, found: GlossaryLookup) -> QueryOutcome:
    try:
        if not isinstance(found, GlossaryLookup):
            raise ValueError("사전 조회 결과 형식이 올바르지 않습니다.")
        if any(provider not in ACTIVE_PROVIDERS for provider in found.providers_checked):
            raise ValueError("현재 사용하지 않는 사전의 조회 결과입니다.")
        found = reparse_refinements(found)
        if any(
            canonical_headword(entry.headword) != canonical_headword(query)
            for entry in found.entries
        ):
            raise ValueError("조회어와 사전 표제어가 일치하지 않습니다.")
        return QueryOutcome(query=query, lookup=found)
    except (ValidationError, ValueError):
        return QueryOutcome(query=query, error_code="invalid_response")


def process_notice_glossary(
    source: NoticeGlossaryInput,
    lookup: Lookup,
    *,
    cached_lookup: CachedLookup | None = None,
    previous: NoticeGlossaryResult | None = None,
    max_queries: int = 100,
    clock: Callable[[], datetime] | None = None,
) -> NoticeGlossaryResult:
    """At most max_queries distinct uncached words per run; successful work survives retries.

    Once a rate limit is reported, defer further uncached queries for this run;
    successful prior outcomes and cached terms can still be used. A later run
    retries the deferred work without assuming a reset time for either provider.
    Whole-word meanings take priority over particle-stripped alternatives. Failures
    are never treated as a successful empty search. All replacements remain linked
    to the exact original; offsets always refer to original Unicode code points.
    """
    if isinstance(max_queries, bool) or not isinstance(max_queries, int) or max_queries < 1:
        raise ValueError("한 번에 조회할 후보 수는 양의 정수여야 합니다.")
    fingerprint = source_hash(source)
    if previous is not None and (
        previous.notice_id != source.notice_id
        or previous.notice_revision != source.notice_revision
        or previous.source_hash != fingerprint
        or previous.rules_version != RULES_VERSION
    ):
        previous = None
    outcomes = (
        {
            item.query: _validated_outcome(item.query, item.lookup) if item.lookup else item
            for item in previous.queries
        }
        if previous
        else {}
    )
    probes = tokenize_notice(source)

    # Give unseen words their turn before retrying earlier failing lookups.
    def retry_priority(probe: WordProbe) -> int:
        for candidate in probe.candidates:
            prior = outcomes.get(candidate.query)
            if prior is None:
                return 0
            if prior.lookup is None:
                return 1
            if prior.lookup.status == "found":
                return 0
        return 0

    ordered = sorted(probes, key=retry_priority)
    decisions: dict[str, DocumentTerm] = {}
    pending: list[str] = []
    new_queries = 0
    attempted: set[str] = set()
    rate_limited = False

    for probe in ordered:
        decision = None
        for candidate in probe.candidates:
            outcome = outcomes.get(candidate.query)
            if outcome is not None and outcome.lookup is None and candidate.query not in attempted:
                outcome = None
            if outcome is None and cached_lookup is not None:
                cached = active_cached_lookup(cached_lookup(candidate.query))
                if cached is not None:
                    outcome = _validated_outcome(candidate.query, cached)
                    outcomes[candidate.query] = outcome
                    attempted.add(candidate.query)
            if outcome is None:
                if rate_limited or new_queries >= max_queries:
                    pending.append(candidate.query)
                    decision = DocumentTerm(
                        word=probe.surface,
                        surface=candidate.surface,
                        query=candidate.query,
                        status="pending",
                        occurrences=_occurrences(probe, candidate.surface),
                    )
                    break
                new_queries += 1
                attempted.add(candidate.query)
                try:
                    found = lookup(candidate.query)
                except GlossaryConfigurationError:
                    outcome = QueryOutcome(query=candidate.query, error_code="configuration")
                except GlossaryAPIError as error:
                    # Only public codes enter JSON/DB; arbitrary exception text
                    # and credential-bearing remote details never do.
                    code = (
                        error.code
                        if isinstance(error.code, str) and error.code in GLOSSARY_FAILURE_CODES
                        else "api"
                    )
                    outcome = QueryOutcome(query=candidate.query, error_code=code)
                    if code == "rate_limit":
                        rate_limited = True
                else:
                    outcome = _validated_outcome(candidate.query, found)
                outcomes[candidate.query] = outcome
            if outcome.lookup is None:
                pending.append(candidate.query)
                decision = DocumentTerm(
                    word=probe.surface,
                    surface=candidate.surface,
                    query=candidate.query,
                    status="failed",
                    error_code=outcome.error_code,
                    occurrences=_occurrences(probe, candidate.surface),
                )
                break
            found = outcome.lookup
            if found.status == "not_found":
                continue
            replacement = confirmed_replacement(found.entries)
            if replacement is not None and replacement != candidate.surface:
                status = "replaced"
            elif any(entry.easy_terms for entry in found.entries):
                status = "ambiguous" if replacement is None else "explained"
                replacement = None
            else:
                status, replacement = "explained", None
            decision = DocumentTerm(
                word=probe.surface,
                surface=candidate.surface,
                query=candidate.query,
                status=status,
                entries=found.entries,
                replacement=replacement,
                occurrences=_occurrences(probe, candidate.surface),
            )
            break
        if decision is None:
            candidate = probe.candidates[0]
            decision = DocumentTerm(
                word=probe.surface,
                surface=candidate.surface,
                query=candidate.query,
                status="not_found",
                occurrences=_occurrences(probe, candidate.surface),
            )
        decisions[probe.surface] = decision

    terms = tuple(decisions[probe.surface] for probe in probes)
    changes = make_changes(source.text, terms)
    pending_queries = tuple(dict.fromkeys(pending))
    return NoticeGlossaryResult(
        notice_id=source.notice_id,
        notice_revision=source.notice_revision,
        original_text=source.text,
        easy_text=apply_changes(source.text, changes),
        source_hash=fingerprint,
        rules_version=RULES_VERSION,
        generated_at=clock() if clock else datetime.now(UTC),
        status="partial" if pending_queries else "completed",
        terms=terms,
        queries=tuple(outcomes.values()),
        changes=changes,
        pending_queries=pending_queries,
        new_query_count=new_queries,
    )
