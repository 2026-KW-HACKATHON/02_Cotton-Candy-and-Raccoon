"""Pure whole-word probing preserves exact source tokens and lookup priority."""

import socket
import unicodedata

import pytest
from pydantic import ValidationError

from pipeline.glossary.source import NoticeGlossaryInput, TermCandidate, locate_candidates
from pipeline.glossary.tokenize import WordProbe, tokenize_notice


def surfaces(text: str) -> list[str]:
    return [probe.surface for probe in tokenize_notice(NoticeGlossaryInput(text=text))]


def test_repeated_words_deduplicate_in_first_source_order_with_all_token_offsets() -> None:
    text = "📌 익일 접수\r\n금회 접수 익일 안내"
    source = NoticeGlossaryInput(text=text)
    probes = tokenize_notice(source)
    assert [probe.surface for probe in probes] == ["익일", "접수", "금회", "안내"]
    assert [(item.start, item.end) for item in probes[0].occurrences] == [(2, 4), (15, 17)]
    assert [(item.start, item.end) for item in probes[1].occurrences] == [(5, 7), (12, 14)]
    for probe in probes:
        for occurrence in probe.occurrences:
            assert text[occurrence.start : occurrence.end] == occurrence.text == probe.surface
            assert text[occurrence.context_start : occurrence.context_end] == occurrence.context
    assert source.text == text


def test_whole_token_occurrences_do_not_include_a_different_particle_form() -> None:
    text = "가정에 가정에서는 가정에 가정"
    probes = tokenize_notice(NoticeGlossaryInput(text=text))
    assert [probe.surface for probe in probes] == ["가정에", "가정에서는", "가정"]
    assert [occurrence.start for occurrence in probes[0].occurrences] == [0, 10]
    assert [occurrence.start for occurrence in probes[2].occurrences] == [14]
    assert [candidate.surface for candidate in probes[0].candidates] == ["가정에", "가정"]


def test_particle_alternatives_are_literal_prefixes_longest_suffix_first() -> None:
    (probe,) = tokenize_notice(NoticeGlossaryInput(text="가정으로"))
    assert [candidate.surface for candidate in probe.candidates] == ["가정으로", "가정", "가정으"]
    assert [candidate.query for candidate in probe.candidates] == ["가정으로", "가정", "가정으"]
    assert [
        candidate.surface
        for candidate in tokenize_notice(NoticeGlossaryInput(text="익일에"))[0].candidates
    ] == ["익일에", "익일"]


@pytest.mark.parametrize(
    "text",
    ["가정은", "가정에", "가정으로", "가정에서", "가정에게", "가정까지", "가정보다"],
)
def test_every_alternative_aligns_with_the_literal_source_locator(text: str) -> None:
    source = NoticeGlossaryInput(text=text)
    (probe,) = tokenize_notice(source)
    for candidate in probe.candidates:
        (located,) = locate_candidates(source, [candidate])
        assert located.occurrences[0].start == probe.occurrences[0].start == 0
        assert located.occurrences[0].text == candidate.surface


def test_complete_noun_lookup_always_precedes_particle_alternatives() -> None:
    probes = tokenize_notice(NoticeGlossaryInput(text="종이 국가 가정이 종이가"))
    assert [candidate.surface for candidate in probes[0].candidates] == ["종이"]
    assert [candidate.surface for candidate in probes[1].candidates] == ["국가"]
    assert [candidate.surface for candidate in probes[2].candidates] == ["가정이", "가정"]
    assert [candidate.surface for candidate in probes[3].candidates] == ["종이가", "종이"]
    assert all(probe.candidates[0].surface == probe.surface for probe in probes)


def test_no_recursive_particle_removal_verb_stemming_or_arbitrary_substrings() -> None:
    probes = tokenize_notice(NoticeGlossaryInput(text="익일에도 지원합니다 다문화가정"))
    assert [candidate.surface for candidate in probes[0].candidates] == ["익일에도", "익일에"]
    assert [candidate.surface for candidate in probes[1].candidates] == ["지원합니다"]
    assert [candidate.surface for candidate in probes[2].candidates] == ["다문화가정"]
    assert all(candidate.surface != "가정" for probe in probes for candidate in probe.candidates)


