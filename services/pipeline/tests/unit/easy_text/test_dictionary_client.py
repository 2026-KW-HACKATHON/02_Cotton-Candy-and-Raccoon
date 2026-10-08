"""Complete entry lookup and safe failure behavior at the real HTTP boundary."""

import json
import logging
import traceback

import httpx
import pytest

from pipeline.glossary import dictionary_client as module
from pipeline.glossary.dictionary import DictionaryError, DictionaryQuery
from pipeline.glossary.dictionary_client import DictionaryClient

SECRET = "dictionary-api-test-secret"


def search_response(items=None, *, total=None, start=1):
    if items is None:
        items = [{"target_code": "100", "word": "신청", "sup_no": "1"}]
    return {"channel": {
        "total": len(items) if total is None else total,
        "start": start, "num": module.PAGE_SIZE, "item": items,
    }}


def detail_response(target="100", *, headword="신청"):
    # Structure verified against live JSON on 2026-10-08. Definition text is synthetic.
    return {"channel": {"total": 1, "item": {
        "target_code": target, "word_info": {"word": headword, "pos_info": [
            {"pos_code": "110", "pos": "명사", "comm_pattern_info": [
                {"comm_pattern_code": "120", "sense_info": [
                    {"sense_code": 121, "definition": "첫 번째 시험용 뜻풀이."},
                    {"sense_code": 122, "definition": "두 번째 시험용 뜻풀이."},
                ]},
                {"comm_pattern_code": "130", "sense_info": [
                    {"sense_code": 131, "definition": "다른 문형의 시험용 뜻풀이."},
                ]},
            ]},
            {"pos_code": "210", "pos": "동사", "comm_pattern_info": [
                {"comm_pattern_code": "220", "sense_info": [
                    {"sense_code": 221, "definition": "다른 품사의 시험용 뜻풀이."},
                ]},
            ]},
        ]},
    }}}


def lookup_with(handler, *, word="신청", timeout_seconds=120):
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as http:
        client = DictionaryClient(SECRET, client=http, timeout_seconds=timeout_seconds)
        result = client.lookup(DictionaryQuery(word))
        assert not http.is_closed
        return result


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(module.time, "sleep", lambda _: None)


def test_lookup_keeps_all_homonyms_grammar_groups_and_senses():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.host == "stdict.korean.go.kr"
        assert request.url.params["key"] == SECRET
        assert request.url.params["req_type"] == "json"
        if request.url.path.endswith("search.do"):
            assert dict(request.url.params) == {
                "key": SECRET, "req_type": "json", "q": "신청", "advanced": "y",
                "target": "1", "method": "exact", "pos": "0", "start": "1", "num": "100",
            }
            return httpx.Response(200, json=search_response([
                {"target_code": "100", "word": "신청", "sup_no": "1"},
                {"target_code": "200", "word": "신청", "sup_no": "2"},
            ]))
        assert request.url.params["method"] == "target_code"
        assert request.url.params["type_search"] == "view"
        return httpx.Response(200, json=detail_response(request.url.params["q"]))

    result = lookup_with(handler)
    assert len(calls) == 3
    assert result.status == "found"
    assert [entry.target_code for entry in result.entries] == ["100", "200"]
    assert [entry.homonym_number for entry in result.entries] == ["1", "2"]
    assert [sense.sense_code for sense in result.entries[0].senses] == ["121", "122", "131", "221"]
    assert result.entries[0].senses[-1].part_of_speech == "동사"
    assert result.entries[0].source_url.endswith("?word_no=100")
    assert SECRET not in result.model_dump_json()


def test_only_verified_zero_result_is_not_found():
    calls = []

    def handler(request):
        calls.append(request)
        body = search_response([])
        del body["channel"]["item"]
        return httpx.Response(200, json=body)

    result = lookup_with(handler)
    assert result.status == "not_found"
    assert result.entries == ()
    assert len(calls) == 1


