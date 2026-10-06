"""Synthetic OnTerm JSON fixtures shaped after the reported live API contract."""

import json
import logging
import re

import httpx
import pytest

from pipeline.glossary import onterm_client as module
from pipeline.glossary.client import GlossaryAPIError
from pipeline.glossary.onterm_client import OnTermClient

KEY = "0123456789abcdef0123456789abcdef"


def record(**updates):
    return {
        "word": "피투피",
        "source": "한국전력공사",
        "glossary": "전력 용어집",
        "relate_word": "(다듬은 말, 개인간통신)",
        "definition": "개인과 개인이 직접 연결되어 파일을 공유하는 방식.",
        "kr_gvrn_lcns_ty": "1",
        "origin": "P to P",
        "category_main": "과학 기술",
        "category_sub": "에너지/자원",
        "use_ex": "피투피 방식으로 파일을 전송한다.",
        **updates,
    }


def success(records=None, *, total=None, start=1, num=100):
    records = [record()] if records is None else records
    return {
        "channel": {
            "total": len(records) if total is None else total,
            "return_object": [{"returnCode": 1, "resultlist": records}],
            "start": start,
            "num": num,
        }
    }


def no_results(code="1", message="검색 결과가 없습니다."):
    return {"channel": {"returnCode": code, "return_object": message}}


def make_client(payload=None, *, handler=None):
    if handler is None:
        payload = success() if payload is None else payload

        def handler(request):
            return httpx.Response(200, json=payload)

    transport_client = httpx.Client(transport=httpx.MockTransport(handler))
    return OnTermClient(KEY, client=transport_client), transport_client


def test_explicit_polished_word_source_scope_and_license_are_returned():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=success())

    client, transport = make_client(handler=handler)
    with transport:
        entries = client.lookup("  피투피  ")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.provider == "onterm"
    assert entry.headword == "피투피"
    assert entry.easy_terms == ("개인간통신",)
    assert entry.source_name == "국립국어원 온용어"
    assert entry.source_institution == "한국전력공사"
    assert entry.source_glossary == "전력 용어집"
    assert entry.source_url == "https://kli.korean.go.kr/term/"
    assert "key" not in entry.source_url
    assert entry.license == "KOGL 1"
    assert entry.license_url == "https://www.kogl.or.kr/info/licenseType1.do"
    assert entry.entry_id_kind == "content_hash"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", entry.entry_id)
    assert entry.sense_id == "content"
    assert [(info.type, info.role, info.description) for info in entry.norm_info] == [
        ("다듬은 말", "과학 기술 > 에너지/자원", "(다듬은 말, 개인간통신)"),
        ("전문 분야", None, "과학 기술 > 에너지/자원"),
        ("용례", None, "피투피 방식으로 파일을 전송한다."),
    ]
    request = requests[0]
    assert request.url.host == "kli.korean.go.kr"
    assert request.url.path == "/term/api/search.do"
    assert dict(request.url.params) == {
        "key": KEY,
        "apiSearchWord": "피투피",
        "start": "1",
        "num": "100",
        "sort": "wt",
    }


def test_definition_missing_remains_null_instead_of_copying_polished_word():
    client, transport = make_client(success([record(definition=None)]))
    with transport:
        entry = client.lookup("피투피")[0]
    assert entry.definition is None
    assert entry.easy_terms == ("개인간통신",)


def test_empty_definition_and_optional_fields_remain_absent():
    item = record(definition=" ", origin=None, category_main=None, category_sub=None, use_ex=None)
    client, transport = make_client(success([item]))
    with transport:
        entry = client.lookup("피투피")[0]
    assert entry.definition is None
    assert entry.original_language is None
    assert len(entry.norm_info) == 1


