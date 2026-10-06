"""Synthetic XML fixtures, not captured authenticated dictionary API responses."""

import logging
from datetime import UTC, datetime
from xml.sax.saxutils import escape

import httpx
import pytest

from pipeline.glossary import client as module
from pipeline.glossary.client import DictionaryClient, GlossaryAPIError, reparse_refinements
from pipeline.glossary.models import GlossaryEntry, GlossaryLookup, NormInfo

KEY = "0123456789abcdef0123456789abcdef"
REFINEMENT_SOURCES = (
    "일본어 투 생활 용어 순화 고시 자료(문화체육부 고시 제1997-19호, 1997년 2월 15일)",
    "행정 용어 순화 편람(1993년 2월 12일)",
)
REFINEMENT_DESCRIPTION = (
    "‘익일’ 대신 될 수 있으면 순화한 용어 ‘다음날’, ‘이튿날’을 쓰라고 되어 있다."
)
REVISION_SOURCE_1996 = "생활 용어 수정 보완 고시 자료(문화체육부 고시 제1996-13호, 1996년 3월 23일)"
EOMSU_DESCRIPTION = "‘엄수’ 대신 될 수 있으면 순화한 용어 ‘꼭 지킴’을 쓰라고 되어 있다."
PARALLEL_USE_DESCRIPTION = "‘선착순’과 ‘먼저 온 차례’, ‘온 차례’를 함께 쓸 수 있다고 되어 있다."


def search_item(provider="opendict", identifier="123", word="익일", senses=None):
    if senses is None:
        senses = [("001" if provider == "opendict" else "1", "어떤 날의 다음 날.")]
    if provider == "opendict":
        sense_id, definition = senses[0]
        return (
            f"<item><word>{escape(word)}</word><sense><target_code>{identifier}</target_code>"
            f"<sense_no>{sense_id}</sense_no><definition>{escape(definition)}</definition>"
            f"<link>https://opendict.korean.go.kr/dictionary/view?sense_no={identifier}</link>"
            "</sense></item>"
        )
    meanings = "".join(
        f"<sense><sense_order>{sense_id}</sense_order>"
        f"<definition>{escape(definition)}</definition></sense>"
        for sense_id, definition in senses
    )
    return (
        f"<item><target_code>{identifier}</target_code><word>{escape(word)}</word>"
        f"<link>https://krdict.korean.go.kr/dicSearch/SearchView?ParaWordNo={identifier}</link>"
        f"{meanings}</item>"
    )


def search_page(items="", total=0, start=1, num=100):
    return f"<channel><total>{total}</total><start>{start}</start><num>{num}</num>{items}</channel>"


def detail(provider="opendict", identifier="123", word="익일", norm="", camel=False):
    word_tag = "wordInfo" if camel else "word_info"
    sense_tag = "senseInfo" if camel else "sense_info"
    word_xml = (
        f"<{word_tag}><word>{escape(word)}</word><pos>명사</pos>"
        "<original_language_info><original_language>翌日</original_language>"
        "</original_language_info>"
    )
    meaning = (
        f"<{sense_tag}><sense_no>001</sense_no><pos>명사</pos>"
        f"<definition>어떤 날의 다음 날.</definition>{norm}</{sense_tag}>"
    )
    if provider == "opendict":
        body = f"{word_xml}</{word_tag}>{meaning}"
    else:
        body = f"{word_xml}{meaning}</{word_tag}>"
    return (
        f"<channel><total>1</total><item><target_code>{identifier}</target_code>"
        f"{body}</item></channel>"
    )


def norm_info(description, type_="순화", role=None):
    role_xml = f"<role>{escape(role)}</role>" if role else ""
    return (
        f"<norm_info><type>{escape(type_)}</type>{role_xml}"
        f"<desc>{escape(description)}</desc></norm_info>"
    )


def make_client(provider="opendict", first=None, second=None, handler=None):
    if handler is None:
        responses = [
            first if first is not None else search_page(search_item(provider), total=1),
            second if second is not None else detail(provider),
        ]

        def handler(request):
            return httpx.Response(200, text=responses.pop(0))

    transport_client = httpx.Client(transport=httpx.MockTransport(handler))
    return DictionaryClient(provider, KEY, client=transport_client), transport_client


