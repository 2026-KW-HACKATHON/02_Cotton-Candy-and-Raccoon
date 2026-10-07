"""Test complete standard dictionary and Ourmalsam meanings with synthetic HTTP."""

import logging
from html import escape

import httpx
import pytest

from pipeline.glossary import client as module
from pipeline.glossary.client import (
    DictionaryDefinitionClient,
    GlossaryAPIError,
)
from pipeline.glossary.config import GlossarySettings
from pipeline.glossary.dictionary_service import query_dictionary_definition

KEY = "synthetic-definition-key"


def _item(code=1, sense_id="001", definition="계산하여 정함.", optional="") -> str:
    return (
        "<item><word>산정</word><sense>"
        f"<target_code>{code}</target_code><sense_no>{sense_id}</sense_no>"
        f"<definition>{escape(definition)}</definition>{optional}"
        f"<link>https://opendict.korean.go.kr/dictionary/view?sense_no={code}</link>"
        "</sense></item>"
    )


def _search(items=(), *, total=None, start=1, num=None) -> str:
    total = len(items) if total is None else total
    num = len(items) if num is None else num
    return (
        f"<channel><total>{total}</total><start>{start}</start><num>{num}</num>"
        + "".join(items)
        + "</channel>"
    )


def _standard_search_item(code=1, *, word="산정", definition="검색 대표 뜻풀이.") -> str:
    return (
        f"<item><target_code>{code}</target_code><word>{escape(word)}</word>"
        "<pos>명사</pos><origin>算定</origin><sense>"
        f"<definition>{escape(definition)}</definition>"
        f"<link>https://stdict.korean.go.kr/search/searchView.do?word_no={code}</link>"
        "</sense></item>"
    )


def _standard_search(items=(), *, wrapped=True, **pagination) -> str:
    result = _search(items, **pagination)
    return f'<xml version="2.0">{result}</xml>' if wrapped else result


def _standard_sense(code=1001, definition="상세 공식 뜻풀이.", *, source_url=None) -> str:
    link = f"<link>{escape(source_url)}</link>" if source_url is not None else ""
    return (
        f"<sense_info><sense_code>{code}</sense_code>"
        f"<definition>{escape(definition)}</definition>{link}</sense_info>"
    )


def _standard_position(*senses, pos="명사") -> str:
    return (
        f"<pos_info><pos>{escape(pos)}</pos><comm_pattern_info>"
        + "".join(senses or (_standard_sense(),))
        + "</comm_pattern_info></pos_info>"
    )


def _standard_detail(code=1, *, positions=None, nested=True, wrapped=True, word="산정") -> str:
    positions = _standard_position() if positions is None else positions
    word_info = (
        f"<word_info><word>{escape(word)}</word><original_language_info>"
        "<original_language>算定</original_language></original_language_info>"
    )
    body = (
        word_info + positions + "</word_info>" if nested else word_info + "</word_info>" + positions
    )
    result = (
        f"<channel><total>1</total><item><target_code>{code}</target_code>{body}</item></channel>"
    )
    return f'<xml version="2.0">{result}</xml>' if wrapped else result


def test_twenty_four_meanings_need_one_search_and_no_detail_requests() -> None:
    requests = []
    items = tuple(
        _item(code, sense_id=f"{code:03}", definition=f"공식 뜻풀이 {code}.")
        for code in range(1, 25)
    )

    def handler(request):
        requests.append(request)
        assert request.url.path == "/api/search"
        assert request.url.params["q"] == "산정"
        assert request.url.params["method"] == "exact"
        assert request.url.params["target"] == "1"
        assert request.url.params["advanced"] == "y"
        assert request.url.params["part"] == "word"
        assert request.url.params["start"] == "1"
        assert request.url.params["key"] == KEY
        assert request.url.params["req_type"] == "xml"
        assert request.url.params["num"] == "100"
        return httpx.Response(200, text=_search(items))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = DictionaryDefinitionClient("opendict", KEY, client=http)
        entries = client.lookup("  산정 \n")
    assert len(requests) == 1
    assert len(entries) == 24
    assert [entry.entry_id for entry in entries] == [str(code) for code in range(1, 25)]
    assert [entry.sense_id for entry in entries] == [f"{code:03}" for code in range(1, 25)]
    assert [entry.definition for entry in entries] == [
        f"공식 뜻풀이 {code}." for code in range(1, 25)
    ]
    assert all(entry.provider == "opendict" and entry.headword == "산정" for entry in entries)
    assert all(entry.norm_info == entry.easy_terms == () for entry in entries)
    assert all(
        entry.part_of_speech is None and entry.original_language is None for entry in entries
    )
    assert entries[0].source_url == "https://opendict.korean.go.kr/dictionary/view?sense_no=1"


