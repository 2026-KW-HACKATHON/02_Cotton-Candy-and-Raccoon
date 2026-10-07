"""Test complete search-only definitions using synthetic HTTP responses."""

import logging
from html import escape

import httpx
import pytest

from pipeline.glossary.client import (
    DictionaryClient,
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
        assert request.url.params["req_type"] == "xml"
        assert request.url.params["num"] == "100"
        return httpx.Response(200, text=_search(items))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = DictionaryClient("opendict", KEY, client=http)
        entries = client.lookup_definitions("  산정 \n")
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
    xml = _search((_item(optional="<pos>명사</pos><origin>算定</origin>"),))
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=xml))
    ) as http:
        entries = DictionaryClient("opendict", KEY, client=http).lookup_definitions("산정")
    assert entries[0].part_of_speech == "명사"
    assert entries[0].original_language == "算定"
    assert entries[0].norm_info == entries[0].easy_terms == ()


def test_default_dictionary_service_uses_definition_client_and_one_search(monkeypatch) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.path == "/api/search"
        return httpx.Response(200, text=_search((_item(), _item(2, "002"))))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        monkeypatch.setattr("pipeline.glossary.client.httpx.Client", lambda **kwargs: http)
        result = query_dictionary_definition(
            "산정", settings=GlossarySettings(opendict_api_key=KEY)
        )
    assert len(requests) == 1
    assert result.status == "found" and len(result.entries) == 2
    assert result.providers_checked == ("opendict",)


def test_definition_client_adapter_lookup_makes_no_detail_requests() -> None:
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
        entries = DictionaryClient("opendict", KEY, client=http).lookup_definitions("산정")
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
            DictionaryClient("opendict", KEY, client=http).lookup_definitions("산정")
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
        entries = DictionaryClient("opendict", KEY, client=http).lookup_definitions("산정")
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
            DictionaryClient("opendict", KEY, client=http).lookup_definitions("산정")
    assert caught.value.code == "invalid_response" and starts == [1, 101]


def test_result_cap_is_preserved_before_returning_partial_entries() -> None:
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, text=_search(total=1001))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryClient("opendict", KEY, client=http).lookup_definitions("산정")
    assert caught.value.code == "too_many_results" and requests == ["/api/search"]


def test_timeout_budget_and_safe_timeout_error_are_preserved() -> None:
    requests = []

    def handler(request):
        requests.append(request)
        assert request.extensions["timeout"] == {
            "connect": 15.0,
            "read": 15.0,
            "write": 15.0,
            "pool": 15.0,
        }
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(GlossaryAPIError) as caught:
            DictionaryClient("opendict", KEY, client=http).lookup_definitions("산정")
    assert caught.value.code == "timeout" and len(requests) == 1
    assert KEY not in str(caught.value) and caught.value.__context__ is None


def test_search_only_requests_preserve_credential_redaction(caplog) -> None:
    caplog.set_level(logging.INFO, logger="httpx")
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=_search((_item(),))))
    ) as http:
        DictionaryClient("opendict", KEY, client=http).lookup_definitions("산정")
    assert KEY not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_unsupported_dictionary_is_rejected_before_requests() -> None:
    requests = []

    def handler(request):
        requests.append(request)
        raise AssertionError("unsupported dictionary must not issue requests")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ValueError, match="Ourmalsam"):
            DictionaryClient("krdict", KEY, client=http).lookup_definitions("산정")
        with pytest.raises(ValueError, match="Ourmalsam"):
            DictionaryDefinitionClient("krdict", KEY, client=http)
    assert requests == []