@pytest.mark.parametrize("provider", ["opendict", "krdict"])
def test_search_detail_request_parameters_and_result(provider):
    requests = []

    def handler(request):
        requests.append(request)
        payload = (
            search_page(search_item(provider), total=1)
            if request.url.path.endswith("search")
            else detail(provider)
        )
        return httpx.Response(200, text=payload)

    client, transport_client = make_client(provider, handler=handler)
    with transport_client, client:
        result = client.lookup("  익일  ")
    assert len(result) == 1
    entry = result[0]
    assert entry.provider == provider
    assert entry.entry_id == "123"
    assert entry.sense_id == ("001" if provider == "opendict" else "1")
    assert entry.headword == "익일"
    assert entry.original_language == "翌日"
    assert entry.part_of_speech == "명사"
    assert entry.easy_terms == ()
    assert requests[0].url.params["q"] == "익일"
    assert requests[0].url.params["method"] == "exact"
    assert requests[0].url.params["target"] == "1"
    assert requests[0].url.params["start"] == "1"
    assert requests[1].url.params["q"] == "123"
    assert requests[1].url.params["method"] == "target_code"
    assert all(request.url.params["key"] == KEY for request in requests)
    if provider == "opendict":
        assert all(request.url.params["req_type"] == "xml" for request in requests)


def test_official_camel_detail_containers_are_supported():
    client, transport = make_client(second=detail(camel=True))
    with transport:
        assert client.lookup("익일")[0].sense_id == "001"


def test_compound_headword_markers_and_original_spelling_are_preserved():
    client, transport = make_client(
        first=search_page(search_item(word="경정^청구"), total=1),
        second=detail(word="경정^청구"),
    )
    with transport:
        assert client.lookup("경정 청구")[0].headword == "경정^청구"


def test_krdict_preserves_all_search_meanings_instead_of_mapping_detail_by_position():
    first = search_page(
        search_item("krdict", senses=[("1", "첫 의미."), ("2", "둘째 의미.")]), total=1
    )
    # The detail API has no documented sense_order; this different definition has no ID.
    second = detail("krdict").replace("어떤 날의 다음 날.", "상세 응답의 별도 표현.")
    client, transport = make_client("krdict", first=first, second=second)
    with transport:
        result = client.lookup("익일")
    assert [(entry.sense_id, entry.definition) for entry in result] == [
        ("1", "첫 의미."),
        ("2", "둘째 의미."),
    ]


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("‘익일’을 ‘다음 날’로 순화하였다.", ("다음 날",)),
        ("'익일'을 '다음 날', '이튿날'로 순화함.", ("다음 날", "이튿날")),
        ("‘다음 날’로 순화.", ("다음 날",)),
        ("‘다음 날’, ‘다음 날’로 순화했다.", ("다음 날",)),
        ("‘익일’ 대신 순화한 용어 ‘다음날’을 쓰라고 되어 있다.", ("다음날",)),
        (REFINEMENT_DESCRIPTION, ("다음날", "이튿날")),
        ("‘익일’을 ‘익일’로 순화하였다.", ()),
        ("‘금회’를 ‘이번’으로 순화하였다.", ()),
        ("‘금회’ 대신 될 수 있으면 순화한 용어 ‘이번’을 쓰라고 되어 있다.", ()),
        ("행정 분야에서 ‘익일’을 ‘다음 날’로 순화하였다.", ()),
        ("‘다음 날’로 순화하였다. 다만 계약 문서에는 적용하지 않는다.", ()),
        ("‘익일’ 대신 계약 문서에서 순화한 용어 ‘다음날’을 쓰라고 되어 있다.", ()),
        (
            "‘익일’ 대신 될 수 있으면 순화한 용어 ‘다음날’을 쓰라고 되어 있다. "
            "다만 계약 문서는 제외한다.",
            (),
        ),
        ("문맥에 따라 ‘다음 날’로 쓸 수 있다.", ()),
        ("‘다음 날’은 ‘금후’를 설명하기 위한 예시다.", ()),
        ("순화한 용어 ‘다음날’을 쓰라고 되어 있다.", ()),
        ("‘익일’ 대신 순화한 용어 ‘다음날’을 쓰는 방안을 제안한다.", ()),
        ("예를 들어 ‘익일’ 대신 순화한 용어 ‘다음날’을 쓰라고 되어 있다.", ()),
        ("‘익일’은 ‘다음날’, ‘이튿날’의 동의어이다.", ()),
        ("'익일'은 다음 날을 가리킨다.", ()),
    ],
)
def test_only_explicit_unconditional_normative_replacements_are_extracted(description, expected):
    norm = norm_info(description)
    client, transport = make_client(second=detail(norm=norm))
    with transport:
        entry = client.lookup("익일")[0]
    assert entry.easy_terms == expected
    assert entry.norm_info[0].description == description