def test_search_provided_optional_metadata_is_retained_without_inventing_refinements() -> None:
    optional = (
        "<pos>명사</pos><origin>算定</origin>"
        "<norm_info><type>순화</type><desc>‘계산’으로 순화함.</desc></norm_info>"
    )
    xml = _search((_item(optional=optional),))
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=xml))
    ) as http:
        entries = DictionaryDefinitionClient("opendict", KEY, client=http).lookup("산정")
    assert entries[0].part_of_speech == "명사"
    assert entries[0].original_language == "算定"
    assert entries[0].norm_info == entries[0].easy_terms == ()


def test_dictionary_service_falls_back_to_ourmalsam_only_after_empty_standard_lookup(
    monkeypatch,
) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "stdict.korean.go.kr":
            assert request.url.path == "/api/search.do"
            return httpx.Response(200, text=_standard_search())
        assert request.url.path == "/api/search"
        return httpx.Response(200, text=_search((_item(), _item(2, "002"))))

    http_client = httpx.Client
    monkeypatch.setattr(
        "pipeline.glossary.client.httpx.Client",
        lambda **kwargs: http_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    result = query_dictionary_definition(
        "산정", settings=GlossarySettings(stdict_api_key=KEY, opendict_api_key=KEY)
    )
    assert len(requests) == 2
    assert result.status == "found" and len(result.entries) == 2
    assert result.providers_checked == ("stdict", "opendict")


def test_definition_client_lookup_makes_no_detail_requests() -> None:
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, text=_search((_item(),)))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with DictionaryDefinitionClient("opendict", KEY, client=http) as client:
            entries = client.lookup("산정")
    assert requests == ["/api/search"]
    assert len(entries) == 1


def test_successful_empty_search_needs_one_request() -> None:
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, text=_search())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        entries = DictionaryDefinitionClient("opendict", KEY, client=http).lookup("산정")
    assert entries == () and requests == ["/api/search"]


@pytest.mark.parametrize(
    "items",
    [
        (_item(definition=""),),
        (_item().replace("<definition>계산하여 정함.</definition>", ""),),
        (_item(sense_id="1"),),
        (_item().replace("<sense_no>001</sense_no>", ""),),
        (_item(code="invalid"),),
        (_item().replace("<word>산정</word>", "<word>다른 말</word>"),),
        (_item().replace("https://opendict.korean.go.kr", "https://other.example"),),
        (_item().replace("sense_no=1", "sense_no=2"),),
        (_item().replace("sense_no=1", "sense_no="),),
        (_item().replace("sense_no=1", "sense_no=1&amp;sense_no="),),
        (_item().replace("sense_no=1", "sense_no=1&amp;key=another-key"),),
        (_item().replace("<sense>", "<different>"),),
        (_item().replace("</sense>", "</sense><sense/>"),),
        (_item(), _item()),
    ],
)
def test_invalid_search_meanings_fail_without_returning_partial_success(items) -> None:
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, text=_search(items))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient("opendict", KEY, client=http).lookup("산정")
    assert caught.value.code == "invalid_response"
    assert requests == ["/api/search"]


def test_multiple_search_pages_preserve_all_meanings_without_details() -> None:
    starts = []

    def handler(request):
        assert request.url.path == "/api/search"
        start = int(request.url.params["start"])
        starts.append(start)
        codes = range(1, 101) if start == 1 else range(101, 102)
        items = tuple(_item(code) for code in codes)
        return httpx.Response(200, text=_search(items, total=101, start=start))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        entries = DictionaryDefinitionClient("opendict", KEY, client=http).lookup("산정")
    assert starts == [1, 101]
    assert len(entries) == 101
    assert [entry.entry_id for entry in entries] == [str(code) for code in range(1, 102)]


