"""Exact source preservation, fingerprinting and input validation."""

import hashlib
import unicodedata

import pytest
from pydantic import ValidationError

from pipeline.glossary.source import NoticeGlossaryInput, notice_content_revision, source_hash


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


def test_source_and_hash_preserve_whitespace_unicode_and_notice_id() -> None:
    text = " \r\n📌 금회\t신청  \n"
    source = NoticeGlossaryInput(notice_id=42, text=text)
    assert source.text == text
    assert source_hash(source) == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert source_hash(source) == source_hash(text)
    assert source_hash(source) == source_hash(NoticeGlossaryInput(notice_id=3, text=text))
    assert source_hash(text) != source_hash(text.strip())
    assert source_hash(text) != source_hash(unicodedata.normalize("NFC", text))


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


def test_models_are_frozen_and_forbid_unknown_fields() -> None:
    source = NoticeGlossaryInput(text="익일")
    with pytest.raises(ValidationError):
        source.text = "금회"
    with pytest.raises(ValidationError):
        NoticeGlossaryInput.model_validate({**source.model_dump(), "unexpected": "value"})