def test_html_entities_and_tags_become_plain_text():
    item = record(
        word="<b>피투피</b>",
        definition="&lt;strong&gt;개인 간&lt;/strong&gt; 통신 &amp; 파일 공유.",
        relate_word="<span>(다듬은 말, 개인간통신)</span>",
        source="<em>한국전력공사</em>",
        use_ex="<b>예시</b> &quot;파일&quot; 공유.",
    )
    client, transport = make_client(success([item]))
    with transport:
        entry = client.lookup("피투피")[0]
    assert entry.definition == "개인 간 통신 & 파일 공유."
    assert entry.source_institution == "한국전력공사"
    assert entry.norm_info[-1].description == '예시 "파일" 공유.'


def test_exact_matching_ignores_only_dictionary_separator_conventions():
    client, transport = make_client(success([record(word="경정^청구")]))
    with transport:
        assert client.lookup("경정 청구")[0].headword == "경정^청구"


@pytest.mark.parametrize("word", ["피투피 통신", "피투피를 활용하다", "다른 용어"])
def test_substring_matches_are_not_returned(word):
    client, transport = make_client(success([record(word=word)]))
    with transport:
        assert client.lookup("피투피") == ()


@pytest.mark.parametrize("license_type", ["2", "3", "4", "0", None, "", True, "01"])
def test_only_explicit_kogl_type_one_records_are_returned(license_type):
    client, transport = make_client(success([record(kr_gvrn_lcns_ty=license_type)]))
    with transport:
        assert client.lookup("피투피") == ()


def test_integer_kogl_one_is_also_supported():
    client, transport = make_client(success([record(kr_gvrn_lcns_ty=1)]))
    with transport:
        assert client.lookup("피투피")[0].license == "KOGL 1"


@pytest.mark.parametrize(
    "relation",
    [
        "(다듬을 말, 개인간통신)",
        "(동의어, 개인간통신)",
        "(관련어, 개인간통신)",
        "(대역어, peer to peer)",
        "다듬은 말: 개인간통신",
        "(다듬은 말, 개인간통신, 파일공유)",
        "(다듬은 말, 개인간통신(에너지 분야))",
        "(다듬은 말, 개인간통신) 특정 조건에 한함",
        "(다듬은 말, 피투피)",
        None,
    ],
)
def test_reverse_other_and_ambiguous_relations_never_become_replacements(relation):
    client, transport = make_client(success([record(relate_word=relation)]))
    with transport:
        assert client.lookup("피투피") == ()


def test_multiple_explicit_polished_words_preserve_unrelated_relations_as_context():
    relation = "(동의어, P2P), (다듬은 말, 개인간통신), (다듬은 말, 개인 간 통신)"
    client, transport = make_client(success([record(relate_word=relation)]))
    with transport:
        entry = client.lookup("피투피")[0]
    assert entry.easy_terms == ("개인간통신", "개인 간 통신")
    assert entry.norm_info[0].description == relation


def test_distinct_institutions_definitions_and_scopes_are_never_combined():
    records = [
        record(),
        record(source="다른 기관"),
        record(definition="다른 의미."),
        record(category_sub="정보통신"),
        record(glossary="다른 용어집"),
    ]
    client, transport = make_client(success(records))
    with transport:
        entries = client.lookup("피투피")
    assert len(entries) == 5
    assert len({entry.entry_id for entry in entries}) == 5


def test_same_content_hash_is_stable_under_json_order_and_plain_text_markup():
    first = record()
    second = dict(reversed(list(first.items())))
    second["definition"] = f"<span>{second['definition']}</span>"
    client, transport = make_client(success([first, second]))
    with transport:
        entries = client.lookup("피투피")
    assert len(entries) == 1


