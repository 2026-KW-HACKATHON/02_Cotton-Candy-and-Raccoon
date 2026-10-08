"""Contract boundaries that protect persistent dictionary cache identities."""

import json
import unicodedata

import pytest
from pydantic import ValidationError

from pipeline.glossary.dictionary import DictionaryError, DictionaryQuery, DictionaryResult


def _payload():
    return {
        "query_word": "지원", "status": "found", "entries": [{
            "target_code": "100", "headword": "지원",
            "source_url": "https://stdict.korean.go.kr/search/searchView.do?word_no=100",
            "senses": [{
                "sense_code": "101", "pos_code": "1", "part_of_speech": "명사",
                "definition": "어떤 일을 돕는 일.",
            }],
        }],
    }


def test_equivalent_unicode_and_outer_whitespace_share_cache_identity():
    original = DictionaryQuery("지원")
    equivalent = DictionaryQuery("  " + unicodedata.normalize("NFD", "지원") + "  ")
    assert equivalent == original
    assert equivalent.cache_key == original.cache_key
    assert DictionaryQuery("지 원").cache_key != original.cache_key
    assert DictionaryQuery("지원하다").cache_key != original.cache_key


@pytest.mark.parametrize("word", [None, 1, "", "  ", "가" * 101, "지\x00원", "지\u200b원"])
def test_invalid_query_has_safe_error_before_lookup(word):
    with pytest.raises(DictionaryError, match="^invalid_dictionary_query$"):
        DictionaryQuery(word)


@pytest.mark.parametrize("corruption", ["entry", "sense", "source", "status", "version"])
def test_untrustworthy_persisted_result_cannot_cross_contract(corruption):
    payload = _payload()
    entry = payload["entries"][0]
    if corruption == "entry":
        payload["entries"].append(entry.copy())
    elif corruption == "sense":
        entry["senses"].append(entry["senses"][0].copy())
    elif corruption == "source":
        entry["source_url"] = "https://stdict.korean.go.kr/search/searchView.do?word_no=999"
    elif corruption == "status":
        payload["status"] = "not_found"
    else:
        payload["contract_version"] = "future-incompatible-contract"
    with pytest.raises(ValidationError):
        DictionaryResult.model_validate_json(json.dumps(payload))
