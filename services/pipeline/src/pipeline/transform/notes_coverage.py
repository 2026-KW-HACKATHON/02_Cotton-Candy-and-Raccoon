"""Find omitted, explicit text conditions without judging model meaning with an LLM.

This deliberately checks a small set of direct clauses. Ambiguous tables, historical
examples, mixed notices and multiple competing conditions are outside its scope.
PDF/image contents are not available here and must not be treated as checked text.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_schema import NoticeSummary

ConditionKind = Literal["cost", "support", "selection"]


@dataclass(frozen=True, slots=True)
class MissingNoteCondition:
    """A source quote for retry feedback; keep source contents out of object logs."""

    kind: ConditionKind
    excerpt: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _Candidate:
    kind: ConditionKind
    source: str = field(repr=False)
    start: int
    end: int
    anchors: tuple[tuple[int, int], ...]

    @property
    def excerpt(self) -> str:
        return self.source[self.start : self.end]


_COST_LABEL = (
    r"참가비(?:용)?|참여비(?:용)?|수강료|이용료|입장료|관람료|"
    r"신청비(?:용)?|접수비(?:용)?|교육비|본인부담(?:금|액)|자부담(?:금|액)"
)
_MONEY = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?:만\s*)?원"
_COST = re.compile(
    r"(?P<label>" + _COST_LABEL + r")"
    r"(?:\s*[:：]\s*|\s*(?:은|는|이|가)\s*|\s+)"
    r"(?P<value>" + _MONEY + r"|무료|없음|면제)(?![가-힣\d])"
)
_MONEY_TOKEN = re.compile(_MONEY)
_CHARGE = re.compile(r"(?<![가-힣])(?:" + _COST_LABEL + r")(?:[은는이가])?(?=\s|[:：])")
_SUPPORT_VALUE = re.compile(r"전액|일부|무료|면제|\d+(?:\.\d+)?\s*%|" + _MONEY)
_SUPPORT_VERB = re.compile(r"지원|감면|면제|할인|무료")
_AUDIENCE = re.compile(
    r"대상|학생|주민|수급자|계층|가족|장애인|유공자|청년|어르신|노인|"
    r"참가자|신청자|수강생|이용자|"
    r"\d+\s*세\s*(?:이상|이하|미만|초과)"
)
_OVERFLOW = re.compile(
    r"(?:(?:모집)?정원|모집인원|인원)\s*(?:을\s*)?초과\s*"
    r"(?:신청\s*)?(?:할\s*|하는\s*|한\s*)?(?:시|경우)"
    r"|신청(?:자|인원)?(?:가|이)?\s*(?:모집)?정원(?:을)?\s*"
    r"초과(?:할|하는|한)?\s*(?:시|경우)"
)
_SELECTION_METHOD = re.compile(r"전산\s*추첨|추첨|선착순")
_SELECTION_VERB = re.compile(r"선정|선발|결정|배정|접수|마감")
_UNIT_BOUNDARY = re.compile(r"\n|[;；※•●○■□]")
_PREFIX = re.compile(r"^\s*(?:[-–—]\s*)?")
# A family name containing "가정" is not a hypothetical section. Only an
# assumption heading or an explicit "assume" construction suppresses conditions.
_UNSAFE_SOURCE = re.compile(
    r"변경\s*전|종전|예시|예제|예를\s*들|샘플|사업별|비교표|"
    r"구분\s*[|\t]|(?:^|\n)\s*(?:이전|과거)\s*(?:내용|조건|금액|안내)"
    r"|^가정(?:\s*[:：]|\s*$)"
    r"|(?<![가-힣A-Za-z0-9])가정\s*(?:하[다고면여는며]|"
    r"한(?:다|\s*(?:경우|때))|합니다|합시다|해(?:서|도|보)?|"
    r"했(?:다|습니다|으며)|할\s*(?:경우|때))"
    r"|(?:라고|으로|로)\s*가정(?=\s|$|하|한|합|해|했|할)"
)
_CURRENT_HEADING = re.compile(
    r"(?:변경\s*후|현행|현재|최종\s*안내|확정(?:안)?)"
    r"(?:\s*(?:내용|조건|금액|안내|비용))?\s*[:：]?"
)
_BUSINESS_HEADING = re.compile(
    r"(?m)^\s*(?:[■□•●○]\s*)?([^\n:：|]{1,40}(?:사업|프로그램|강좌))\s*$"
)
_UNSAFE_UNIT = re.compile(r"문의|연락|담당|☎|https?://|www\.|[|\t]")
_NEGATED = re.compile(
    r"아님|아니|아닙|불가(?!피)|불가능|하지\s*않|하지\s*못|없(?:음|습니다)|"
    r"(?:지원|감면|할인|추첨|선착순)(?:은|는|이|가)?\s*(?:없|안\s*함)"
)
_COMPLEX_COST = re.compile(r"지원|감면|할인|환급|환불|보증금|대상|경우|조건")
_UNCERTAIN_SUPPORT = re.compile(r"예정|계획|검토|여부|가능|신청|요청|승인|조건")
_UNCONFIRMED_TARGET = re.compile(r"참고|확인|추후|미정")


def _units(source: str) -> list[tuple[int, int, str]]:
    """Return original clause offsets; do not join lines or table cells."""
    result = []
    start = 0
    for end in [*(match.start() for match in _UNIT_BOUNDARY.finditer(source)), len(source)]:
        raw = source[start:end]
        prefix = _PREFIX.match(raw)
        assert prefix is not None
        begin = start + prefix.end()
        finish = end - (len(raw) - len(raw.rstrip()))
        if begin < finish:
            result.append((begin, finish, source[begin:finish]))
        start = end + 1
    return result


def _candidate(
    kind: ConditionKind,
    source: str,
    start: int,
    end: int,
    matches: tuple[re.Match[str], ...],
) -> _Candidate:
    return _Candidate(
        kind,
        source,
        start,
        end,
        tuple((start + match.start(), start + match.end()) for match in matches),
    )


def _cost(source: str, start: int, end: int, unit: str) -> _Candidate | None:
    match = _COST.fullmatch(unit)
    if match is None:
        # A short trailing description is harmless only when there are no other
        # amounts, eligibility rules or changes to the payable cost in this clause.
        match = _COST.match(unit)
        if match is None or _COMPLEX_COST.search(unit[match.end() :]):
            return None
        if unit[match.end() :].strip(" .。()[]"):
            return None
    if len(_MONEY_TOKEN.findall(unit)) > 1:
        return None
    # Both the cost role and its value must appear in the same shown quote.
    # A bare amount with unrelated award evidence cannot establish a payable fee.
    return _Candidate(
        "cost",
        source,
        start,
        end,
        (
            (start + match.start("label"), start + match.end("label")),
            (start + match.start("value"), start + match.end("value")),
        ),
    )


def _support(source: str, start: int, end: int, unit: str) -> _Candidate | None:
    charge = _CHARGE.search(unit)
    if charge is None:
        return None
    target = unit[: charge.start()].strip()
    if (
        not target
        or not _AUDIENCE.search(target)
        or re.search(r"[:：/,]", target)
        or _UNCONFIRMED_TARGET.search(target)
    ):
        return None
    value = _SUPPORT_VALUE.search(unit, charge.end())
    if value is None:
        return None
    verb = _SUPPORT_VERB.search(unit, value.start())
    if verb is None or _UNCERTAIN_SUPPORT.search(unit[charge.end() :]):
        return None
    # Multiple values/verbs may describe different groups or competing benefits.
    if len(_SUPPORT_VALUE.findall(unit[charge.end() :])) != 1:
        return None
    target_start = len(unit[: charge.start()]) - len(unit[: charge.start()].lstrip())
    target_end = charge.start() - (
        len(unit[: charge.start()]) - len(unit[: charge.start()].rstrip())
    )
    if target_end and unit[target_end - 1] in "은는이가":
        target_end -= 1
    return _Candidate(
        "support",
        source,
        start,
        end,
        (
            (start + target_start, start + target_end),
            (start + value.start(), start + value.end()),
            (start + verb.start(), start + verb.end()),
        ),
    )


def _selection(source: str, start: int, end: int, unit: str) -> _Candidate | None:
    condition = _OVERFLOW.search(unit)
    if condition is None:
        return None
    method = _SELECTION_METHOD.search(unit, condition.end())
    if method is None or _SELECTION_METHOD.search(unit, method.end()):
        return None
    verb = _SELECTION_VERB.search(unit, method.end())
    if verb is None:
        return None
    return _candidate("selection", source, start, end, (condition, method, verb))


def _covered(candidate: _Candidate, notes: list[str]) -> bool:
    for note in notes:
        for match in re.finditer(re.escape(note), candidate.source):
            if all(
                match.start() <= start and end <= match.end() for start, end in candidate.anchors
            ):
                return True
    return False


def find_missing_note_conditions(
    summary: NoticeSummary, notice: NoticeInput
) -> tuple[MissingNoteCondition, ...]:
    """Return only unambiguous, explicit text conditions absent from shown notes.

    A missing condition is feedback, not permission to synthesize new summary text.
    No text is extracted from visual files and evidence-only mentions do not count.
    """
    if summary.category == "mixed":
        return ()
    sources = [notice.body_text, *(attachment.text for attachment in notice.attachments)]
    candidates: list[_Candidate] = []
    for source in sources:
        if len(set(_BUSINESS_HEADING.findall(source))) > 1:
            continue
        unsafe_section = False
        for start, end, unit in _units(source):
            if _CURRENT_HEADING.fullmatch(unit):
                unsafe_section = False
                continue
            if _UNSAFE_SOURCE.search(unit):
                unsafe_section = True
                continue
            if unsafe_section:
                continue
            if _UNSAFE_UNIT.search(unit):
                continue
            # A directly labelled "cost: none" is still an explicit cost value.
            direct_cost = _COST.fullmatch(unit)
            explicit_none = direct_cost is not None and direct_cost.group("value") == "없음"
            if _NEGATED.search(unit) and not explicit_none:
                continue
            for candidate in (
                _cost(source, start, end, unit),
                _support(source, start, end, unit),
                _selection(source, start, end, unit),
            ):
                if candidate is not None:
                    candidates.append(candidate)

    missing = []
    for kind in ("cost", "support", "selection"):
        found = [candidate for candidate in candidates if candidate.kind == kind]
        # Do not map several businesses/groups/documents onto one shared notes list.
        # A duplicated identical clause is safe; distinct clauses need richer IDs.
        if len({candidate.excerpt for candidate in found}) != 1:
            continue
        if any(_covered(candidate, summary.notes) for candidate in found):
            continue
        missing.append(MissingNoteCondition(kind, found[0].excerpt))
    return tuple(missing)