@pytest.mark.parametrize("code", ["1", 1])
def test_exact_reported_no_result_shape_is_empty_success(code):
    client, transport = make_client(no_results(code))
    with transport:
        assert client.lookup("없는 용어") == ()


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("100", "api"),
        ("000", "api"),
        ("020", "authentication"),
        ("021", "authentication"),
        ("022", "rate_limit"),
        (22, "rate_limit"),
        ("010", "rate_limit"),
    ],
)
def test_remote_error_codes_are_never_empty_success(code, expected):
    client, transport = make_client(no_results(code=code, message="시스템 에러"))
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("피투피")
    assert caught.value.provider == "onterm"
    assert caught.value.code == expected
    assert "시스템 에러" not in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        no_results(message="검색 결과가 없습니다. "),
        no_results(message="시스템 에러"),
        {"channel": {"returnCode": "1", "return_object": ""}},
        {"channel": {"returnCode": "1", "return_object": []}},
        {"channel": {"return_object": {}}},
        success(total=-1),
        success(start=2),
        success(num=0),
        success(total=0),
        success([], total=1),
        success([{"word": None}]),
        success([record(source=None)]),
        success([record(glossary=None)]),
        success([record(definition=["invalid"])]),
        {"other": {}},
        [],
    ],
)
def test_malformed_or_inconsistent_response_is_failure(payload):
    client, transport = make_client(payload)
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("피투피")
    assert caught.value.code == "invalid_response"
    assert caught.value.__context__ is None


@pytest.mark.parametrize("body", ["{", "<html>error</html>", '{"channel":{},"channel":{}}'])
def test_invalid_and_duplicate_field_json_is_failure(body):
    client, transport = make_client(handler=lambda _: httpx.Response(200, text=body))
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("피투피")
    assert caught.value.code == "invalid_response"


def test_pagination_counts_all_raw_matches_before_filtering_exact_polished_words():
    starts = []

    def handler(request):
        start = int(request.url.params["start"])
        starts.append(start)
        if start == 1:
            records = [record(word=f"비관련 항목{index}") for index in range(100)]
        else:
            records = [record()]
        return httpx.Response(200, json=success(records, total=101, start=start))

    client, transport = make_client(handler=handler)
    with transport:
        entries = client.lookup("피투피")
    assert starts == [1, 2]
    assert len(entries) == 1


def test_page_two_uses_page_number_and_collects_remaining_raw_records():
    starts = []

    def handler(request):
        start = int(request.url.params["start"])
        starts.append(start)
        if start == 1:
            records = [record(word=f"비관련 항목{index}") for index in range(100)]
        elif start == 2:
            records = [record(source=f"테스트 기관{index}") for index in range(69)]
        else:
            raise AssertionError("pagination requested a record offset instead of a page")
        return httpx.Response(200, json=success(records, total=169, start=start))

    client, transport = make_client(handler=handler)
    with transport:
        entries = client.lookup("피투피")
    assert starts == [1, 2]
    assert len(entries) == 69
    assert len({entry.identity for entry in entries}) == 69


def test_missing_records_on_a_nonfinal_page_cannot_skip_results():
    starts = []

    def handler(request):
        start = int(request.url.params["start"])
        starts.append(start)
        records = [record(source=f"테스트 기관{index}") for index in range(99)]
        return httpx.Response(200, json=success(records, total=169, start=start))

    client, transport = make_client(handler=handler)
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("피투피")
    assert caught.value.code == "invalid_response"
    assert starts == [1]


def test_result_overflow_is_failure_without_a_partial_return():
    client, transport = make_client(success(total=1001))
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("피투피")
    assert caught.value.code == "too_many_results"


