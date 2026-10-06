"""Regressions for semantic scope, retry starvation and concurrent lock ordering."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from psycopg.pq import TransactionStatus
from pydantic import ValidationError

from pipeline.glossary import notice_service
from pipeline.glossary.client import GlossaryAPIError
from pipeline.glossary.document import NoticeGlossaryResult
from pipeline.glossary.models import GlossaryEntry, GlossaryLookup, NormInfo
from pipeline.glossary.process import process_notice_glossary
from pipeline.glossary.source import NoticeGlossaryInput

_TIME = datetime(2026, 10, 6, 12, tzinfo=UTC)


def _lookup(query: str, entries=()) -> GlossaryLookup:
    return GlossaryLookup(
        query=query,
        entries=entries,
        status="found" if entries else "not_found",
        providers_checked=("onterm",),
        queried_at=_TIME,
    )


@pytest.mark.parametrize(
    "norm_info",
    [
        (NormInfo(type="다듬은 말", role="건설", description="다듬은 말: 굳히기"),),
        (NormInfo(type="전문 분야", description="건설"),),
    ],
)
def test_professional_field_metadata_alone_allows_official_refinement(
    norm_info,
) -> None:
    scoped = GlossaryEntry(
        provider="onterm",
        entry_id="sha256:" + "a" * 64,
        entry_id_kind="content_hash",
        sense_id="content",
        headword="양생",
        definition="건축 재료를 굳히는 일.",
        easy_terms=("굳히기",),
        norm_info=norm_info,
        source_institution="테스트 기관",
        source_glossary="건설 전문 용어",
        source_url="https://kli.korean.go.kr/term/record",
    )
    original = "콘크리트 양생 안내"
    result = process_notice_glossary(
        NoticeGlossaryInput(text=original),
        lambda query: _lookup(query, (scoped,) if query == "양생" else ()),
    )
    assert result.original_text == original
    assert result.easy_text == "콘크리트 굳히기 안내"
    assert result.terms[1].status == "replaced"
    assert result.terms[1].entries == (scoped,)
    assert result.terms[1].replacement == "굳히기"
    assert len(result.changes) == 1


@pytest.mark.parametrize(
    "norm",
    [
        NormInfo(type="다듬은 말", description="건설 분야에서만 굳히기로 다듬는다."),
        NormInfo(type="다듬은 말", description="건설 재료인 경우 굳히기로 다듬는다."),
        NormInfo(type="다듬은 말", description="건설 재료에 한정해 굳히기로 다듬는다."),
        NormInfo(type="다듬은 말", role="건설 분야에서만 사용", description="(다듬은 말, 굳히기)"),
        NormInfo(
            type="다듬은 말", role="건설 재료에 한정해 적용", description="(다듬은 말, 굳히기)"
        ),
        NormInfo(type="전문 분야", description="콘크리트에 한함"),
        NormInfo(type="전문 분야", description="콘크리트에 국한"),
        NormInfo(type="다듬은 말", description="콘크리트에 한하여 굳히기로 다듬는다."),
        NormInfo(type="다듬은 말", description="사용 범위를 콘크리트에 국한하여 적용한다."),
        NormInfo(type="다듬은 말", description="콘크리트일 때 굳히기로 다듬는다."),
        NormInfo(type="전문 분야", description="콘크리트에 한한다."),
    ],
    ids=[
        "description-field-only",
        "description-if",
        "description-limited",
        "role-field-only",
        "role-limited",
        "category-limited-hanham",
        "category-limited-gukhan",
        "description-only-hanhayeo",
        "description-limited-gukhanyeo",
        "description-when",
        "category-limited-hanhanda",
    ],
)
def test_explicit_refinement_usage_conditions_preserve_original_and_evidence(norm) -> None:
    scoped = GlossaryEntry(
        provider="onterm",
        entry_id="sha256:" + "a" * 64,
        entry_id_kind="content_hash",
        sense_id="content",
        headword="양생",
        definition="건축 재료를 굳히는 일.",
        easy_terms=("굳히기",),
        norm_info=(norm,),
        source_institution="테스트 기관",
        source_glossary="건설 전문 용어",
        source_url="https://kli.korean.go.kr/term/record",
    )
    original = "화초 양생 안내"
    result = process_notice_glossary(
        NoticeGlossaryInput(text=original),
        lambda query: _lookup(query, (scoped,) if query == "양생" else ()),
    )
    assert result.original_text == result.easy_text == original
    assert result.terms[1].status == "ambiguous"
    assert result.terms[1].entries == (scoped,)
    assert result.changes == ()


@pytest.mark.parametrize(
    "target,description",
    [
        ("경우", "(다듬은 말, 경우)"),
        ("경우", "‘케이스’를 ‘경우’로 다듬음."),
        ("경우", "다듬은 말: 경우"),
        ("경우의 수", "‘케이스 수’를 ‘경우의 수’로 다듬음."),
        ("한정", "(다듬은 말, 한정)"),
        ("특정 경우", "‘케이스’를 ‘특정 경우’로 다듬음."),
        ("사용할 때", "‘케이스’를 ‘사용할 때’로 다듬음."),
    ],
)
def test_official_target_mentions_are_not_usage_conditions(target, description) -> None:
    refined = GlossaryEntry(
        provider="onterm",
        entry_id="sha256:" + "a" * 64,
        entry_id_kind="content_hash",
        sense_id="content",
        headword="케이스",
        easy_terms=(target,),
        norm_info=(NormInfo(type="다듬은 말", description=description),),
        source_institution="테스트 기관",
        source_glossary="테스트 용어집",
        source_url="https://kli.korean.go.kr/term/record",
    )
    original = "케이스 안내: 케이스"
    result = process_notice_glossary(
        NoticeGlossaryInput(text=original),
        lambda query: _lookup(query, (refined,) if query == "케이스" else ()),
    )
    assert result.original_text == original
    assert result.easy_text == f"{target} 안내: {target}"
    assert result.terms[0].status == "replaced"
    assert result.terms[0].entries == (refined,)


@pytest.mark.parametrize(
    "condition",
    [
        "(다듬은 말, 경우) 콘크리트에 한함",
        "‘케이스’를 ‘경우’로 다듬음. 콘크리트인 경우에만 적용한다.",
        "‘경우’인 경우에 한함.",
        "다듬은 말: 경우. 콘크리트에 국한하여 사용한다.",
        "‘케이스’를 ‘경우’로 다듬음. 경우에 따라 적용한다.",
        "‘케이스’를 ‘경우’로 다듬음. ‘경우’에 따라 적용한다.",
        "‘케이스’를 ‘경우’로 다듬음. '경우'에 따라 적용한다.",
        '‘케이스’를 ‘경우’로 다듬음. "경우"에 따라 적용한다.',
        "‘케이스’를 ‘경우’로 다듬음. ‘경우’라면 적용한다.",
    ],
)
def test_quoted_official_target_does_not_hide_surrounding_scope_conditions(condition) -> None:
    restricted = GlossaryEntry(
        provider="onterm",
        entry_id="sha256:" + "a" * 64,
        entry_id_kind="content_hash",
        sense_id="content",
        headword="케이스",
        easy_terms=("경우",),
        norm_info=(NormInfo(type="다듬은 말", description=condition),),
        source_institution="테스트 기관",
        source_glossary="테스트 용어집",
        source_url="https://kli.korean.go.kr/term/record",
    )
    original = "케이스 안내"
    result = process_notice_glossary(
        NoticeGlossaryInput(text=original),
        lambda query: _lookup(query, (restricted,) if query == "케이스" else ()),
    )
    assert result.original_text == result.easy_text == original
    assert result.terms[0].status == "ambiguous"
    assert result.terms[0].entries == (restricted,)
    assert result.changes == ()


@pytest.mark.parametrize(
    "target,condition",
    [
        ("특정 경우", "‘특정 경우’에 따라 적용한다."),
        ("한정", "‘한정’하여 사용한다."),
    ],
)
def test_scope_spanning_quoted_target_and_following_clause_preserves_original(
    target, condition
) -> None:
    restricted = GlossaryEntry(
        provider="onterm",
        entry_id="sha256:" + "a" * 64,
        entry_id_kind="content_hash",
        sense_id="content",
        headword="케이스",
        easy_terms=(target,),
        norm_info=(NormInfo(type="다듬은 말", description=f"‘{target}’로 다듬음. {condition}"),),
        source_institution="테스트 기관",
        source_glossary="테스트 용어집",
        source_url="https://kli.korean.go.kr/term/record",
    )
    source = NoticeGlossaryInput(text="케이스 안내")
    result = process_notice_glossary(
        source,
        lambda query: _lookup(query, (restricted,) if query == "케이스" else ()),
    )
    assert result.original_text == result.easy_text == source.text
    assert result.terms[0].status == "ambiguous"
    assert result.terms[0].entries == (restricted,)
    assert result.changes == ()


@pytest.mark.parametrize("norm_type", ["순화", "순화 정보", "순화어"])
@pytest.mark.parametrize("scope_field", [None, "description", "role"])
def test_supported_ourmalsam_refinement_types_keep_their_scope_clauses(
    norm_type, scope_field
) -> None:
    records = [NormInfo(type=norm_type, description="‘굳히기’로 순화.")]
    if scope_field:
        records.append(NormInfo(type=norm_type, **{scope_field: "콘크리트에 한함"}))
    refined = GlossaryEntry(
        provider="opendict",
        entry_id="123",
        sense_id="1",
        headword="양생",
        definition="건축 재료를 굳히는 일.",
        easy_terms=("굳히기",),
        norm_info=tuple(records),
        source_url="https://opendict.korean.go.kr/word/123",
    )

    def lookup(query):
        entries = (refined,) if query == "양생" else ()
        return GlossaryLookup(
            query=query,
            status="found" if entries else "not_found",
            entries=entries,
            providers_checked=("onterm", "opendict"),
            queried_at=_TIME,
        )

    source = NoticeGlossaryInput(text="화초 양생 안내")
    result = process_notice_glossary(source, lookup)
    assert result.original_text == source.text
    assert result.terms[1].entries[0].norm_info == tuple(records)
    assert result.terms[1].entries[0].easy_terms == ("굳히기",)
    if scope_field:
        assert result.easy_text == source.text
        assert result.terms[1].status == "ambiguous"
        assert result.changes == ()
    else:
        assert result.easy_text == "화초 굳히기 안내"
        assert result.terms[1].status == "replaced"
        assert len(result.changes) == 1


def test_obsolete_scope_replacement_payload_fails_current_rules_validation() -> None:
    unrestricted = GlossaryEntry(
        provider="onterm",
        entry_id="sha256:" + "a" * 64,
        entry_id_kind="content_hash",
        sense_id="content",
        headword="양생",
        easy_terms=("굳히기",),
        source_institution="테스트 기관",
        source_glossary="테스트 용어집",
        source_url="https://kli.korean.go.kr/term/record",
    )
    output = process_notice_glossary(
        NoticeGlossaryInput(text="화초 양생 안내"),
        lambda query: _lookup(query, (unrestricted,) if query == "양생" else ()),
    )
    assert output.easy_text == "화초 굳히기 안내"
    old_payload = output.model_dump(mode="json")
    old_payload["rules_version"] = "dictionary-replacement-v3"
    condition = [{"type": "전문 분야", "description": "콘크리트에 한함"}]
    old_payload["terms"][1]["entries"][0]["norm_info"] = condition
    for outcome in old_payload["queries"]:
        if outcome["query"] == "양생":
            outcome["lookup"]["entries"][0]["norm_info"] = condition
    # v3 admitted this scoped replacement. Current models enforce the repaired
    # rule, so loaders must inspect stale metadata before validating old payloads.
    with pytest.raises(ValidationError):
        NoticeGlossaryResult.model_validate(old_payload)


def test_voucher_field_metadata_preserves_live_refinement_relationship() -> None:
    # Reproduce non-secret fields from the saved single-notice execution demo.
    voucher = GlossaryEntry(
        provider="onterm",
        entry_id="sha256:" + "a" * 64,
        entry_id_kind="content_hash",
        sense_id="content",
        headword="바우처",
        definition=(
            "일정한 조건을 갖춘 사람이 교육, 주택, 의료 따위의 복지 서비스를 이용할 때 "
            "정부가 비용을 대신 지급하거나 보조하기 위하여 내놓은 지불 보증서."
        ),
        easy_terms=("이용권",),
        norm_info=(
            NormInfo(type="다듬은 말", role="인문사회학 > 사회", description="(다듬은 말, 이용권)"),
            NormInfo(type="전문 분야", description="인문사회학 > 사회"),
        ),
        source_institution="국어문화원연합회",
        source_glossary="학술 용어집",
        source_url="https://kli.korean.go.kr/term/",
    )
    original = "바우처 안내: 바우처"
    result = process_notice_glossary(
        NoticeGlossaryInput(text=original),
        lambda query: _lookup(query, (voucher,) if query == "바우처" else ()),
    )
    assert result.original_text == original
    assert result.easy_text == "이용권 안내: 이용권"
    assert result.terms[0].status == "replaced"
    assert result.terms[0].entries[0].norm_info == voucher.norm_info
    assert len(result.changes) == 2


def test_shared_failed_stem_cannot_starve_an_unseen_whole_word_across_resumes() -> None:
    calls = []

    def lookup(query):
        calls.append(query)
        if query == "가정":
            raise GlossaryAPIError("opendict", "timeout")
        return _lookup(query)

    source = NoticeGlossaryInput(text="가정에 가정으로")
    result = None
    per_run = []
    for _ in range(4):
        before = len(calls)
        result = process_notice_glossary(source, lookup, previous=result, max_queries=1)
        per_run.append(calls[before:])
        assert result.original_text == result.easy_text == source.text
        assert result.new_query_count == 1
    assert per_run == [["가정에"], ["가정"], ["가정으로"], ["가정"]]
    assert result.pending_queries == ("가정",)
    assert [term.word for term in result.terms] == ["가정에", "가정으로"]


def test_opposite_source_orders_save_glossary_queries_in_the_same_lock_order(monkeypatch) -> None:
    written = []
    monkeypatch.setattr(notice_service, "get_glossary", lambda *args: None)
    monkeypatch.setattr(notice_service, "query_glossary", lambda query, **kwargs: _lookup(query))
    monkeypatch.setattr(
        notice_service, "save_glossary", lambda conn, result: written.append(result.query)
    )
    monkeypatch.setattr(notice_service, "save_notice_glossary", lambda *args: None)
    for text in ("익일 금회", "금회 익일"):
        source = NoticeGlossaryInput(notice_id=42, text=text)
        generated = process_notice_glossary(source, _lookup, clock=lambda: _TIME)
        responses = iter((None, generated))
        monkeypatch.setattr(
            notice_service,
            "get_notice_glossary",
            lambda *a, responses=responses, **kw: next(responses),
        )
        conn = MagicMock()
        conn.autocommit = False
        conn.info.transaction_status = TransactionStatus.INTRANS
        assert notice_service.process_and_store_notice_glossary(conn, source) == generated
        conn.commit.assert_not_called()
        conn.close.assert_not_called()
    assert written == ["금회", "익일", "금회", "익일"]