def test_live_empty_json_shape_requires_explicit_xml_zero_confirmation():
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        if request.url.params["req_type"] == "json":
            return httpx.Response(200, json={})
        return httpx.Response(200, content=(
            b'<?xml version="1.0"?><channel><total>0</total>'
            b'<start>1</start><num>100</num></channel>'
        ))

    assert lookup_with(handler).status == "not_found"
    assert len(calls) == 2
    first, second = calls
    assert {**first, "req_type": "xml"} == second


@pytest.mark.parametrize("xml", [
    b"", b"<channel />", b"<channel><total>0</total></channel>",
    b"<channel><total>1</total><start>1</start><num>100</num></channel>",
    b"<channel><total>0</total><start>1</start><num>100</num><item /></channel>",
    b"<channel><total>0</total><total>0</total><start>1</start><num>100</num></channel>",
    b'<!DOCTYPE channel [<!ENTITY a "0">]><channel><total>&a;</total></channel>',
])
def test_empty_json_followed_by_invalid_xml_never_means_not_found(xml):
    def handler(request):
        if request.url.params["req_type"] == "json":
            return httpx.Response(200, json={})
        return httpx.Response(200, content=xml)

    with pytest.raises(DictionaryError, match="^dictionary_invalid_response$"):
        lookup_with(handler)


def test_xml_confirmation_auth_error_is_not_absence():
    def handler(request):
        if request.url.params["req_type"] == "json":
            return httpx.Response(200, json={})
        return httpx.Response(200, content=b"<error><error_code>020</error_code></error>")

    with pytest.raises(DictionaryError, match="^dictionary_authentication_failed$"):
        lookup_with(handler)


def test_pagination_collects_every_entry_before_details(monkeypatch):
    monkeypatch.setattr(module, "PAGE_SIZE", 2)
    calls = []

    def handler(request):
        calls.append((request.url.path, request.url.params.get("start")))
        if request.url.path.endswith("view.do"):
            return httpx.Response(200, json=detail_response(request.url.params["q"]))
        start = int(request.url.params["start"])
        items = [{"target_code": str(n), "word": "신청"} for n in range(start, min(start + 2, 4))]
        return httpx.Response(200, json=search_response(items, total=3, start=start))

    result = lookup_with(handler)
    assert [e.target_code for e in result.entries] == ["1", "2", "3"]
    assert [c[1] for c in calls[:2]] == ["1", "3"]
    assert len(calls) == 5


@pytest.mark.parametrize("malformation", [
    "no_channel", "no_total", "negative_total", "bool_total", "no_start", "wrong_start",
    "no_num", "short_page", "duplicate_entry", "different_word", "wrong_items", "zero_with_item",
])
def test_bad_search_is_never_cached_as_absence(malformation):
    payload = search_response()
    channel = payload["channel"]
    if malformation == "no_channel":
        payload = {}
    elif malformation == "no_total":
        del channel["total"]
    elif malformation == "negative_total":
        channel["total"] = -1
    elif malformation == "bool_total":
        channel["total"] = True
    elif malformation == "no_start":
        del channel["start"]
    elif malformation == "wrong_start":
        channel["start"] = 2
    elif malformation == "no_num":
        del channel["num"]
    elif malformation == "short_page":
        channel["total"] = 2
    elif malformation == "duplicate_entry":
        channel["item"] *= 2
        channel["total"] = 2
    elif malformation == "different_word":
        channel["item"][0]["word"] = "다른 단어"
    elif malformation == "wrong_items":
        channel["item"] = ["not-an-entry"]
    elif malformation == "zero_with_item":
        channel["total"] = 0
    with pytest.raises(DictionaryError, match="^dictionary_invalid_response$"):
        lookup_with(lambda _: httpx.Response(200, json=payload))