def test_urls_and_email_addresses_are_excluded_with_adjacent_prose_retained() -> None:
    text = (
        "문의: user.name+notice@example.co.kr,지원 "
        "https://example.com/가정?value=익일&count=2;접수 "
        "www.example.com/안내 (ftp://example.com/금회) "
        "info@예시.kr 끝"
    )
    assert surfaces(text) == ["문의", "지원", "접수", "끝"]


def test_bare_domains_and_paths_are_reserved_with_korean_prose_punctuation_retained() -> None:
    text = (
        "문의:nowon.kr,지원.접수 news.seoul.go.kr/path?word=익일&count=2;마감 "
        "예시.kr/path 서울.한국에서 안내 (cafe\u0301.fr/가정) 끝"
    )
    assert surfaces(text) == ["문의", "지원", "접수", "마감", "안내", "끝"]


@pytest.mark.parametrize(
    "filename",
    [
        "notice.pdf",
        "공지.hwpx",
        "안내.docx",
        "report.xlsx",
        "양식.hwp를",
        r"C:\folder\notice.docx",
        r"C:\자료\공지.hwp",
        r"\\server\folder\notice.pdf",
        "/folder/notice.pdf",
        "./folder/공지.pdf",
        "folder/notice.docx",
        "cafe\u0301.pdf",
        "notice.pdf.docx",
        '"C:\\자료 폴더\\공지.pdf"',
        r"C:\Program Files\notice.pdf",
    ],
)
def test_known_document_filenames_and_paths_are_excluded(filename: str) -> None:
    assert surfaces(f"첨부:{filename},접수.안내") == ["첨부", "접수", "안내"]


def test_address_recognition_preserves_regular_korean_sentences_dates_and_amounts() -> None:
    text = "신청.접수.마감 안내.끝 2026.10.06까지 1,000원 10.5만원"
    assert surfaces(text) == ["신청", "접수", "마감", "안내", "끝"]


def test_full_size_dotted_plain_text_retains_every_token_occurrence() -> None:
    source = NoticeGlossaryInput(text="a." * 50_000)
    (probe,) = tokenize_notice(source)
    assert probe.surface == "a"
    assert len(probe.occurrences) == 50_000
    assert probe.occurrences[0].start == 0
    assert probe.occurrences[-1].start == 99_998


@pytest.mark.parametrize(
    "text",
    [
        "2026년 10월 6일",
        "2026-10-06 2026.10.06 10/06",
        "100만원 ₩1,000원",
        "20kg 5.5㎞ 30분 10세 ３００원",
        "123-456-7890 +82-02-1234",
        "100%",
    ],
)
def test_numeric_leading_dates_amounts_units_and_phone_numbers_are_skipped(text: str) -> None:
    assert surfaces(text) == []


@pytest.mark.parametrize(
    "amount",
    [
        "월28,000원",
        "월12,000원",
        "월28000원",
        "월 28,000 원",
        "월1.5만원",
        "월12,000원부터",
        "월28,000원은",
        "월２８,０００원",
    ],
)
def test_monthly_won_amounts_do_not_create_letter_leading_numeric_fragments(amount: str) -> None:
    source = NoticeGlossaryInput(text=f"비용:{amount};안내")
    probes = tokenize_notice(source)
    assert [probe.surface for probe in probes] == ["비용", "안내"]
    assert probes[1].occurrences[0].start == len(f"비용:{amount};")
    assert source.text == f"비용:{amount};안내"


def test_monthly_amount_rule_preserves_identifiers_and_words_without_won_units() -> None:
    assert surfaces("제1차 K-패스 A1 월1차 월28 월드28 월28,000원") == [
        "제1차",
        "K-패스",
        "A1",
        "월1차",
        "월28",
        "월드28",
    ]


def test_unicode_letters_internal_digits_hyphens_and_middle_dots_are_words() -> None:
    assert surfaces("K-패스 A1 제1차 전기·가스 café Ωμέγα 東京") == [
        "K-패스",
        "A1",
        "제1차",
        "전기·가스",
        "café",
        "Ωμέγα",
        "東京",
    ]
    assert surfaces("-익일 ·금회 지원--접수 지원··안내") == ["익일", "금회", "지원", "접수", "안내"]


def test_punctuation_separates_tokens_and_connector_words_are_skipped_whole() -> None:
    assert surfaces("(익일),금회:접수! '안내' 신청/마감; foo_bar _secret 이름＿값") == [
        "익일",
        "금회",
        "접수",
        "안내",
        "신청",
        "마감",
    ]