def test_ourmalsam_refinement_citations_preserve_alternatives_and_complete_notes():
    # Reproduce the field values of the official 익일 note with a synthetic XML envelope.
    norm = "".join(norm_info(REFINEMENT_DESCRIPTION, role=role) for role in REFINEMENT_SOURCES)
    client, transport = make_client(second=detail(norm=norm))
    with transport:
        entry = client.lookup("익일")[0]
    assert entry.easy_terms == ("다음날", "이튿날")
    assert [(info.type, info.role, info.description) for info in entry.norm_info] == [
        ("순화", role, REFINEMENT_DESCRIPTION) for role in REFINEMENT_SOURCES
    ]
    assert entry.source_url == "https://opendict.korean.go.kr/dictionary/view?sense_no=123"


@pytest.mark.parametrize("role", REFINEMENT_SOURCES)
def test_source_citation_also_allows_canonical_refinement_statement(role):
    description = "‘익일’을 ‘다음 날’로 순화하였다."
    client, transport = make_client(second=detail(norm=norm_info(description, role=role)))
    with transport:
        entry = client.lookup("익일")[0]
    assert entry.easy_terms == ("다음 날",)
    assert entry.norm_info[0].role == role


def test_1996_official_refinement_remains_attached_to_only_its_eomsu_meaning():
    meanings = [
        ("225258", "001", "‘별수’의 방언"),
        ("225259", "002", "조선 시대에, 내시부에 속하여 임금의 시중을 들던 남자."),
        ("428979", "003", "달아나지 못하도록 엄중하게 가둠."),
        ("465915", "004", "명령이나 약속 따위를 어김없이 지킴."),
        ("225262", "005", "의식 따위를 엄숙하게 치름."),
    ]
    responses = [
        search_page(
            "".join(
                search_item(identifier=identifier, word="엄수", senses=[(sense, definition)])
                for identifier, sense, definition in meanings
            ),
            total=5,
        ),
    ]
    for identifier, sense, definition in meanings:
        norm = norm_info(EOMSU_DESCRIPTION, role=REVISION_SOURCE_1996) if sense == "004" else ""
        responses.append(
            detail(identifier=identifier, word="엄수", norm=norm)
            .replace("<sense_no>001", f"<sense_no>{sense}")
            .replace("어떤 날의 다음 날.", definition)
        )
    client, transport = make_client(handler=lambda _: httpx.Response(200, text=responses.pop(0)))
    with transport:
        entries = client.lookup("엄수")
    assert [(entry.entry_id, entry.sense_id, entry.definition) for entry in entries] == meanings
    assert [entry.easy_terms for entry in entries] == [(), (), (), ("꼭 지킴",), ()]
    assert entries[3].norm_info[0].role == REVISION_SOURCE_1996
    assert entries[3].norm_info[0].description == EOMSU_DESCRIPTION
    assert all(not entry.norm_info for index, entry in enumerate(entries) if index != 3)


def test_official_parallel_use_note_preserves_all_alternatives_and_raw_headword():
    norm = norm_info(PARALLEL_USE_DESCRIPTION, role=REFINEMENT_SOURCES[1])
    client, transport = make_client(
        first=search_page(search_item(word="선착-순"), total=1),
        second=detail(word="선착-순", norm=norm),
    )
    with transport:
        entry = client.lookup("선착순")[0]
    assert entry.headword == "선착-순"
    assert entry.easy_terms == ("먼저 온 차례", "온 차례")
    assert entry.norm_info[0].type == "순화"
    assert entry.norm_info[0].role == REFINEMENT_SOURCES[1]
    assert entry.norm_info[0].description == PARALLEL_USE_DESCRIPTION


