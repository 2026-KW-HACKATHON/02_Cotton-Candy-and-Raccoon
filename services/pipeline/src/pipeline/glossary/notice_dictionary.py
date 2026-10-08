"""Map attested notice candidates to their shared cache and easy-text spans."""

from bisect import bisect_left, bisect_right

from pipeline.glossary.dictionary import DictionaryQuery
from pipeline.glossary.easy_language import EasyLanguageResult


def map_dictionary_candidates(result: EasyLanguageResult) -> list[dict[str, object]]:
    """Preserve original offsets and calculate separate easy-text code-point offsets.

    A replacement wholly inside the candidate can be mapped exactly, including
    a replacement of the whole candidate. When a replacement crosses a candidate
    boundary, its inner word positions cannot be inferred: keep the original
    reference with an explicit original_only mapping instead of guessing.
    """
    result = EasyLanguageResult.model_validate(result.model_dump(mode="python"))
    if result.dictionary_candidates is None:
        raise ValueError("사전 후보를 추출한 결과가 필요합니다.")
    if result.easy_result is not None:
        # A rewrite has no word-level alignment to the original; candidates are
        # shown only on the original text.
        return [
            {
                **candidate.model_dump(),
                "cache_key": DictionaryQuery(candidate.query_word).cache_key,
                "easy_start": None,
                "easy_end": None,
                "easy_expression": None,
                "mapping_status": "original_only",
            }
            for candidate in result.dictionary_candidates
        ]
    starts = [change.start for change in result.changes]
    ends = [change.end for change in result.changes]
    deltas = [0]
    for change in result.changes:
        deltas.append(deltas[-1] + len(change.replacement) - (change.end - change.start))
    mapped = []
    for candidate in result.dictionary_candidates:
        before = bisect_right(ends, candidate.start)
        after = bisect_left(starts, candidate.end)
        boundary_overlap = before < after and (
            starts[before] < candidate.start or ends[after - 1] > candidate.end
        )
        easy_start = None if boundary_overlap else candidate.start + deltas[before]
        easy_end = None if boundary_overlap else candidate.end + deltas[after]
        mapped.append({
            **candidate.model_dump(),
            "cache_key": DictionaryQuery(candidate.query_word).cache_key,
            "easy_start": easy_start,
            "easy_end": easy_end,
            "easy_expression": (
                None if boundary_overlap else result.easy_text[easy_start:easy_end]
            ),
            "mapping_status": (
                "original_only" if boundary_overlap
                else "replaced" if before < after else "unchanged"
            ),
        })
    return mapped