@pytest.mark.parametrize(
    "second",
    [
        "changed_total",
        "changed_num",
        "wrong_start",
        "missing_records",
        "no_result",
        "error",
        "repeated_record",
    ],
)
def test_incomplete_or_repeated_later_page_cannot_be_a_success(second):
    starts = []

    def handler(request):
        start = int(request.url.params["start"])
        starts.append(start)
        if start == 1:
            payload = success(
                [record(source=f"테스트 기관{index}") for index in range(100)], total=101
            )
        elif second == "changed_total":
            payload = success([record(source="다른 기관")], total=100, start=start)
        elif second == "changed_num":
            payload = success([record(source="다른 기관")], total=101, start=start, num=99)
        elif second == "wrong_start":
            payload = success([record(source="다른 기관")], total=101, start=101)
        elif second == "missing_records":
            payload = success([], total=101, start=start)
        elif second == "no_result":
            payload = no_results()
        elif second == "error":
            payload = no_results("100", "시스템 에러")
        else:
            payload = success([record(source="테스트 기관0")], total=101, start=start)
        return httpx.Response(200, json=payload)

    client, transport = make_client(handler=handler)
    with transport, pytest.raises(GlossaryAPIError):
        client.lookup("피투피")
    assert starts == [1, 2]


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
    client, transport = make_client(handler=lambda _: httpx.Response(status))
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("피투피")
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("kind", "code"), [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "transport")]
)
def test_transport_error_has_no_credential_bearing_exception_context(kind, code):
    def handler(request):
        raise kind(f"Failed {request.url}", request=request)

    client, transport = make_client(handler=handler)
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("피투피")
    assert caught.value.code == code
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert KEY not in str(caught.value)


@pytest.mark.parametrize("declared", [False, True])
def test_response_bytes_are_bounded(monkeypatch, declared):
    monkeypatch.setattr(module, "_MAX_RESPONSE_BYTES", 32)
    headers = {"content-length": "1000"} if declared else {}
    client, transport = make_client(
        handler=lambda _: httpx.Response(200, content=b"x" * 40, headers=headers)
    )
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("피투피")
    assert caught.value.code == "response_too_large"


@pytest.mark.parametrize(
    "form", ["plain", "unicode_json", "html_entities", "percent", "split_html"]
)
def test_encoded_credentials_cannot_reach_results_or_logs(form, caplog):
    caplog.set_level(logging.DEBUG)
    if form == "unicode_json":
        payload = json.dumps(success(), ensure_ascii=False)
        encoded = "".join(f"\\u{ord(char):04x}" for char in KEY)
        payload = payload.replace("개인과 개인이 직접 연결되어 파일을 공유하는 방식.", encoded)
    else:
        if form == "html_entities":
            echo = "".join(f"&#{ord(char)};" for char in KEY)
        elif form == "percent":
            echo = "".join(f"%{ord(char):02x}" for char in KEY)
        elif form == "split_html":
            echo = f"{KEY[:16]}<span>{KEY[16:]}</span>"
        else:
            echo = KEY
        payload = json.dumps(success([record(definition=echo)]), ensure_ascii=False)
    client, transport = make_client(handler=lambda _: httpx.Response(200, text=payload))
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("피투피")
    assert caught.value.code == "invalid_response"
    assert KEY not in str(caught.value)
    assert KEY not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_html_encoded_credential_url_echo_is_rejected_without_warning_output(capsys):
    encoded = "".join(f"&#{ord(char)};" for char in KEY)
    echoed_url = f"https://kli.korean.go.kr/term/api/search.do?key={encoded}"
    client, transport = make_client(success([record(definition=echoed_url)]))
    with transport, pytest.raises(GlossaryAPIError) as caught:
        client.lookup("피투피")
    captured = capsys.readouterr()
    assert caught.value.code == "invalid_response"
    assert KEY not in captured.out
    assert KEY not in captured.err
    assert "MarkupResemblesLocatorWarning" not in captured.err


def test_external_client_is_not_closed_and_context_client_cannot_be_reused():
    client, transport = make_client(no_results())
    with client:
        assert client.lookup("없는 용어") == ()
    assert not transport.is_closed
    transport.close()
    with pytest.raises(GlossaryAPIError) as caught:
        client.lookup("피투피")
    assert caught.value.code == "configuration"


def test_owned_client_is_closed():
    client = OnTermClient(KEY)
    with client:
        pass
    assert client._client.is_closed


@pytest.mark.parametrize("key", ["", None, "  "])
def test_missing_key_fails_without_network(key):
    with pytest.raises(GlossaryAPIError) as caught:
        OnTermClient(key)
    assert caught.value.code == "configuration"