@pytest.mark.parametrize(
    "description,type_,role",
    [
        (PARALLEL_USE_DESCRIPTION.replace("‘선착순’", "‘등록순’"), "순화", REFINEMENT_SOURCES[1]),
        (PARALLEL_USE_DESCRIPTION, "동의어", REFINEMENT_SOURCES[1]),
        (PARALLEL_USE_DESCRIPTION, "번역", REFINEMENT_SOURCES[1]),
        (PARALLEL_USE_DESCRIPTION, "순화 제안", REFINEMENT_SOURCES[1]),
        ("예를 들어 " + PARALLEL_USE_DESCRIPTION, "순화", REFINEMENT_SOURCES[1]),
        ("행정 분야에서 " + PARALLEL_USE_DESCRIPTION, "순화", REFINEMENT_SOURCES[1]),
        ("계약서를 쓸 때 " + PARALLEL_USE_DESCRIPTION, "순화", REFINEMENT_SOURCES[1]),
        (PARALLEL_USE_DESCRIPTION + " 다만 계약 문서는 제외한다.", "순화", REFINEMENT_SOURCES[1]),
        (PARALLEL_USE_DESCRIPTION, "순화", "계약 문서에 한정해 적용"),
        ("‘선착순’과 ‘먼저 온 차례’, ‘온 차례’는 동의어이다.", "순화", REFINEMENT_SOURCES[1]),
        ("‘선착순’과 ‘first come’은 번역 관계이다.", "순화", REFINEMENT_SOURCES[1]),
        ("‘선착순’은 ‘먼저 온 차례’라는 뜻이다.", "순화", REFINEMENT_SOURCES[1]),
    ],
)
def test_parallel_use_extraction_does_not_promote_other_relations_or_discard_conditions(
    description, type_, role
):
    norm = norm_info(description, type_, role)
    client, transport = make_client(
        first=search_page(search_item(word="선착-순"), total=1),
        second=detail(word="선착-순", norm=norm),
    )
    with transport:
        entry = client.lookup("선착순")[0]
    assert entry.easy_terms == ()
    assert (entry.norm_info[0].type, entry.norm_info[0].role, entry.norm_info[0].description) == (
        type_,
        role,
        description,
    )


@pytest.mark.parametrize(
    "query,headword,description,role,expected",
    [
        ("엄수", "엄수", EOMSU_DESCRIPTION, REVISION_SOURCE_1996, ("꼭 지킴",)),
        (
            "선착순",
            "선착-순",
            PARALLEL_USE_DESCRIPTION,
            REFINEMENT_SOURCES[1],
            ("먼저 온 차례", "온 차례"),
        ),
    ],
)
@pytest.mark.parametrize("cached", [False, True])
def test_reparse_saved_official_relations_keeps_all_other_lookup_data_and_order(
    query, headword, description, role, expected, cached, monkeypatch
):
    def forbidden(*args, **kwargs):
        raise AssertionError("reparsing must not make a dictionary request")

    monkeypatch.setattr(httpx, "Client", forbidden)
    entry = GlossaryEntry(
        provider="opendict",
        entry_id="123",
        sense_id="001",
        headword=headword,
        definition="저장된 공식 뜻풀이.",
        original_language="保存",
        part_of_speech="명사",
        norm_info=(NormInfo(type="순화", role=role, description=description),),
        source_url="https://opendict.korean.go.kr/dictionary/view?sense_no=123",
    )
    saved = GlossaryLookup(
        query=query,
        status="found",
        entries=(entry,),
        providers_checked=("onterm", "opendict"),
        queried_at=datetime(2026, 10, 6, 12, tzinfo=UTC),
        from_cache=cached,
    )
    reparsed = reparse_refinements(saved)
    assert reparsed.entries[0].easy_terms == expected
    assert saved.entries[0].easy_terms == ()
    before, after = saved.model_dump(), reparsed.model_dump()
    after["entries"][0]["easy_terms"] = before["entries"][0]["easy_terms"]
    assert after == before