@pytest.mark.parametrize("malformation", [
    "missing_entry", "wrong_id", "wrong_word", "no_pos", "empty_pos", "empty_groups",
    "empty_senses", "no_sense_code", "duplicate_sense", "blank_definition",
])
def test_any_incomplete_detail_rejects_whole_lookup(malformation):
    detail = detail_response()
    item = detail["channel"]["item"]
    info = item["word_info"]
    senses = info["pos_info"][0]["comm_pattern_info"][0]["sense_info"]
    if malformation == "missing_entry":
        detail["channel"]["total"] = 0
    elif malformation == "wrong_id":
        item["target_code"] = "101"
    elif malformation == "wrong_word":
        info["word"] = "다른 단어"
    elif malformation == "no_pos":
        del info["pos_info"]
    elif malformation == "empty_pos":
        info["pos_info"] = []
    elif malformation == "empty_groups":
        info["pos_info"][0]["comm_pattern_info"] = []
    elif malformation == "empty_senses":
        info["pos_info"][0]["comm_pattern_info"][0]["sense_info"] = []
    elif malformation == "no_sense_code":
        del senses[0]["sense_code"]
    elif malformation == "duplicate_sense":
        senses[1]["sense_code"] = senses[0]["sense_code"]
    elif malformation == "blank_definition":
        senses[0]["definition"] = " "

    def handler(request):
        payload = search_response() if request.url.path.endswith("search.do") else detail
        return httpx.Response(200, json=payload)

    with pytest.raises(DictionaryError, match="^dictionary_invalid_response$"):
        lookup_with(handler)


def test_optional_spacing_and_morpheme_markers_preserve_provider_headword():
    def handler(request):
        if request.url.path.endswith("search.do"):
            payload = search_response([{"target_code": "100", "word": "신청-하다"}])
        else:
            payload = detail_response(headword="신청-하다")
        return httpx.Response(200, json=payload)

    result = lookup_with(handler, word="신청하다")
    assert result.entries[0].headword == "신청-하다"


def test_singleton_objects_preserve_sense_identifier():
    detail = detail_response()
    info = detail["channel"]["item"]["word_info"]
    pos = info["pos_info"][0]
    group = pos["comm_pattern_info"][0]
    group["sense_info"] = group["sense_info"][0]
    pos["comm_pattern_info"] = group
    info["pos_info"] = pos

    def handler(request):
        return httpx.Response(200, json=(
            search_response() if request.url.path.endswith("search.do") else detail
        ))

    result = lookup_with(handler)
    assert [s.sense_code for s in result.entries[0].senses] == ["121"]


@pytest.mark.parametrize("status,error,retryable,calls", [
    (401, "dictionary_authentication_failed", False, 1),
    (403, "dictionary_authentication_failed", False, 1),
    (429, "dictionary_rate_limited", True, 1),
    (500, "dictionary_upstream_error", True, 2),
    (503, "dictionary_upstream_error", True, 2),
    (400, "dictionary_invalid_request", False, 1),
    (302, "dictionary_invalid_response", False, 1),
])
def test_http_failures_are_safe_and_retries_bounded(status, error, retryable, calls, caplog):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(status, content=f"private {SECRET}", headers={
            "location": f"https://other.example/steal?key={SECRET}",
        })

    with caplog.at_level(logging.DEBUG), pytest.raises(DictionaryError) as caught:
        lookup_with(handler)
    assert caught.value.code == error
    assert caught.value.retryable is retryable
    assert count == calls
    assert SECRET not in caplog.text
    assert SECRET not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("code,error,retryable,calls", [
    ("020", "dictionary_authentication_failed", False, 1),
    (21, "dictionary_authentication_failed", False, 1),
    ("000", "dictionary_upstream_error", True, 2),
    (102, "dictionary_invalid_request", False, 1),
])
def test_http_200_vendor_errors_are_failures(code, error, retryable, calls):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"error": {"error_code": code, "message": SECRET}})

    with pytest.raises(DictionaryError) as caught:
        lookup_with(handler)
    assert caught.value.code == error
    assert caught.value.retryable is retryable
    assert len(requests) == calls


