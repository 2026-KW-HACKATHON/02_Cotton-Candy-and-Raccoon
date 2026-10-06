"""Test cache reuse, dictionary fallback and preservation on failure offline."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, call

import pytest

from pipeline.glossary.config import GlossarySettings
from pipeline.glossary.models import GlossaryEntry, GlossaryLookup, NormInfo
from pipeline.glossary.service import lookup_glossary, query_glossary

NOW = datetime(2026, 10, 5, 2, tzinfo=UTC)


@pytest.fixture(autouse=True)
def default_onterm(monkeypatch):
    """Keep existing dictionary cases independent of credentials and the network."""
    client = MagicMock()
    client.__enter__.return_value = client
    client.lookup.return_value = ()
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr("pipeline.glossary.service.OnTermClient", constructor)
    monkeypatch.setattr(
        "pipeline.glossary.service.GlossarySettings.from_env",
        lambda: GlossarySettings(onterm_api_key="onterm-fake"),
    )
    return client, constructor


def entry(provider="opendict", sense_id="001") -> GlossaryEntry:
    host = "opendict" if provider == "opendict" else "krdict"
    return GlossaryEntry(
        provider=provider,
        entry_id="10",
        sense_id=sense_id,
        headword="익일",
        definition="어느 날의 다음 날.",
        source_url=f"https://{host}.korean.go.kr/entry/10",
    )


def result(entries=(), providers=("opendict",), **changes) -> GlossaryLookup:
    return GlossaryLookup(
        **{
            "query": "익일",
            "status": "found" if entries else "not_found",
            "entries": entries,
            "providers_checked": providers,
            "queried_at": NOW,
            "from_cache": True,
            **changes,
        }
    )


@pytest.fixture
def store(monkeypatch):
    rows = {}
    calls = []

    def get(conn, query):
        return rows.get(query)

    def save(conn, lookup):
        calls.append(lookup)
        rows[lookup.query] = lookup.model_copy(update={"from_cache": True})

    monkeypatch.setattr("pipeline.glossary.service.get_glossary", get)
    monkeypatch.setattr("pipeline.glossary.service.save_glossary", save)
    return rows, calls


def test_cache_is_reused_indefinitely_without_reading_keys(store, monkeypatch) -> None:
    rows, calls = store
    rows["익일"] = result((entry(),), queried_at=datetime(2000, 1, 1, tzinfo=UTC))
    onterm, primary = MagicMock(), MagicMock()
    config = MagicMock(side_effect=AssertionError("must not read credentials"))
    monkeypatch.setattr("pipeline.glossary.service.GlossarySettings.from_env", config)
    assert (
        lookup_glossary(
            MagicMock(),
            " 익일 ",
            onterm_client=onterm,
            opendict_client=primary,
        )
        == rows["익일"]
    )
    primary.lookup.assert_not_called()
    onterm.lookup.assert_not_called()
    assert calls == []


def test_opendict_success_preserves_multiple_senses_after_empty_onterm(
    store, default_onterm
) -> None:
    _, calls = store
    primary = MagicMock()
    primary.lookup.return_value = (entry(sense_id="001"), entry(sense_id="002"))
    conn = MagicMock()
    returned = lookup_glossary(
        conn,
        "익일",
        opendict_client=primary,
        clock=lambda: NOW,
    )
    assert len(returned.entries) == 2
    assert returned.providers_checked == ("onterm", "opendict")
    assert returned.from_cache is False
    assert len(calls) == 1
    default_onterm[0].lookup.assert_called_once_with("익일")
    primary.lookup.assert_called_once_with("익일")
    conn.commit.assert_not_called()


def test_empty_primary_search_queries_fallback_and_saves_its_source(store) -> None:
    primary, fallback = MagicMock(), MagicMock()
    timeline = MagicMock()
    timeline.attach_mock(primary, "onterm")
    timeline.attach_mock(fallback, "opendict")
    primary.lookup.return_value = ()
    fallback.lookup.return_value = (entry(),)
    returned = lookup_glossary(
        MagicMock(),
        "익일",
        onterm_client=primary,
        opendict_client=fallback,
    )
    assert returned.providers_checked == ("onterm", "opendict")
    assert returned.entries[0].provider == "opendict"
    primary.lookup.assert_called_once_with("익일")
    fallback.lookup.assert_called_once_with("익일")
    assert timeline.mock_calls == [call.onterm.lookup("익일"), call.opendict.lookup("익일")]


def test_both_active_empty_searches_are_saved_and_reused_as_not_found(store) -> None:
    rows, calls = store
    primary, fallback = MagicMock(), MagicMock()
    primary.lookup.return_value = fallback.lookup.return_value = ()
    returned = lookup_glossary(
        MagicMock(),
        "익일",
        onterm_client=primary,
        opendict_client=fallback,
    )
    assert returned.status == "not_found"
    assert returned.providers_checked == ("onterm", "opendict")
    assert rows["익일"].entries == ()
    assert len(calls) == 1
    primary.lookup.assert_called_once_with("익일")
    fallback.lookup.assert_called_once_with("익일")
    primary.lookup.side_effect = fallback.lookup.side_effect = AssertionError(
        "cache must be reused"
    )
    cached = lookup_glossary(
        MagicMock(),
        "익일",
        onterm_client=primary,
        opendict_client=fallback,
    )
    assert cached == rows["익일"]
    assert cached.status == "not_found" and cached.from_cache
    assert len(calls) == 1


@pytest.mark.parametrize("failed_provider", ["onterm", "opendict"])
def test_failure_preserves_previous_cache_and_never_saves_partial_result(
    store,
    failed_provider,
) -> None:
    rows, calls = store
    old = result((entry(),))
    rows["익일"] = old
    primary, fallback = MagicMock(), MagicMock()
    primary.lookup.return_value = ()
    (primary if failed_provider == "onterm" else fallback).lookup.side_effect = RuntimeError(
        "offline API failure"
    )
    with pytest.raises(RuntimeError, match="API failure"):
        lookup_glossary(
            MagicMock(),
            "익일",
            refresh=True,
            onterm_client=primary,
            opendict_client=fallback,
        )
    assert rows["익일"] is old
    assert calls == []
    if failed_provider == "onterm":
        fallback.lookup.assert_not_called()


def test_manual_refresh_queries_again_even_when_cache_exists(store) -> None:
    rows, calls = store
    rows["익일"] = result((entry(),))
    primary = MagicMock()
    primary.lookup.return_value = (entry(sense_id="002"),)
    returned = lookup_glossary(
        MagicMock(),
        "익일",
        refresh=True,
        opendict_client=primary,
    )
    assert returned.entries[0].sense_id == "002"
    assert len(calls) == 1


def test_save_failure_is_not_returned_as_a_success(store, monkeypatch) -> None:
    rows, _ = store
    old = result((entry(),))
    rows["익일"] = old
    primary = MagicMock()
    primary.lookup.return_value = (entry(sense_id="002"),)
    save = MagicMock(side_effect=RuntimeError("offline DB failure"))
    monkeypatch.setattr("pipeline.glossary.service.save_glossary", save)
    with pytest.raises(RuntimeError, match="DB failure"):
        lookup_glossary(MagicMock(), "익일", refresh=True, opendict_client=primary)
    assert rows["익일"] is old


def test_mismatched_provider_never_reaches_storage(store) -> None:
    _, calls = store
    primary = MagicMock()
    primary.lookup.return_value = (entry("krdict"),)
    with pytest.raises(ValueError, match="출처"):
        lookup_glossary(MagicMock(), "익일", opendict_client=primary)
    assert calls == []


def test_lazy_owned_client_closes_but_injected_client_does_not(store, monkeypatch) -> None:
    client = MagicMock()
    client.__enter__.return_value = client
    client.lookup.return_value = (entry(),)
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr("pipeline.glossary.service.DictionaryClient", constructor)
    settings = GlossarySettings(opendict_api_key="test-key", onterm_api_key="onterm-fake")
    lookup_glossary(MagicMock(), "익일", settings=settings)
    constructor.assert_called_once_with("opendict", "test-key")
    client.__exit__.assert_called_once()


def test_empty_owned_searches_require_only_onterm_and_opendict_keys(
    store, default_onterm, monkeypatch
) -> None:
    settings = GlossarySettings(onterm_api_key="onterm-fake", opendict_api_key="opendict-fake")
    assert not hasattr(settings, "krdict_api_key")
    client = MagicMock()
    client.__enter__.return_value = client
    client.lookup.return_value = ()
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr("pipeline.glossary.service.DictionaryClient", constructor)
    monkeypatch.setattr("pipeline.glossary.service.GlossarySettings.from_env", lambda: settings)
    returned = lookup_glossary(MagicMock(), "익일")
    assert returned.status == "not_found"
    assert returned.providers_checked == ("onterm", "opendict")
    default_onterm[1].assert_called_once_with("onterm-fake")
    constructor.assert_called_once_with("opendict", "opendict-fake")
    client.lookup.assert_called_once_with("익일")
    client.__exit__.assert_called_once()


def test_three_provider_legacy_cache_excludes_krdict_without_active_requests(store) -> None:
    rows, calls = store
    old = result((entry("krdict", "1"),), providers=("onterm", "opendict", "krdict"))
    rows["익일"] = old
    onterm, opendict = MagicMock(), MagicMock()
    returned = lookup_glossary(
        MagicMock(),
        "익일",
        onterm_client=onterm,
        opendict_client=opendict,
    )
    assert returned.status == "not_found" and returned.entries == ()
    assert returned.providers_checked == ("onterm", "opendict")
    assert returned.from_cache
    onterm.lookup.assert_not_called()
    opendict.lookup.assert_not_called()
    assert rows["익일"] is old and old.entries[0].provider == "krdict"
    assert calls == []


def test_excluded_only_legacy_cache_requires_complete_active_lookup(store) -> None:
    rows, calls = store
    rows["익일"] = result((entry("krdict", "1"),), providers=("krdict",))
    onterm, opendict = MagicMock(), MagicMock()
    onterm.lookup.return_value = opendict.lookup.return_value = ()
    returned = lookup_glossary(
        MagicMock(),
        "익일",
        onterm_client=onterm,
        opendict_client=opendict,
    )
    onterm.lookup.assert_called_once_with("익일")
    opendict.lookup.assert_called_once_with("익일")
    assert returned.status == "not_found" and returned.providers_checked == ("onterm", "opendict")
    assert len(calls) == 1


def test_api_only_lookup_needs_no_database(monkeypatch) -> None:
    forbidden_db = MagicMock(side_effect=AssertionError("API-only must not touch the DB"))
    monkeypatch.setattr("pipeline.glossary.service.get_glossary", forbidden_db)
    monkeypatch.setattr("pipeline.glossary.service.save_glossary", forbidden_db)
    primary = MagicMock()
    primary.lookup.return_value = (entry(),)
    response = query_glossary(
        " 익일 ",
        opendict_client=primary,
        clock=lambda: NOW,
    )
    assert response.query == "익일"
    assert response.queried_at == NOW
    assert response.from_cache is False
    assert response.providers_checked == ("onterm", "opendict")


def test_storage_preservation_error_is_reported_after_empty_refresh(store, monkeypatch) -> None:
    rows, _ = store
    old = result((entry(),))
    rows["익일"] = old
    primary, fallback = MagicMock(), MagicMock()
    primary.lookup.return_value = fallback.lookup.return_value = ()
    from pipeline.storage.glossary import GlossaryStorageError

    monkeypatch.setattr(
        "pipeline.glossary.service.save_glossary",
        MagicMock(side_effect=GlossaryStorageError("기존 용어 설명을 보존했습니다.")),
    )
    with pytest.raises(GlossaryStorageError, match="보존"):
        lookup_glossary(
            MagicMock(),
            "익일",
            refresh=True,
            onterm_client=primary,
            opendict_client=fallback,
        )
    assert rows["익일"] is old


def test_explicit_onterm_refinement_is_preferred_and_saved_with_its_source(store) -> None:
    rows, calls = store
    refined = GlossaryEntry(
        provider="onterm",
        entry_id="sha256:" + "b" * 64,
        sense_id="content",
        entry_id_kind="content_hash",
        headword="익일",
        easy_terms=("다음 날",),
        source_institution="예시 기관",
        source_glossary="예시 용어집",
        source_url="https://kli.korean.go.kr/term/",
    )
    onterm, primary = MagicMock(), MagicMock()
    onterm.lookup.return_value = (refined,)
    returned = lookup_glossary(
        MagicMock(),
        "익일",
        onterm_client=onterm,
        opendict_client=primary,
        clock=lambda: NOW,
    )
    assert returned.providers_checked == ("onterm",)
    assert returned.entries == (refined,)
    assert rows["익일"].entries[0].entry_id_kind == "content_hash"
    assert len(calls) == 1
    primary.lookup.assert_not_called()
    onterm.close.assert_not_called()


def test_onterm_failure_is_not_treated_as_no_refinement(store) -> None:
    rows, calls = store
    previous = result((entry(),))
    rows["익일"] = previous
    onterm, primary = MagicMock(), MagicMock()
    onterm.lookup.side_effect = TimeoutError("offline timeout")
    with pytest.raises(TimeoutError):
        lookup_glossary(
            MagicMock(),
            "익일",
            refresh=True,
            onterm_client=onterm,
            opendict_client=primary,
        )
    assert rows["익일"] is previous
    assert calls == []
    primary.lookup.assert_not_called()


@pytest.mark.parametrize("mode", ["cache", "fresh", "refresh"])
@pytest.mark.parametrize(
    ("query", "headword", "description", "role", "expected"),
    [
        (
            "익일",
            "익일",
            "‘익일’ 대신 될 수 있으면 순화한 용어 ‘다음날’, ‘이튿날’을 쓰라고 되어 있다.",
            "일본어 투 생활 용어 순화 고시 자료(문화체육부 고시 제1997-19호, 1997년 2월 15일)",
            ("다음날", "이튿날"),
        ),
        (
            "선착순",
            "선착-순",
            "‘선착순’과 ‘먼저 온 차례’, ‘온 차례’를 함께 쓸 수 있다고 되어 있다.",
            "행정 용어 순화 편람(1993년 2월 12일)",
            ("먼저 온 차례", "온 차례"),
        ),
        (
            "엄수",
            "엄수",
            "‘엄수’ 대신 될 수 있으면 순화한 용어 ‘꼭 지킴’을 쓰라고 되어 있다.",
            "생활 용어 수정 보완 고시 자료(문화체육부 고시 제1996-13호, 1996년 3월 23일)",
            ("꼭 지킴",),
        ),
    ],
)
def test_standalone_lookup_reparses_official_notes_and_preserves_provenance(
    store, monkeypatch, mode, query, headword, description, role, expected
) -> None:
    rows, calls = store
    raw_entry = GlossaryEntry(
        provider="opendict",
        entry_id="123",
        sense_id="001",
        headword=headword,
        definition="저장된 공식 사전 뜻풀이.",
        part_of_speech="명사",
        original_language="原語",
        norm_info=(
            NormInfo(type="순화", role=role, description=description),
            NormInfo(type="참고", description="저장된 참고 원자료."),
        ),
        source_url="https://opendict.korean.go.kr/dictionary/view?sense_no=123",
    )
    old_time = datetime(2000, 1, 1, tzinfo=UTC)
    raw_cache = GlossaryLookup(
        query=query,
        status="found",
        entries=(raw_entry,),
        providers_checked=("onterm", "opendict"),
        queried_at=old_time,
        from_cache=True,
    )
    if mode != "fresh":
        rows[query] = raw_cache
    onterm, opendict = MagicMock(), MagicMock()
    onterm.lookup.return_value = ()
    opendict.lookup.return_value = (raw_entry,)
    forbidden_config = MagicMock(side_effect=AssertionError("must not read credentials"))
    monkeypatch.setattr("pipeline.glossary.service.GlossarySettings.from_env", forbidden_config)
    conn = MagicMock()
    returned = lookup_glossary(
        conn,
        query,
        refresh=mode == "refresh",
        onterm_client=onterm,
        opendict_client=opendict,
        clock=lambda: NOW,
    )
    assert returned.entries[0].easy_terms == expected
    assert returned.entries[0].model_dump(exclude={"easy_terms"}) == raw_entry.model_dump(
        exclude={"easy_terms"}
    )
    assert returned.query == query
    assert returned.providers_checked == ("onterm", "opendict")
    assert returned.queried_at == (old_time if mode == "cache" else NOW)
    assert returned.from_cache is (mode == "cache")
    assert raw_entry.easy_terms == ()
    forbidden_config.assert_not_called()
    conn.commit.assert_not_called()
    conn.close.assert_not_called()
    if mode == "cache":
        assert rows[query] is raw_cache
        assert rows[query].entries[0].easy_terms == ()
        assert calls == []
        onterm.lookup.assert_not_called()
        opendict.lookup.assert_not_called()
    else:
        assert len(calls) == 1
        assert calls[0].entries[0].easy_terms == expected
        assert rows[query].entries[0].easy_terms == expected
        onterm.lookup.assert_called_once_with(query)
        opendict.lookup.assert_called_once_with(query)


def test_standalone_cache_reparse_removes_unsafe_old_candidate_without_writing(store) -> None:
    rows, calls = store
    raw_entry = entry().model_copy(
        update={
            "easy_terms": ("다음날",),
            "norm_info": (NormInfo(type="순화", description="행정 분야일 때 ‘다음날’로 순화."),),
        }
    )
    raw_cache = result((raw_entry,), providers=("onterm", "opendict"))
    rows["익일"] = raw_cache
    forbidden = MagicMock()
    forbidden.lookup.side_effect = AssertionError("cached interpretation needs no network")
    returned = lookup_glossary(
        MagicMock(), "익일", onterm_client=forbidden, opendict_client=forbidden
    )
    assert returned.entries[0].easy_terms == ()
    assert returned.entries[0].norm_info == raw_entry.norm_info
    assert returned.queried_at == raw_cache.queried_at and returned.from_cache
    assert rows["익일"] is raw_cache and raw_cache.entries[0].easy_terms == ("다음날",)
    assert calls == []
    forbidden.lookup.assert_not_called()


def test_standalone_fresh_readback_reparses_a_newer_saved_snapshot(monkeypatch) -> None:
    raw_entry = entry().model_copy(
        update={"norm_info": (NormInfo(type="순화", description="‘익일’을 ‘다음 날’로 순화."),)}
    )
    newer_time = datetime(2026, 10, 6, tzinfo=UTC)
    newer_saved = result((raw_entry,), providers=("onterm", "opendict"), queried_at=newer_time)
    # Storage can keep a newer concurrent write instead of the just-fetched response.
    get = MagicMock(side_effect=[None, newer_saved])
    save = MagicMock()
    monkeypatch.setattr("pipeline.glossary.service.get_glossary", get)
    monkeypatch.setattr("pipeline.glossary.service.save_glossary", save)
    onterm, opendict = MagicMock(), MagicMock()
    onterm.lookup.return_value = ()
    opendict.lookup.return_value = (entry(),)
    returned = lookup_glossary(
        MagicMock(), "익일", onterm_client=onterm, opendict_client=opendict, clock=lambda: NOW
    )
    assert returned.entries[0].easy_terms == ("다음 날",)
    assert returned.entries[0].norm_info == newer_saved.entries[0].norm_info
    assert returned.queried_at == newer_time
    assert returned.from_cache is False
    assert newer_saved.entries[0].easy_terms == () and newer_saved.from_cache
    assert get.call_count == 2
    save.assert_called_once()