@pytest.mark.parametrize("provider", ["opendict", "krdict"])
def test_reparse_keeps_existing_terms_when_original_norm_info_is_unavailable(provider):
    entry = GlossaryEntry(
        provider=provider,
        entry_id="123",
        sense_id="001",
        headword="익일",
        definition="어떤 날의 다음 날.",
        easy_terms=("다음 날", "이튿날"),
        source_url=f"https://{provider}.korean.go.kr/word/123",
    )
    saved = GlossaryLookup(
        query="익일",
        status="found",
        entries=(entry,),
        providers_checked=(provider,),
        queried_at=datetime(2026, 10, 6, 12, tzinfo=UTC),
        from_cache=True,
    )
    assert reparse_refinements(saved) == saved
    assert reparse_refinements(saved).entries[0] is entry


def test_reparse_keeps_other_provider_targets_even_when_they_have_norm_info():
    entry = GlossaryEntry(
        provider="onterm",
        entry_id="sha256:" + "a" * 64,
        sense_id="content",
        entry_id_kind="content_hash",
        headword="엄수",
        easy_terms=("꼭 지킴",),
        source_institution="예시 기관",
        source_glossary="예시 용어집",
        source_url="https://kli.korean.go.kr/term/record",
        norm_info=(NormInfo(type="다듬은 말", description="꼭 지킴"),),
    )
    saved = GlossaryLookup(
        query="엄수",
        status="found",
        entries=(entry,),
        providers_checked=("onterm",),
        queried_at=datetime(2026, 10, 6, 12, tzinfo=UTC),
        from_cache=True,
    )
    assert reparse_refinements(saved) == saved
    assert reparse_refinements(saved).entries[0] is entry


def test_reparse_does_not_promote_an_old_condition_or_proposal_to_a_refinement():
    description = "계약서를 쓸 때 ‘엄수’를 ‘꼭 지킴’으로 순화할 것을 제안한다."
    entry = GlossaryEntry(
        provider="opendict",
        entry_id="123",
        sense_id="001",
        headword="엄수",
        definition="명령이나 약속 따위를 어김없이 지킴.",
        easy_terms=("꼭 지킴",),
        norm_info=(NormInfo(type="순화", role=REVISION_SOURCE_1996, description=description),),
        source_url="https://opendict.korean.go.kr/dictionary/view?sense_no=123",
    )
    saved = GlossaryLookup(
        query="엄수",
        status="found",
        entries=(entry,),
        providers_checked=("opendict",),
        queried_at=datetime(2026, 10, 6, 12, tzinfo=UTC),
        from_cache=True,
    )
    reparsed = reparse_refinements(saved)
    assert reparsed.entries[0].easy_terms == ()
    assert reparsed.entries[0].norm_info == saved.entries[0].norm_info
    assert saved.entries[0].easy_terms == ("꼭 지킴",)


@pytest.mark.parametrize(
    "role",
    [
        "계약 문서에 한정해 적용",
        "행정 분야에서만 사용",
        REFINEMENT_SOURCES[0] + "; 계약 문서에 한정해 적용",
        "행정 용어 순화 편람(계약 문서에 한정해 적용)",
        "확인되지 않은 순화 자료",
    ],
)
def test_semantic_conditions_and_unknown_roles_block_recommended_replacements(role):
    client, transport = make_client(
        second=detail(norm=norm_info(REFINEMENT_DESCRIPTION, role=role))
    )
    with transport:
        entry = client.lookup("익일")[0]
    assert entry.easy_terms == ()
    assert entry.norm_info[0].role == role


@pytest.mark.parametrize(
    ("type_", "description"),
    [
        ("순화 제안", REFINEMENT_DESCRIPTION),
        ("동의어", REFINEMENT_DESCRIPTION),
        ("순화", "‘익일’ 대신 될 수 있으면 순화한 용어 ‘다음날’을 쓰라는 제안이 있다."),
        ("순화", "행정 분야에서 ‘익일’을 ‘다음날’로 순화하였다."),
        ("순화", "예를 들어 ‘익일’ 대신 순화한 용어 ‘다음날’을 쓰라고 되어 있다."),
    ],
)
def test_source_citation_does_not_make_nonuniversal_statements_replacements(type_, description):
    norm = norm_info(description, type_, REFINEMENT_SOURCES[0])
    client, transport = make_client(second=detail(norm=norm))
    with transport:
        entry = client.lookup("익일")[0]
    assert entry.easy_terms == ()
    assert entry.norm_info[0].description == description