@pytest.mark.parametrize("kind,error", [
    (httpx.ReadTimeout, "dictionary_timeout"),
    (httpx.ConnectError, "dictionary_connection_error"),
])
def test_transport_failure_does_not_expose_secret(kind, error):
    calls = []

    def handler(request):
        calls.append(request)
        raise kind(str(request.url), request=request)

    with pytest.raises(DictionaryError) as caught:
        lookup_with(handler)
    assert caught.value.code == error
    assert len(calls) == 2
    assert SECRET not in "".join(traceback.format_exception(caught.value))


def test_transient_failure_can_recover_on_one_retry():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=search_response([]))

    assert lookup_with(handler).status == "not_found"
    assert calls == 2


@pytest.mark.parametrize("content", [b"", b"<html>upstream error</html>", b"[]", b"null"])
def test_unparseable_payload_never_means_absence(content):
    with pytest.raises(DictionaryError, match="^dictionary_invalid_response$"):
        lookup_with(lambda _: httpx.Response(200, content=content))


@pytest.mark.parametrize("limit", ["response", "lookup", "entries"])
def test_result_limits_do_not_return_partial_results(limit, monkeypatch):
    if limit == "response":
        monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 20)
    elif limit == "lookup":
        size = len(json.dumps(search_response()).encode())
        monkeypatch.setattr(module, "MAX_LOOKUP_BYTES", size + 30)
    else:
        monkeypatch.setattr(module, "MAX_ENTRIES", 0)

    def handler(request):
        return httpx.Response(200, json=(
            search_response() if request.url.path.endswith("search.do") else detail_response()
        ))

    with pytest.raises(DictionaryError, match="^dictionary_result_limit$"):
        lookup_with(handler)


def test_deadline_discards_response_instead_of_finishing_after_budget(monkeypatch):
    clock = [1.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    calls = []

    def handler(request):
        calls.append(request)
        assert max(request.extensions["timeout"].values()) <= 2
        clock[0] += 3
        return httpx.Response(200, json=search_response([]))

    with pytest.raises(DictionaryError, match="^dictionary_timeout$"):
        lookup_with(handler, timeout_seconds=2)
    assert len(calls) == 1


def test_debug_http_logs_do_not_contain_key_and_other_logs_still_work(caplog):
    def handler(request):
        logging.getLogger("httpcore.http11").debug("request target=%s", request.url)
        logging.getLogger("pipeline.dictionary.test").info("lookup attempt")
        return httpx.Response(200, json=search_response([]))

    with caplog.at_level(logging.DEBUG):
        lookup_with(handler)
        logging.getLogger("httpx").info("ordinary later HTTP log")
    assert SECRET not in caplog.text
    assert "lookup attempt" in caplog.text
    assert "ordinary later HTTP log" in caplog.text
    assert SECRET not in repr(DictionaryClient(SECRET))


def test_second_entry_failure_discards_first_success():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("search.do"):
            return httpx.Response(200, json=search_response([
                {"target_code": "100", "word": "신청"},
                {"target_code": "200", "word": "신청"},
            ]))
        if request.url.params["q"] == "100":
            return httpx.Response(200, json=detail_response())
        return httpx.Response(200, content=b"")

    with pytest.raises(DictionaryError, match="^dictionary_invalid_response$"):
        lookup_with(handler)
    assert len(calls) == 3


def test_changing_pagination_total_is_rejected(monkeypatch):
    monkeypatch.setattr(module, "PAGE_SIZE", 1)

    def handler(request):
        start = int(request.url.params["start"])
        payload = search_response(
            [{"target_code": str(start), "word": "신청"}],
            total=2 if start == 1 else 3, start=start,
        )
        return httpx.Response(200, json=payload)

    with pytest.raises(DictionaryError, match="^dictionary_invalid_response$"):
        lookup_with(handler)