def test_duplicate_meaning_across_pages_is_rejected() -> None:
    starts = []

    def handler(request):
        start = int(request.url.params["start"])
        starts.append(start)
        codes = range(1, 101) if start == 1 else (1,)
        return httpx.Response(
            200, text=_search(tuple(_item(code) for code in codes), total=101, start=start)
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient("opendict", KEY, client=http).lookup("산정")
    assert caught.value.code == "invalid_response" and starts == [1, 101]


def test_result_cap_is_preserved_before_returning_partial_entries() -> None:
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, text=_search(total=1001))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient("opendict", KEY, client=http).lookup("산정")
    assert caught.value.code == "too_many_results" and requests == ["/api/search"]


@pytest.mark.parametrize(
    ("exception_type", "code"),
    [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "transport")],
)
@pytest.mark.parametrize("provider", ["opendict", "stdict"])
def test_timeout_budget_and_safe_transport_errors_are_preserved(
    exception_type, code, provider
) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        assert request.extensions["timeout"] == {
            "connect": 15.0,
            "read": 15.0,
            "write": 15.0,
            "pool": 15.0,
        }
        raise exception_type(f"Failed {request.url}", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient(provider, KEY, client=http).lookup("산정")
    assert caught.value.code == code and len(requests) == 1
    assert KEY not in str(caught.value) and caught.value.__context__ is None
    assert caught.value.__cause__ is None


def test_search_only_requests_preserve_credential_redaction(caplog) -> None:
    caplog.set_level(logging.DEBUG)

    def handler(request):
        logging.getLogger("httpcore.http11").debug("send_request_headers %s", request.url)
        return httpx.Response(200, text=_search((_item(),)))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        DictionaryDefinitionClient("opendict", KEY, client=http).lookup("산정")
    assert KEY not in caplog.text
    assert "[REDACTED]" in caplog.text
    assert "HTTP Request" in caplog.text
    assert all(KEY not in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("provider", ["krdict", "onterm", "unknown"])
def test_unsupported_dictionary_is_rejected_before_requests(provider) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        raise AssertionError("unsupported dictionary must not issue requests")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ValueError, match="Ourmalsam"):
            DictionaryDefinitionClient(provider, KEY, client=http)
    assert requests == []


@pytest.mark.parametrize("word,query", [("경정^청구", "경정 청구"), ("선착-순", "선착순")])
def test_compound_headword_markers_are_preserved(word, query) -> None:
    xml = _search((_item().replace("<word>산정</word>", f"<word>{word}</word>"),))
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=xml))
    ) as http:
        entries = DictionaryDefinitionClient("opendict", KEY, client=http).lookup(query)
    assert entries[0].headword == word


@pytest.mark.parametrize(
    ("remote_code", "code"),
    [("020", "authentication"), ("021", "authentication"), ("010", "rate_limit"), ("000", "api")],
)
@pytest.mark.parametrize("nested", [False, True])
def test_provider_errors_are_not_empty_successes(remote_code, code, nested) -> None:
    xml = f"<error><error_code>{remote_code}</error_code><message>Remote text</message></error>"
    if nested:
        xml = f"<channel>{xml}</channel>"
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=xml))
    ) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient("opendict", KEY, client=http).lookup("산정")
    assert caught.value.provider == "opendict" and caught.value.code == code
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
@pytest.mark.parametrize("provider", ["opendict", "stdict"])
def test_http_errors_and_redirects_fail(status, code, provider) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"location": "https://other.example"})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient(provider, KEY, client=http).lookup("산정")
    assert len(requests) == 1 and caught.value.code == code


@pytest.mark.parametrize(
    "payload",
    [
        "<html>not XML</html>",
        "<channel>",
        "<channel><total>0</total></channel>",
        _search(total=1),
        _search(total=-1),
        _search(total=0, start=2),
        _search(total=0, num=101),
        _search((_item(),), total=0),
        _search((_item(),), num=0),
        "<!DOCTYPE channel [<!ENTITY x 'oops'>]><channel><total>&x;</total></channel>",
        '<?xml version="1.0" encoding="utf-16"?>' + _search(),
        _search().replace("<total>0</total>", f"<total>{KEY}</total>"),
        _search().replace("<total>0</total>", "<total>\x000</total>"),
    ],
)
def test_invalid_xml_and_pagination_are_never_empty_successes(payload) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
    ) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient("opendict", KEY, client=http).lookup("산정")
    assert caught.value.code == "invalid_response"
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert KEY not in str(caught.value)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-32"])
def test_non_utf8_xml_cannot_bypass_doctype_or_secret_checks(encoding) -> None:
    payload = (
        "<!DOCTYPE channel [<!ENTITY x 'expanded'>]>"
        "<channel><total>&x;</total><start>1</start><num>100</num></channel>"
    ).encode(encoding)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
    ) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient("opendict", KEY, client=http).lookup("산정")
    assert caught.value.code == "invalid_response"