def test_refined_alternatives_stay_attached_to_their_dictionary_meaning():
    first_meaning = "어떤 날의 다음 날."
    second_meaning = "다른 뜻풀이."
    responses = [
        search_page(
            search_item(identifier="123", senses=[("001", first_meaning)])
            + search_item(identifier="456", senses=[("002", second_meaning)]),
            total=2,
        ),
        detail(norm=norm_info(REFINEMENT_DESCRIPTION, role=REFINEMENT_SOURCES[0])),
        detail(identifier="456")
        .replace("<sense_no>001", "<sense_no>002")
        .replace(first_meaning, second_meaning),
    ]
    client, transport = make_client(handler=lambda _: httpx.Response(200, text=responses.pop(0)))
    with transport:
        entries = client.lookup("익일")
    meanings = [
        (entry.entry_id, entry.sense_id, entry.definition, entry.easy_terms) for entry in entries
    ]
    assert meanings == [
        ("123", "001", first_meaning, ("다음날", "이튿날")),
        ("456", "002", second_meaning, ()),
    ]
    assert entries[1].norm_info == ()


def test_all_normative_explanations_and_roles_are_preserved():
    descriptions = ["관련 조항을 확인해야 한다.", "조건에 따라 의미가 달라진다."]
    norm = norm_info(descriptions[0], "맞춤법", "제10항") + norm_info(descriptions[1])
    client, transport = make_client(second=detail(norm=norm))
    with transport:
        entry = client.lookup("익일")[0]
    assert [(info.type, info.role, info.description) for info in entry.norm_info] == [
        ("맞춤법", "제10항", descriptions[0]),
        ("순화", None, descriptions[1]),
    ]
    assert entry.easy_terms == ()


@pytest.mark.parametrize(
    ("type_", "role"), [("순화 제안", None), ("순화", "계약 문서에 한정해 적용")]
)
def test_draft_norms_and_related_clauses_do_not_become_unconditional_replacements(type_, role):
    norm = norm_info("‘익일’을 ‘다음 날’로 순화하였다.", type_, role)
    client, transport = make_client(second=detail(norm=norm))
    with transport:
        entry = client.lookup("익일")[0]
    assert entry.easy_terms == ()
    assert entry.norm_info[0].role == role


@pytest.mark.parametrize("provider", ["opendict", "krdict"])
def test_no_results_is_a_successful_empty_tuple_and_no_detail_call(provider):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=search_page())

    client, transport = make_client(provider, handler=handler)
    with transport:
        assert client.lookup("없는말") == ()
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("remote_code", "code"),
    [("020", "authentication"), ("021", "authentication"), ("010", "rate_limit"), ("000", "api")],
)
@pytest.mark.parametrize("provider", ["opendict", "krdict"])
def test_http_200_provider_error_is_not_no_results(provider, remote_code, code):
    first = f"<error><error_code>{remote_code}</error_code><message>Remote text</message></error>"
    client, transport = make_client(provider, first=first)
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("익일")
    assert caught.value.provider == provider
    assert caught.value.code == code
    assert "Remote text" not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "authentication"),
        (403, "authentication"),
        (429, "rate_limit"),
        (500, "http"),
        (302, "http"),
    ],
)
def test_http_errors_and_redirects_fail(status, code):
    client, transport = make_client(
        handler=lambda _: httpx.Response(status, headers={"location": "https://other.example"})
    )
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("익일")
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("exception_type", "code"),
    [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "transport")],
)
def test_transport_failures_have_no_credential_bearing_exception_chain(exception_type, code):
    def handler(request):
        raise exception_type(f"Failed {request.url}", request=request)

    client, transport = make_client(handler=handler)
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("익일")
    assert caught.value.code == code
    assert KEY not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "payload",
    [
        "<html>not XML</html>",
        "<channel>",
        "<channel><total>0</total></channel>",
        search_page(total=1),
        search_page(total=-1),
        search_page(total=0, start=2),
        search_page(search_item(), total=0),
        search_page(search_item(), total=1, num=0),
        search_page(search_item().replace("<target_code>123", "<target_code>oops"), total=1),
        search_page(search_item().replace("<sense_no>001", "<sense_no>1"), total=1),
        search_page(search_item().replace("익일", "다른말"), total=1),
        search_page(search_item().replace("opendict.korean.go.kr", "other.example"), total=1),
        search_page(search_item().replace("view?sense_no=123", "view?sense_no=456"), total=1),
        search_page(search_item().replace("어떤 날의 다음 날.", ""), total=1),
        "<!DOCTYPE channel [<!ENTITY x 'oops'>]><channel><total>&x;</total></channel>",
        f"<channel><total>{KEY}</total></channel>",
    ],
)
def test_invalid_response_is_never_an_empty_success(payload):
    client, transport = make_client(first=payload)
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("익일")
    assert caught.value.code == "invalid_response"
    assert caught.value.__context__ is None
    assert KEY not in str(caught.value)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-32"])