def test_nfd_spelling_preserves_token_offsets_and_only_normalizes_query() -> None:
    nfd = unicodedata.normalize("NFD", "금회")
    text = f"📌 {nfd} 금회 {nfd}"
    source = NoticeGlossaryInput(text=text)
    probes = tokenize_notice(source)
    assert [probe.surface for probe in probes] == [nfd, "금회"]
    assert probes[0].candidates[0].query == probes[1].candidates[0].query == "금회"
    assert [occurrence.start for occurrence in probes[0].occurrences] == [2, 6 + len(nfd)]
    assert all(occurrence.text == nfd for occurrence in probes[0].occurrences)
    assert source.text == text


def test_combining_marks_are_retained_and_nfd_particles_are_not_inferred() -> None:
    accent = "cafe\u0301"
    nfd = unicodedata.normalize("NFD", "익일에")
    probes = tokenize_notice(NoticeGlossaryInput(text=f"{accent} {nfd}"))
    assert probes[0].surface == accent
    assert probes[0].candidates[0].query == "café"
    assert [candidate.surface for candidate in probes[1].candidates] == [nfd]
    assert probes[1].candidates[0].query == "익일에"


def test_minimum_stripped_base_counts_normalized_query_characters() -> None:
    surface = unicodedata.normalize("NFD", "종") + "이"
    (probe,) = tokenize_notice(NoticeGlossaryInput(text=surface))
    assert probe.candidates[0].query == "종이"
    assert len(probe.candidates) == 1


def test_numeric_ending_base_does_not_get_a_particle_alternative() -> None:
    (probe,) = tokenize_notice(NoticeGlossaryInput(text="A1에"))
    assert [candidate.surface for candidate in probe.candidates] == ["A1에"]


def test_long_word_is_skipped_whole_without_splitting_or_failing_notice() -> None:
    text = "a" * 201 + " 익일"
    assert surfaces(text) == ["익일"]
    nfd = unicodedata.normalize("NFD", "가" * 200)
    (probe,) = tokenize_notice(NoticeGlossaryInput(text=nfd))
    assert probe.surface == nfd
    assert probe.candidates[0].query == "가" * 200


def test_multiword_phrases_remain_independent_words_without_ngrams() -> None:
    assert surfaces("경정 청구 안내") == ["경정", "청구", "안내"]


def test_context_is_bounded_original_text() -> None:
    text = "0" * 100 + "\n익일\n" + "1" * 100
    (probe,) = tokenize_notice(NoticeGlossaryInput(text=text))
    (occurrence,) = probe.occurrences
    assert (occurrence.start, occurrence.end) == (101, 103)
    assert (occurrence.context_start, occurrence.context_end) == (21, 183)
    assert occurrence.context == text[21:183]


def test_tokenizer_performs_no_network_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("pure tokenization attempted network I/O")

    monkeypatch.setattr(socket, "socket", reject_network)
    assert surfaces("익일 접수 가정에 안내") == ["익일", "접수", "가정에", "안내"]


def test_probe_is_frozen_and_forbids_unknown_fields() -> None:
    (probe,) = tokenize_notice(NoticeGlossaryInput(text="익일에"))
    with pytest.raises(ValidationError):
        probe.surface = "금회"
    with pytest.raises(ValidationError):
        WordProbe.model_validate({**probe.model_dump(), "extra": "value"})


def test_probe_rejects_missing_full_word_and_mismatched_occurrences() -> None:
    (probe,) = tokenize_notice(NoticeGlossaryInput(text="익일에"))
    with pytest.raises(ValidationError):
        WordProbe(surface=probe.surface, candidates=(), occurrences=probe.occurrences)
    with pytest.raises(ValidationError):
        WordProbe(surface=probe.surface, candidates=probe.candidates, occurrences=())
    with pytest.raises(ValidationError):
        WordProbe(
            surface=probe.surface,
            candidates=probe.candidates[1:],
            occurrences=probe.occurrences,
        )
    with pytest.raises(ValidationError):
        WordProbe(surface="익일", candidates=(probe.candidates[1],), occurrences=probe.occurrences)
    with pytest.raises(ValidationError):
        WordProbe(
            surface=probe.surface,
            candidates=(TermCandidate(surface=probe.surface, query="금회"),),
            occurrences=probe.occurrences,
        )