@pytest.mark.parametrize("form", ["decimal", "hexadecimal", "mixed", "split_elements", "attribute"])
def test_encoded_credential_echo_is_rejected_before_returning_decoded_data(form, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    if form == "hexadecimal":
        encoded = "".join(f"&#x{ord(char):x};" for char in KEY)
    elif form == "mixed":
        encoded = "".join(
            char if index % 2 else f"&#{ord(char)};" for index, char in enumerate(KEY)
        )
    else:
        encoded = "".join(f"&#{ord(char)};" for char in KEY)
    if form == "split_elements":
        midpoint = len(KEY) // 2
        first = "".join(f"&#{ord(char)};" for char in KEY[:midpoint])
        second = "".join(f"&#{ord(char)};" for char in KEY[midpoint:])
        encoded = f"{first}<part>{second}</part>"
    payload = _search((_item(),))
    if form == "attribute":
        payload = payload.replace("<channel>", f'<channel echoed_key="{encoded}">')
    else:
        payload = payload.replace("계산하여 정함.", encoded)
    assert KEY not in payload
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
    ) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient("opendict", KEY, client=http).lookup("산정")
    assert caught.value.code == "invalid_response"
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert KEY not in str(caught.value) and KEY not in caplog.text


@pytest.mark.parametrize("declared", [False, True])
@pytest.mark.parametrize("provider", ["opendict", "stdict"])
def test_response_size_is_bounded(monkeypatch, declared, provider) -> None:
    monkeypatch.setattr(module, "_MAX_RESPONSE_BYTES", 32)
    headers = {"content-length": "500"} if declared else {}
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * 40, headers=headers)
        )
    ) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient(provider, KEY, client=http).lookup("산정")
    assert caught.value.code == "response_too_large"


@pytest.mark.parametrize(
    "second_page", [_search(total=101, start=101), _search(total=100, start=101)]
)
def test_changed_total_or_missing_search_page_fails_without_partial_success(second_page) -> None:
    payloads = [_search(tuple(_item(code) for code in range(1, 101)), total=101), second_page]
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payloads.pop(0)))
    ) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient("opendict", KEY, client=http).lookup("산정")
    assert caught.value.code == "invalid_response" and not payloads


def test_external_http_client_is_not_closed_by_dictionary_client() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=_search()))
    ) as http:
        client = DictionaryDefinitionClient("opendict", KEY, client=http)
        with client:
            assert client.lookup("산정") == ()
        assert not http.is_closed
        with pytest.raises(GlossaryAPIError) as caught:
            client.lookup("산정")
        assert caught.value.code == "configuration"
        with pytest.raises(GlossaryAPIError) as caught:
            client.__enter__()
        assert caught.value.code == "configuration"


def test_owned_http_client_is_closed() -> None:
    client = DictionaryDefinitionClient("opendict", KEY)
    with client:
        pass
    assert client._client.is_closed
    client.close()