def test_non_utf8_xml_cannot_bypass_doctype_or_secret_checks(encoding):
    data = (
        "<!DOCTYPE channel [<!ENTITY x 'expanded'>]>"
        "<channel><total>&x;</total><start>1</start><num>100</num></channel>"
    ).encode(encoding)
    client, transport = make_client(handler=lambda _: httpx.Response(200, content=data))
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("익일")
    assert caught.value.code == "invalid_response"


@pytest.mark.parametrize("provider", ["opendict", "krdict"])
@pytest.mark.parametrize("form", ["decimal", "hexadecimal", "mixed", "split_elements", "attribute"])
def test_encoded_credential_echo_fails_before_decoded_data_can_be_returned(provider, form, caplog):
    caplog.set_level(logging.DEBUG)
    if form == "hexadecimal":
        encoded_key = "".join(f"&#x{ord(char):x};" for char in KEY)
    elif form == "mixed":
        encoded_key = "".join(
            char if index % 2 else f"&#{ord(char)};" for index, char in enumerate(KEY)
        )
    else:
        encoded_key = "".join(f"&#{ord(char)};" for char in KEY)
    if form == "split_elements":
        midpoint = len(KEY) // 2
        first_half = "".join(f"&#{ord(char)};" for char in KEY[:midpoint])
        second_half = "".join(f"&#{ord(char)};" for char in KEY[midpoint:])
        encoded_key = f"{first_half}<part>{second_half}</part>"
    payload = search_page(search_item(provider), total=1)
    if form == "attribute":
        payload = payload.replace("<channel>", f'<channel echoed_key="{encoded_key}">')
    else:
        payload = payload.replace("어떤 날의 다음 날.", encoded_key)
    assert KEY not in payload
    client, transport = make_client(provider, first=payload)
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("익일")
    assert caught.value.code == "invalid_response"
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert KEY not in str(caught.value)
    assert KEY not in caplog.text


def test_encoded_key_in_detail_norm_info_cannot_reach_saved_result():
    encoded_key = "".join(f"&#{ord(char)};" for char in KEY)
    second = detail(norm=norm_info("임시설명"))
    second = second.replace("임시설명", encoded_key)
    assert KEY not in second
    client, transport = make_client(second=second)
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("익일")
    assert caught.value.code == "invalid_response"
    assert KEY not in str(caught.value)


