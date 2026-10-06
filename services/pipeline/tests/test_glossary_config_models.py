"""Check private key loading, stable sense identities and source validation."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pipeline.glossary.config import GlossaryConfigurationError, GlossarySettings
from pipeline.glossary.models import GlossaryEntry, GlossaryLookup, normalize_query


def entry(**changes: object) -> GlossaryEntry:
    return GlossaryEntry(
        **{
            "provider": "opendict",
            "entry_id": "10",
            "sense_id": "001",
            "headword": "익일",
            "definition": "어느 날의 다음 날.",
            "source_url": "https://opendict.korean.go.kr/dictionary/view?sense_no=10",
            **changes,
        }
    )


@pytest.fixture(autouse=True)
def isolate_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ONTERM_API_KEY", "OPENDICT_API_KEY", "KRDICT_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_settings_keep_keys_private_and_environment_wins(tmp_path, monkeypatch) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "OPENDICT_API_KEY='local-secret'\nexport ONTERM_API_KEY=second-secret # local only\n"
        "GEMINI_API_KEY=unrelated-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENDICT_API_KEY", "environment-secret")
    settings = GlossarySettings.from_env(path)
    assert settings.key_for("opendict") == "environment-secret"
    assert settings.key_for("onterm") == "second-secret"
    assert "secret" not in repr(settings)


def test_missing_fallback_key_is_required_only_when_used(tmp_path) -> None:
    path = tmp_path / ".env"
    path.write_text("ONTERM_API_KEY=primary\n", encoding="utf-8")
    settings = GlossarySettings.from_env(path)
    assert settings.key_for("onterm") == "primary"
    with pytest.raises(GlossaryConfigurationError, match="OPENDICT_API_KEY"):
        settings.key_for("opendict")


def test_missing_file_and_wrong_file_encoding_are_safe(tmp_path) -> None:
    path = tmp_path / ".env"
    settings = GlossarySettings.from_env(path)
    with pytest.raises(GlossaryConfigurationError, match="OPENDICT_API_KEY"):
        settings.key_for("opendict")
    path.write_bytes(b"OPENDICT_API_KEY=secret\xff")
    with pytest.raises(GlossaryConfigurationError) as captured:
        GlossarySettings.from_env(path)
    assert "secret" not in str(captured.value)


def test_only_two_issued_keys_from_environment_need_no_readable_dotenv(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENDICT_API_KEY", "first")
    monkeypatch.setenv("ONTERM_API_KEY", "second")
    path = tmp_path / ".env"
    path.write_bytes(b"invalid\xff")
    settings = GlossarySettings.from_env(path)
    assert settings.key_for("opendict") == "first"
    assert settings.key_for("onterm") == "second"


def test_onterm_key_and_quoted_trailing_comments_are_loaded_without_exposure(tmp_path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        'ONTERM_API_KEY="onterm-fake" # API key\n'
        "OPENDICT_API_KEY='opendict#fake' # keep internal hash\n",
        encoding="utf-8",
    )
    settings = GlossarySettings.from_env(path)
    assert settings.key_for("onterm") == "onterm-fake"
    assert settings.key_for("opendict") == "opendict#fake"
    assert "fake" not in repr(settings)


def test_legacy_krdict_settings_are_ignored_without_loading_or_requiring_a_key(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "ONTERM_API_KEY=primary\nOPENDICT_API_KEY=fallback\n"
        'KRDICT_API_KEY="legacy-secret-with-unclosed-quote\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KRDICT_API_KEY", "environment-legacy-secret")
    settings = GlossarySettings.from_env(path)
    assert settings.key_for("onterm") == "primary"
    assert settings.key_for("opendict") == "fallback"
    assert not hasattr(settings, "krdict_api_key")
    with pytest.raises(GlossaryConfigurationError, match="지원하지 않는") as caught:
        settings.key_for("krdict")
    assert "secret" not in repr(settings)
    assert "secret" not in str(caught.value)


def test_legacy_krdict_data_remains_valid_without_active_api_settings() -> None:
    legacy_entry = entry(
        provider="krdict",
        sense_id="1",
        source_url="https://krdict.korean.go.kr/dicSearch/SearchView?ParaWordNo=10",
    )
    cached = GlossaryLookup(
        query="익일",
        status="found",
        entries=(legacy_entry,),
        providers_checked=("krdict",),
        queried_at=datetime(2026, 10, 6, 12, tzinfo=UTC),
        from_cache=True,
    )
    restored = GlossaryLookup.model_validate_json(cached.model_dump_json())
    assert restored == cached
    assert restored.entries[0].provider == "krdict"
    assert restored.entries[0].definition == "어느 날의 다음 날."


@pytest.mark.parametrize("value", ['"fake-key', '"fake-key" unexpected'])
def test_invalid_quoted_key_is_rejected_without_echoing_value(tmp_path, value) -> None:
    path = tmp_path / ".env"
    path.write_text(f"ONTERM_API_KEY={value}\n", encoding="utf-8")
    with pytest.raises(GlossaryConfigurationError) as captured:
        GlossarySettings.from_env(path)
    assert "fake-key" not in str(captured.value)


def test_onterm_refinements_keep_source_and_content_identity() -> None:
    refined = GlossaryEntry(
        provider="onterm",
        entry_id="sha256:" + "a" * 64,
        sense_id="content",
        entry_id_kind="content_hash",
        headword="피투피",
        easy_terms=("개인간통신",),
        source_institution="한국전력공사",
        source_glossary="전력 용어집",
        source_url="https://kli.korean.go.kr/term/",
    )
    assert refined.definition is None
    assert refined.license == "KOGL 1"
    assert refined.source_name == "국립국어원 온용어"
    for changes in (
        {"easy_terms": ()},
        {"entry_id_kind": "provider_id"},
        {"license": "CC BY-SA 2.0 KR"},
        {"source_institution": None},
    ):
        with pytest.raises(ValidationError):
            GlossaryEntry.model_validate({**refined.model_dump(), **changes})


def test_unicode_whitespace_normalization_preserves_word_boundaries() -> None:
    assert normalize_query("  경정  청구 \n") == "경정 청구"
    assert normalize_query("금회") == "금회"
    assert normalize_query("경정청구") != normalize_query("경정 청구")


@pytest.mark.parametrize("query", [None, 42, "", " \n ", "x" * 201, "a\x00b"])
def test_invalid_queries_fail(query) -> None:
    with pytest.raises(ValueError):
        normalize_query(query)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/definition",
        "https://opendict.korean.go.kr.evil.test/x",
        "https://user:secret@opendict.korean.go.kr/x",
        "javascript:alert(1)",
        "https://opendict.korean.go.kr/x?key=secret",
    ],
)
def test_source_does_not_accept_wrong_host_or_credentials(url: str) -> None:
    with pytest.raises(ValidationError):
        entry(source_url=url)


def test_definitions_do_not_automatically_become_easy_terms() -> None:
    result = entry()
    assert result.easy_terms == ()
    assert result.source_name == "국립국어원 우리말샘"
    assert result.identity == ("opendict", "10", "001")
    assert result.license == "CC BY-SA 2.0 KR"


def test_invalid_source_does_not_echo_a_credential_in_validation_errors() -> None:
    with pytest.raises(ValidationError) as captured:
        entry(source_url="https://opendict.korean.go.kr/x?key=fake-review-secret")
    assert "fake-review-secret" not in str(captured.value)


def test_lookup_rejects_duplicate_senses_and_unchecked_source() -> None:
    values = {
        "query": "익일",
        "status": "found",
        "providers_checked": ("opendict",),
        "queried_at": datetime.now(UTC),
    }
    with pytest.raises(ValidationError, match="중복"):
        GlossaryLookup(**values, entries=(entry(), entry()))
    with pytest.raises(ValidationError, match="조회하지 않은"):
        GlossaryLookup(**{**values, "providers_checked": ("krdict",)}, entries=(entry(),))


def test_lookup_separates_no_result_and_invalid_found() -> None:
    values = {
        "query": "익일",
        "providers_checked": ("opendict", "krdict"),
        "queried_at": datetime.now(UTC),
    }
    assert GlossaryLookup(**values, status="not_found").entries == ()
    with pytest.raises(ValidationError, match="일치하지"):
        GlossaryLookup(**values, status="found")
    with pytest.raises(ValidationError, match="시간대"):
        GlossaryLookup(**{**values, "queried_at": datetime(2026, 10, 5)}, status="not_found")