@pytest.mark.parametrize("key", ["", "  ", None])
def test_missing_key_fails_without_request(key) -> None:
    with pytest.raises(GlossaryAPIError) as caught:
        DictionaryDefinitionClient("opendict", key)
    assert caught.value.code == "configuration"


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_standard_dictionary_returns_every_detail_meaning_with_actual_sense_codes(
    wrapped, nested
) -> None:
    requests = []
    positions = _standard_position(
        _standard_sense(701, "첫 뜻풀이."), _standard_sense(702, "둘째 뜻풀이.")
    ) + _standard_position(_standard_sense(703, "셋째 뜻풀이."), pos="동사")

    def handler(request):
        requests.append(request)
        assert request.url.host == "stdict.korean.go.kr"
        assert request.url.params["key"] == KEY
        assert request.url.params["req_type"] == "xml"
        if request.url.path == "/api/search.do":
            assert request.url.params["q"] == "산정"
            assert request.url.params["target"] == "1"
            assert request.url.params["method"] == "exact"
            assert request.url.params["advanced"] == "y"
            assert request.url.params["num"] == "100"
            assert "part" not in request.url.params and "sort" not in request.url.params
            return httpx.Response(
                200, text=_standard_search((_standard_search_item(),), wrapped=wrapped)
            )
        assert request.url.path == "/api/view.do"
        assert request.url.params["q"] == "1"
        assert request.url.params["method"] == "target_code"
        return httpx.Response(
            200, text=_standard_detail(positions=positions, nested=nested, wrapped=wrapped)
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        entries = DictionaryDefinitionClient("stdict", KEY, client=http).lookup("  산정 \n")
    assert [request.url.path for request in requests] == ["/api/search.do", "/api/view.do"]
    assert [entry.sense_id for entry in entries] == ["701", "702", "703"]
    assert [entry.definition for entry in entries] == ["첫 뜻풀이.", "둘째 뜻풀이.", "셋째 뜻풀이."]
    assert [entry.part_of_speech for entry in entries] == ["명사", "명사", "동사"]
    assert all(entry.provider == "stdict" and entry.entry_id == "1" for entry in entries)
    assert all(entry.original_language == "算定" for entry in entries)
    assert all(entry.source_name == "국립국어원 표준국어대사전" for entry in entries)
    assert all(entry.easy_terms == entry.norm_info == () for entry in entries)
    assert entries[0].source_url == "https://stdict.korean.go.kr/search/searchView.do?word_no=1"


def test_standard_dictionary_reads_all_search_pages_before_every_entry_detail() -> None:
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path == "/api/search.do":
            start = int(request.url.params["start"])
            codes = range(1, 101) if start == 1 else (101,)
            return httpx.Response(
                200,
                text=_standard_search(
                    tuple(_standard_search_item(code) for code in codes), total=101, start=start
                ),
            )
        code = int(request.url.params["q"])
        return httpx.Response(
            200,
            text=_standard_detail(code, positions=_standard_position(_standard_sense(code + 1000))),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        entries = DictionaryDefinitionClient("stdict", KEY, client=http).lookup("산정")
    assert requests[:2] == ["/api/search.do", "/api/search.do"]
    assert requests[2:] == ["/api/view.do"] * 101
    assert [entry.entry_id for entry in entries] == [str(code) for code in range(1, 102)]
    assert [entry.sense_id for entry in entries] == [str(code + 1000) for code in range(1, 102)]


def test_standard_dictionary_empty_search_is_success_without_detail_request() -> None:
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, text=_standard_search())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        entries = DictionaryDefinitionClient("stdict", KEY, client=http).lookup("산정")
    assert entries == () and requests == ["/api/search.do"]


@pytest.mark.parametrize(
    "item",
    [
        _standard_search_item(code="invalid"),
        _standard_search_item(code=0),
        _standard_search_item(word="다른말"),
        _standard_search_item(definition=""),
        _standard_search_item().replace("<target_code>1</target_code>", ""),
        _standard_search_item().replace("word_no=1", "word_no=2"),
        _standard_search_item().replace("word_no=1", "word_no=1&amp;WORD_NO=1"),
        _standard_search_item().replace("word_no=1", "word_no="),
        _standard_search_item().replace("word_no=1", "word_no=1&amp;api_key=synthetic"),
        _standard_search_item().replace("stdict.korean.go.kr", "other.example"),
        _standard_search_item().replace("</sense>", "</sense><sense/>"),
    ],
)
def test_invalid_standard_search_items_fail_before_any_detail_request(item) -> None:
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, text=_standard_search((item,)))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient("stdict", KEY, client=http).lookup("산정")
    assert caught.value.code == "invalid_response" and requests == ["/api/search.do"]