@pytest.mark.parametrize("field", ["role", "target"])
@pytest.mark.parametrize(
    "description,target,role",
    [
        (REFINEMENT_DESCRIPTION, "다음날", REFINEMENT_SOURCES[0]),
        (PARALLEL_USE_DESCRIPTION, "먼저 온 차례", REFINEMENT_SOURCES[1]),
    ],
)
def test_encoded_credential_in_recognized_refinement_note_is_rejected(
    field, description, target, role, caplog
):
    caplog.set_level(logging.DEBUG)
    norm = norm_info(description, role=role)
    encoded_key = "".join(f"&#x{ord(char):x};" for char in KEY)
    if field == "role":
        norm = norm.replace(role, encoded_key)
    else:
        norm = norm.replace(target, encoded_key)
    second = detail(norm=norm)
    assert KEY not in second
    client, transport = make_client(second=second)
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("익일")
    assert caught.value.code == "invalid_response"
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert KEY not in str(caught.value)
    assert KEY not in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        detail(identifier="999"),
        detail(word="다른말"),
        detail().replace("<sense_no>001", "<sense_no>002"),
        detail().replace("<sense_no>001</sense_no>", ""),
        detail().replace("<total>1</total>", "<total>0</total>"),
        detail().replace("어떤 날의 다음 날.", ""),
        # A meaning nested under word_info violates the official Ourmalsam contract.
        detail()
        .replace("</word_info><sense_info>", "<sense_info>")
        .replace("</sense_info>", "</sense_info></word_info>"),
    ],
)
def test_mismatched_or_missing_detail_fields_fail(payload):
    client, transport = make_client(second=payload)
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("익일")
    assert caught.value.code == "invalid_response"


def test_all_search_pages_are_read_before_details():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("search"):
            start = int(request.url.params["start"])
            ids = range(1, 101) if start == 1 else [101]
            items = "".join(search_item(identifier=str(identifier)) for identifier in ids)
            return httpx.Response(200, text=search_page(items, total=101, start=start))
        return httpx.Response(200, text=detail(identifier=request.url.params["q"]))

    client, transport = make_client(handler=handler)
    with transport:
        entries = client.lookup("익일")
    assert len(entries) == 101
    assert requests[1].url.params["start"] == "101"
    assert len(requests) == 103


def test_result_limit_is_failure_not_a_partial_result():
    client, transport = make_client(first=search_page(total=1001))
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("익일")
    assert caught.value.code == "too_many_results"


def test_changed_total_or_duplicate_page_fails_without_partial_success():
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(
            200, text=search_page(search_item(), total=2 if count == 1 else 1, start=count)
        )

    client, transport = make_client(handler=handler)
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("익일")
    assert caught.value.code == "invalid_response"


def test_duplicate_meaning_ids_are_not_returned():
    first = search_page(search_item() + search_item(), total=2)
    client, transport = make_client(first=first)
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("익일")
    assert caught.value.code == "invalid_response"


@pytest.mark.parametrize("declared", [False, True])
def test_response_size_is_bounded(monkeypatch, declared):
    monkeypatch.setattr(module, "_MAX_RESPONSE_BYTES", 32)
    headers = {"content-length": "500"} if declared else {}
    client, transport = make_client(
        handler=lambda _: httpx.Response(200, content=b"x" * 40, headers=headers)
    )
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("익일")
    assert caught.value.code == "response_too_large"


def test_httpx_info_and_httpcore_debug_logs_redact_credentials(caplog):
    caplog.set_level(logging.DEBUG)

    def handler(request):
        logging.getLogger("httpcore.http11").debug("send_request_headers %s", request.url)
        return httpx.Response(200, text=search_page())

    client, transport = make_client(handler=handler)
    with transport:
        assert client.lookup("익일") == ()
    assert "HTTP Request" in caplog.text
    assert "[REDACTED]" in caplog.text
    assert KEY not in caplog.text
    assert all(KEY not in record.getMessage() for record in caplog.records)


def test_external_http_client_is_not_closed_by_dictionary_client():
    client, transport = make_client(first=search_page())
    with client:
        assert client.lookup("익일") == ()
    assert not transport.is_closed
    transport.close()
    with pytest.raises(GlossaryAPIError) as caught:
        client.lookup("익일")
    assert caught.value.code == "configuration"


def test_owned_http_client_is_closed():
    client = DictionaryClient("opendict", KEY)
    with client:
        pass
    assert client._client.is_closed
    client.close()


@pytest.mark.parametrize("key", ["", "  ", None])
def test_missing_key_fails_without_a_request(key):
    with pytest.raises(GlossaryAPIError) as caught:
        DictionaryClient("opendict", key)
    assert caught.value.code == "configuration"
