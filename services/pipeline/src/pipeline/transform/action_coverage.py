"""Find omitted, explicit textual signup conditions for correction feedback.

This check returns source quotations, never a new action or an inferred obligation.
In particular, a signup contact for one program does not make its whole event
mandatory. Empty text and media-only notices provide no locally checked conditions.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_schema import NoticeSummary

ActionConditionKind = Literal[
    "preregistration", "required_application", "onsite_registration"
]
MAX_CONDITION_EXCERPT = 240
MAX_MISSING_ACTION_CONDITIONS = 8


@dataclass(frozen=True, slots=True)
class MissingActionCondition:
    """An original source quote for feedback, kept out of object logs."""

    kind: ActionConditionKind
    excerpt: str = field(repr=False)


_SIGNUP = re.compile(
    r"(?P<preregistration>사전\s*(?:신청|접수))"
    r"|(?P<required_application>신청(?:은|이)?\s*필수(?!서류|품))"
    r"|(?P<onsite_registration>현장\s*접수)"
)
_UNIT_BOUNDARY = re.compile(r"\n|[;；※•●○■□◼]")
_PREFIX = re.compile(r"^\s*(?:[-–—*]\s*)?")
_UNSAFE = re.compile(r"예시|예제|샘플|변경\s*전|종전|이전\s*안내|과거\s*안내|[|\t]")
_CURRENT_HEADING = re.compile(r"(?:변경\s*후|현행|현재|최종\s*안내)\s*[:：]?")
_TAIL_PREFIX = re.compile(
    r"^\s*[:：(\[]?\s*[은는이가을를]?\s*"
    r"(?:(?:별도로|별도|일절|따로|전혀)\s*)?"
)
_NEGATED = re.compile(
    r"(?:불필요|불가(?!피)|불가능|없(?:이|음|습니다|어요|고|어도)?"
    r"|필요(?:가|는)?\s*없|필요하지\s*않|하지\s*않|하지\s*못"
    r"|(?:하|받)지\s*않|안\s*(?:받|함|해|하)|미실시|미운영"
    r"|(?:하|할|받을)\s*필요(?:가|는)?\s*없"
    r"|필수(?:가|는)?\s*(?:아니|아님|아닙)|아니|아님|아닙)"
)
_HONORIFIC_NEGATED = re.compile(
    r"(?:하실|하셔야\s*할)\s*(?:필요|의무)(?:가|는|까지는)?\s*없"
    r"|하실\s*수(?:는|가)?\s*없"
    r"|하시지\s*않"
    r"|하셔도\s*(?:되(?:지|지는)\s*않|안\s*(?:되|됩))"
    r"|하시면\s*안\s*(?:되|됩)"
    r"|하시는\s*(?:것|건)(?:은|이)?\s*(?:필수|의무)(?:가|는)?\s*(?:아니|아님|아닙)"
)
_CONDITIONAL_NEGATION_SUFFIX = re.compile(
    r"^\s*(?:(?:으)?(?:면|시면)|(?:하|되)(?:면|시면)"
    r"|(?:(?:았|었)?(?:을|은|는)|으실|으신|실|신|셨을)\s*(?:경우|때|시|분))"
)
_UNCONFIRMED = re.compile(
    r"^(?:여부|가능한지|필요한지|필요\s*여부|가능\s*여부|미정|검토"
    r"|필수인지|필수\s*여부"
    r"|필요한가|필요합니까|가능한가|가능합니까|해야\s*(?:하는지|하나요|되는지|되나요))"
)
_COVERAGE_PATTERNS: dict[ActionConditionKind, re.Pattern[str]] = {
    "preregistration": re.compile(r"사전\s*(?:신청|접수)|미리\s*신청"),
    "required_application": re.compile(
        r"신청(?:은|이)?\s*필수|필수(?:로)?\s*신청|반드시\s*신청"
        r"|신청(?:해야|하셔야)"
    ),
    "onsite_registration": re.compile(r"현장\s*접수|현장에서\s*(?:신청|접수)"),
}
_PROGRAM_CONTEXT = re.compile(
    r"([가-힣A-Za-z0-9][가-힣A-Za-z0-9 ‘'’()·_-]{0,40}"
    r"(?:상상데이|프로그램|체험|강좌|투어|만들기))"
    r"(?:은|는|이|가|만|도|을|를|에서|에만|에)?\s*$"
)
_GENERIC_CONTEXT = re.compile(r"선착순|참여|참가|희망|대상|모든|일부|각\s|수강|신청")
_COVERAGE_UNIT_BOUNDARY = re.compile(r"[\n.!?。;；,，]|하지만|다만|반면|그리고")
_NORMALIZE = re.compile(r"[\s‘'’\"“”()·_-]+")


def _units(source: str) -> list[str]:
    result = []
    for raw in _UNIT_BOUNDARY.split(source):
        prefix = _PREFIX.match(raw)
        assert prefix is not None
        unit = raw[prefix.end() :].rstrip()
        if unit:
            result.append(unit)
    return result


def _negated(unit: str, match: re.Match[str], next_start: int) -> bool:
    # Stop at the next signup clause so "사전 신청 없이 현장 접수" still
    # reports the explicit onsite condition, rather than negating both.
    tail = unit[match.end() : next_start]
    tail = re.split(r"[,，.!?。]|(?:하지만|다만|대신)", tail, maxsplit=1)[0]
    tail = _TAIL_PREFIX.sub("", tail, count=1)
    for pattern in (_NEGATED, _HONORIFIC_NEGATED):
        negative = pattern.match(tail)
        if negative is not None:
            # "신청하지 않으면 참여 불가" states a condition, not that signup
            # is absent. Keep that original clause for feedback without deriving
            # a required/optional action from the conditional negative wording.
            return _CONDITIONAL_NEGATION_SUFFIX.match(tail[negative.end() :]) is None
    return _UNCONFIRMED.match(tail) is not None


def _program_context(unit: str, match: re.Match[str]) -> str | None:
    prefix = re.split(r"[:：,，]", unit[: match.start()])[-1].strip()
    context = _PROGRAM_CONTEXT.search(prefix)
    if context is None or _GENERIC_CONTEXT.search(context.group(1)):
        return None
    name = context.group(1)
    # These words may be a generic descriptor after a program name. Keep only
    # the explicit name, rather than demanding the same descriptor in prose.
    name = re.sub(r"\s+(?:프로그램|체험|강좌)$", "", name)
    return name or None


def _covered(kind: ActionConditionKind, context: str | None, texts: list[str]) -> bool:
    for text in texts:
        for unit in _COVERAGE_UNIT_BOUNDARY.split(text):
            matches = list(_COVERAGE_PATTERNS[kind].finditer(unit))
            for index, match in enumerate(matches):
                next_start = matches[index + 1].start() if index + 1 < len(matches) else len(unit)
                if _negated(unit, match, next_start):
                    continue
                if context is None or _NORMALIZE.sub("", context) in _NORMALIZE.sub("", unit):
                    return True
    return False


def _excerpt(unit: str, match: re.Match[str]) -> str:
    if len(unit) <= MAX_CONDITION_EXCERPT:
        return unit
    # Preserve a contiguous source slice, including nearby program context.
    start = max(0, match.start() - 80)
    end = min(len(unit), start + MAX_CONDITION_EXCERPT)
    return unit[start:end]


def find_missing_action_conditions(
    summary: NoticeSummary, notice: NoticeInput
) -> tuple[MissingActionCondition, ...]:
    """Return explicit signup conditions absent from retained actions and notes.

    Known action/notes fields and their card prose both count as retained content;
    an evidence-only mention does not. Matching an explicit program name prevents
    signup for one program covering a different program. Historical/example
    sections, negated signup and ambiguous mixed notices are not inferred.
    """
    if summary.category == "mixed":
        return ()
    retained = [*(summary.notes), *([summary.action] if summary.action else [])]
    if summary.card_summaries is not None:
        retained.extend(
            text
            for text in (summary.card_summaries.action, summary.card_summaries.notes)
            if text is not None
        )
    result: list[MissingActionCondition] = []
    seen: set[tuple[ActionConditionKind, str]] = set()
    for source in [notice.body_text, *(item.text for item in notice.attachments)]:
        unsafe_section = False
        for unit in _units(source):
            if _CURRENT_HEADING.fullmatch(unit):
                unsafe_section = False
                continue
            if _UNSAFE.search(unit):
                # Tables and inline examples are ambiguous. Only headings scope
                # later lines; a later historical aside cannot hide earlier text.
                if len(unit) <= 30 and not _SIGNUP.search(unit):
                    unsafe_section = True
                continue
            if unsafe_section:
                continue
            matches = list(_SIGNUP.finditer(unit))
            for index, match in enumerate(matches):
                next_start = matches[index + 1].start() if index + 1 < len(matches) else len(unit)
                if _negated(unit, match, next_start):
                    continue
                kind: ActionConditionKind = next(
                    name
                    for name in _COVERAGE_PATTERNS
                    if match.group(name) is not None
                )
                if _covered(kind, _program_context(unit, match), retained):
                    continue
                excerpt = _excerpt(unit, match)
                key = (kind, excerpt)
                if key not in seen:
                    seen.add(key)
                    result.append(MissingActionCondition(kind, excerpt))
                    if len(result) == MAX_MISSING_ACTION_CONDITIONS:
                        return tuple(result)
    return tuple(result)