def test_duplicate_standard_search_entries_fail_before_details() -> None:
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(
            200, text=_standard_search((_standard_search_item(), _standard_search_item()))
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient("stdict", KEY, client=http).lookup("산정")
    assert caught.value.code == "invalid_response" and requests == ["/api/search.do"]


@pytest.mark.parametrize(
    "detail",
    [
        _standard_detail(code=2),
        _standard_detail(word="다른말"),
        _standard_detail().replace("<total>1</total>", "<total>0</total>"),
        _standard_detail().replace("<word_info>", "<unknown>"),
        _standard_detail(positions=""),
        _standard_detail(positions="<pos_info><pos>명사</pos></pos_info>"),
        _standard_detail(positions="<pos_info><comm_pattern_info/></pos_info>"),
        _standard_detail().replace("<sense_code>1001</sense_code>", ""),
        _standard_detail().replace("<sense_code>1001", "<sense_code>invalid"),
        _standard_detail().replace("<sense_code>1001", "<sense_code>0"),
        _standard_detail().replace("상세 공식 뜻풀이.", ""),
        _standard_detail().replace("<definition>상세 공식 뜻풀이.</definition>", ""),
        _standard_detail(positions=_standard_position(_standard_sense(), _standard_sense())),
        _standard_detail(
            positions=_standard_position(_standard_sense(source_url="https://other.example"))
        ),
        _standard_detail(
            positions=_standard_position(
                _standard_sense(
                    source_url="https://stdict.korean.go.kr/search/searchView.do?word_no=2"
                )
            )
        ),
    ],
)
def test_invalid_standard_detail_never_returns_representative_or_partial_success(detail) -> None:
    payloads = [_standard_search((_standard_search_item(),)), detail]
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payloads.pop(0)))
    ) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient("stdict", KEY, client=http).lookup("산정")
    assert caught.value.code == "invalid_response" and not payloads
    assert caught.value.__context__ is None and KEY not in str(caught.value)


def test_standard_search_origin_is_retained_when_detail_has_no_original_language() -> None:
    detail = _standard_detail().replace(
        "<original_language_info><original_language>算定</original_language></original_language_info>",
        "",
    )
    payloads = [_standard_search((_standard_search_item(),)), detail]
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payloads.pop(0)))
    ) as http:
        entries = DictionaryDefinitionClient("stdict", KEY, client=http).lookup("산정")
    assert entries[0].original_language == "算定"


@pytest.mark.parametrize("remote_code,code", [("020", "authentication"), ("000", "api")])
def test_standard_detail_provider_error_is_never_a_partial_success(remote_code, code) -> None:
    payloads = [
        _standard_search((_standard_search_item(),)),
        f"<xml><error><error_code>{remote_code}</error_code></error></xml>",
    ]
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payloads.pop(0)))
    ) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient("stdict", KEY, client=http).lookup("산정")
    assert caught.value.provider == "stdict" and caught.value.code == code and not payloads


@pytest.mark.parametrize("field", ["definition", "original_language", "attribute"])
def test_standard_detail_encoded_credential_echo_is_rejected(field, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    encoded = "".join(f"&#x{ord(char):x};" for char in KEY)
    detail = _standard_detail()
    if field == "attribute":
        detail = detail.replace('<xml version="2.0">', f'<xml echoed="{encoded}">')
    elif field == "original_language":
        detail = detail.replace("<original_language>算定", f"<original_language>{encoded}")
    else:
        detail = detail.replace("상세 공식 뜻풀이.", encoded)
    payloads = [_standard_search((_standard_search_item(),)), detail]
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payloads.pop(0)))
    ) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient("stdict", KEY, client=http).lookup("산정")
    assert caught.value.code == "invalid_response"
    assert KEY not in str(caught.value) and KEY not in caplog.text
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_standard_detail_meaning_count_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(module, "_MAX_RESULTS", 2)
    payloads = [
        _standard_search((_standard_search_item(),)),
        _standard_detail(
            positions=_standard_position(_standard_sense(1), _standard_sense(2), _standard_sense(3))
        ),
    ]
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payloads.pop(0)))
    ) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient("stdict", KEY, client=http).lookup("산정")
    assert caught.value.code == "too_many_results"


@pytest.mark.parametrize("word,query", [("경정^청구", "경정 청구"), ("선착-순", "선착순")])
def test_standard_dictionary_preserves_source_headword_markers(word, query) -> None:
    payloads = [
        _standard_search((_standard_search_item(word=word),)),
        _standard_detail(word=word),
    ]
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payloads.pop(0)))
    ) as http:
        entries = DictionaryDefinitionClient("stdict", KEY, client=http).lookup(query)
    assert entries[0].headword == word


@pytest.mark.parametrize(
    "payload",
    ["<xml><channel/><channel/></xml>", "<xml><channel/><unexpected/></xml>", "<xml/>"],
)
def test_standard_xml_wrapper_requires_one_channel(payload) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
    ) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryDefinitionClient("stdict", KEY, client=http).lookup("산정")
    assert caught.value.code == "invalid_response"
