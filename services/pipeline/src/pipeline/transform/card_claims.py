"""Conservative checks for clear contradictions in newly generated card prose."""

import re
from decimal import Decimal

from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_schema import DateEntry, NoticeSummary

_AGE = re.compile(r"(?<!\d)(\d{1,3})\s*세")
_AGE_RANGE = re.compile(
    r"(?<!\d)(\d{1,3})\s*(?:세)?\s*(?:[~～〜–-]|부터|에서)\s*(\d{1,3})\s*세"
)
_AGE_BOUND = re.compile(r"(?<!\d)(\d{1,3})\s*세\s*(이상|초과|이하|미만|부터|까지)")
_AREA = re.compile(
    r"[가-힣\d]+(?:시|도|구|동|읍|면|군)"
    r"(?=\s|$|[,.!?;；()]|(?:에|의|에서|거주|주민))"
)
_RESIDENT_AREA = re.compile(
    r"([가-힣\d]+(?:시|도|구|동|읍|면|군))\s*(?:에\s*)?"
    r"(?:거주|주민|구민|시민|도민)"
)
_UNRESTRICTED = re.compile(r"누구나|모든\s*(?:사람|주민|시민|구민)|제한\s*없이")
_WAIVED_ELIGIBILITY = re.compile(
    r"(?:거주지|지역|나이|연령|자격|조건)[^.!?;；]{0,25}(?:관계없이|무관|상관없이)"
)
_MONEY = re.compile(
    r"(?<![\d.,])((?:\d[\d,]*(?:\.\d+)?\s*(?:만|천)\s*)+"
    r"(?:\d[\d,]*(?:\.\d+)?\s*)?|\d[\d,]*(?:\.\d+)?\s*)원"
)
_MONEY_PART = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(만|천)?")
_FEE_WORD = re.compile(r"참가비|수강료|이용료|비용|요금|회비|납부|지원금|지원액|수당|보조금")
_FREE = re.compile(
    r"(?:무료|무상)(?!\s*(?:가|는|이)?\s*(?:아니|아닌|아님))|비용\s*(?:이|은)?\s*없"
)
_WAIVER = re.compile(r"무료|무상|면제|감면|지원")
_REQUIRED = re.compile(r"반드시|필수|의무")
_OPTIONAL = re.compile(
    r"선택(?!\s*(?:이|은|이)?\s*(?:아니|아닌|아님))|하지\s*않아도|안\s*해도|생략(?:할\s*수|해도)"
)
_NOT_REQUIRED = re.compile(r"필수(?:가|는|로)?\s*(?:아니|아님)|의무(?:가|는)?\s*(?:아니|없)")
_DATE = re.compile(
    r"(?<!\d)(?:(?P<year>\d{4})\s*(?:년|[./-])\s*)?"
    r"(?P<month>\d{1,2})\s*(?:월|[./-])\s*(?P<day>\d{1,2})(?:일)?(?!\d)"
)
_TIME = re.compile(
    r"(?<!\d)(?:(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)(?!\d)"
    r"|(?P<period>오전|오후)\s*(?P<korean_hour>1[0-2]|0?\d)\s*시"
    r"(?:\s*(?P<korean_minute>[0-5]?\d)\s*분)?"
    r"|(?P<bare_hour>[01]?\d|2[0-3])\s*시"
    r"(?:\s*(?P<bare_minute>[0-5]?\d)\s*분)?)"
)
_REFUND_RESTRICTION = re.compile(r"환불[^.!?;；]{0,20}(?:불가|불가능|제한|금지)")
_CANCELLED_CLASS = re.compile(r"(?:수업|교육|행사|강좌|운영|강의|프로그램)[^.!?;；]{0,12}취소")
_FULL_REFUND = re.compile(r"전액\s*환불|모두\s*(?:환불|돌려)|전체\s*(?:환불|반환)")
_NEGATED_FULL_REFUND = re.compile(
    r"(?:전액\s*환불|모두\s*(?:환불|돌려)|전체\s*(?:환불|반환))"
    r"[^\r\n.!?;；]{0,20}(?:불가|불가능|금지|받을\s*수\s*없|되지\s*않|하지\s*않)"
)
_SCHEDULE_ROLES = {
    "application": "신청|접수|모집|예약",
    "event": "행사|개최|캠프|전시|공연|축제",
    "operation": "운영",
    "payment": "납부|납입",
    "submission": "제출",
    "effective": "시행",
    "disruption": "중단",
    "result": "발표",
}
_SCHEDULE_ROLE = re.compile(
    "(?:" + "|".join(f"(?P<{kind}>{words})" for kind, words in _SCHEDULE_ROLES.items())
    + r")\s*(?:기간|기한|일정|일시|시간|일)?\s*(?:은|는|이|가|[:：])\s*"
)
_FEE_GROUP = re.compile(
    r"(?P<group>[가-힣][가-힣\d]{1,19})(?:\s*\([^)]{0,40}\))?"
    r"\s*(?:께는|에게는|에게|께|은|는|이|가|의)?\s*$"
)


def _field_facts(summary: NoticeSummary, notice: NoticeInput, field: str) -> list[str]:
    """Use field values only when their text references still match supplied text."""
    sources = [notice.body_text, *(item.text for item in notice.attachments)]
    references = [
        item.excerpt for item in summary.evidence
        if item.field == field
        and item.source_type == "text"
        and item.source_id is None
        and item.page is None
        and item.verification == "text_matched"
        and any(item.excerpt in source for source in sources)
    ]
    if not references:
        return []
    value = getattr(summary, field)
    if isinstance(value, str):
        return [value, *references]
    if field == "notes":
        return [*value, *references]
    if field == "dates":
        return [
            *(text for entry in value for text in (
                entry.text, entry.start_date, entry.end_date, entry.start_time, entry.end_time,
            ) if text),
            *references,
        ]
    return references


def _money_values(text: str) -> set[Decimal]:
    amounts = set()
    for match in _MONEY.finditer(text):
        amount = sum(
            Decimal(part[1].replace(",", "")) * {None: 1, "천": 1_000, "만": 10_000}[part[2]]
            for part in _MONEY_PART.finditer(match[1])
        )
        amounts.add(amount)
    return amounts


def _age_values(text: str) -> set[int]:
    values = {int(match[1]) for match in _AGE.finditer(text)}
    for match in _AGE_RANGE.finditer(text):
        values.update((int(match[1]), int(match[2])))
    return values


def _age_bounds(text: str) -> set[tuple[int, str]]:
    meanings = {"이상": ">=", "부터": ">=", "초과": ">", "이하": "<=", "까지": "<=", "미만": "<"}
    bounds = {(int(match[1]), meanings[match[2]]) for match in _AGE_BOUND.finditer(text)}
    for match in _AGE_RANGE.finditer(text):
        bounds.update(((int(match[1]), ">="), (int(match[2]), "<=")))
    return bounds


def _age_bound_changed(text: str, source: str) -> bool:
    source_bounds = _age_bounds(source)
    return any(
        (age, bound) not in source_bounds
        and any(source_age == age for source_age, _ in source_bounds)
        for age, bound in _age_bounds(text)
    )


def _date_values(text: str) -> set[tuple[int | None, int, int]]:
    values = set()
    for match in _DATE.finditer(text):
        month, day = int(match["month"]), int(match["day"])
        if 1 <= month <= 12 and 1 <= day <= 31:
            values.add((int(match["year"]) if match["year"] else None, month, day))
    return values


def _time_values(text: str) -> set[tuple[int, int]]:
    values = set()
    for match in _TIME.finditer(text):
        if match["hour"] is not None:
            values.add((int(match["hour"]), int(match["minute"])))
        elif match["korean_hour"] is not None:
            hour = int(match["korean_hour"]) % 12
            if match["period"] == "오후":
                hour += 12
            values.add((hour, int(match["korean_minute"] or 0)))
        else:
            values.add((int(match["bare_hour"]), int(match["bare_minute"] or 0)))
    return values


def _area_key(value: str) -> str:
    return re.sub(r"(?:특별자치|특별|광역)시$", "시", value)


def _audience_reasons(text: str, source: str, scope: str) -> list[str]:
    reasons = []
    source_ages = _age_values(source)
    card_ages = _age_values(text)
    if card_ages - source_ages:
        reasons.append("card_audience_age_added")
    if source_ages - card_ages:
        reasons.append("card_audience_age_omitted")
    if _age_bound_changed(text, source):
        reasons.append("card_audience_age_bound_changed")
    resident_areas = {_area_key(match[1]) for match in _RESIDENT_AREA.finditer(source)}
    card_resident_areas = {_area_key(match[1]) for match in _RESIDENT_AREA.finditer(text)}
    if resident_areas:
        if card_resident_areas - resident_areas:
            reasons.append("card_audience_resident_area_added")
        if not resident_areas & {_area_key(match[0]) for match in _AREA.finditer(text)}:
            reasons.append("card_audience_resident_area_omitted")
    areas = {match[0] for match in _AREA.finditer(source)}
    if scope in {"conditional", "specific"} or areas or source_ages:
        if _WAIVED_ELIGIBILITY.search(text) and not _WAIVED_ELIGIBILITY.search(source):
            reasons.append("card_audience_unrestricted")
        elif _UNRESTRICTED.search(text) and not _UNRESTRICTED.search(source):
            retains_areas = all(area in text for area in areas)
            retains_ages = source_ages <= card_ages
            conditional = bool(re.search(r"해당|충족|조건", text))
            if not (retains_areas and retains_ages and (areas or source_ages or conditional)):
                reasons.append("card_audience_unrestricted")
    return reasons


def _deadline_reasons(
    text: str,
    source: str,
    expected_range: tuple[tuple[int, int, int], tuple[int, int, int]] | None,
    expected_end_times: set[tuple[int, int]],
) -> list[str]:
    reasons = []
    allowed = _date_values(source)
    for year, month, day in _date_values(text):
        matches = {source_year for source_year, source_month, source_day in allowed
                   if (month, day) == (source_month, source_day)}
        explicit_years = matches - {None}
        if not matches or (year is not None and explicit_years and year not in explicit_years):
            reasons.append("card_deadline_date_added")
            break
    if _time_values(text) - _time_values(source):
        reasons.append("card_deadline_time_added")
    if expected_end_times - _time_values(text):
        reasons.append("card_deadline_end_time_omitted")
    tokens = list(_DATE.finditer(text))
    if expected_range is not None and len(tokens) == 2:
        first, last = tokens
        between = _TIME.sub("", text[first.end():last.start()])
        connector = re.match(r"\s*(?:부터|에서|[~～〜–-])", between)
        first_day, last_day = (int(first["month"]), int(first["day"])), (
            int(last["month"]), int(last["day"])
        )
        start, end = expected_range
        distinct = start[1:] != end[1:] or bool(first["year"] and last["year"])
        first_is_end = first_day == end[1:] and (
            first["year"] is None or int(first["year"]) == end[0]
        )
        last_is_start = last_day == start[1:] and (
            last["year"] is None or int(last["year"]) == start[0]
        )
        if connector and distinct and first_is_end and last_is_start:
            reasons.append("card_deadline_range_reversed")
    return reasons


def _schedule_markers(text: str, entries: list[DateEntry]) -> list[tuple[int, int, str]]:
    """Use explicit subjects/labels; an unlabelled date phrase is left unassigned."""
    markers = [(match.start(), match.end(), match.lastgroup)
               for match in _SCHEDULE_ROLE.finditer(text)]
    labels: dict[str, set[str]] = {}
    for entry in entries:
        if entry.label and entry.label not in {"일정", "기간", "기한", "시간", "일시", "일"}:
            labels.setdefault(entry.label, set()).add(entry.kind)
    for label, kinds in labels.items():
        if len(kinds) != 1:
            continue
        pattern = r"\s*".join(re.escape(part) for part in label.split())
        for match in re.finditer(pattern + r"\s*(?:은|는|이|가|[:：])\s*", text):
            markers.append((match.start(), match.end(), next(iter(kinds))))
    selected = []
    # A source's complete label takes precedence over a generic word inside it.
    for start, end, kind in sorted(set(markers), key=lambda item: (item[0], -item[1])):
        if selected and start < selected[-1][1]:
            continue
        selected.append((start, end, kind))
    return selected


def _period_matches(tokens: list[re.Match[str]], entry: DateEntry, *, reversed_order=False) -> bool:
    if entry.start_date is None or entry.end_date is None:
        return False
    dates = [entry.start_date, entry.end_date]
    if reversed_order:
        dates.reverse()
    return all(
        (int(token["month"]), int(token["day"])) == (int(day[5:7]), int(day[8:10]))
        and (token["year"] is None or int(token["year"]) == int(day[:4]))
        for token, day in zip(tokens, dates, strict=True)
    )


def _deadline_relation_reasons(text: str, entries: list[DateEntry]) -> list[str]:
    """Compare explicit role-labelled ranges, without assigning ambiguous prose."""
    reasons = []
    markers = _schedule_markers(text, entries)
    for index, (_, end, kind) in enumerate(markers):
        stop = markers[index + 1][0] if index + 1 < len(markers) else len(text)
        segment = text[end:stop]
        tokens = list(_DATE.finditer(segment))
        if len(tokens) != 2:
            continue
        between = _TIME.sub("", segment[tokens[0].end():tokens[1].start()])
        if not re.match(r"\s*(?:부터|에서|[~～〜–-])", between):
            continue
        same_role = [entry for entry in entries if entry.kind == kind]
        if not same_role or any(_period_matches(tokens, entry) for entry in same_role):
            continue
        if any(_period_matches(tokens, entry) for entry in entries if entry.kind != kind):
            reasons.append("card_deadline_role_changed")
        elif any(
            entry.start_date != entry.end_date
            and _period_matches(tokens, entry, reversed_order=True)
            for entry in same_role
        ):
            reasons.append("card_deadline_range_reversed")
    return reasons


def _group_amounts(text: str) -> dict[tuple[str, str], set[Decimal]]:
    """Collect a narrow explicit 'recipient [age] fee/support amount' relation."""
    groups: dict[tuple[str, str], set[Decimal]] = {}
    for amount in _MONEY.finditer(text):
        prefix = text[max(0, amount.start() - 100):amount.start()]
        prefix = re.split(r"[\n.!?;；]", prefix)[-1]
        fees = list(_FEE_WORD.finditer(prefix))
        if not fees:
            continue
        fee = fees[-1]
        group = _FEE_GROUP.search(prefix[:fee.start()].strip())
        if group is None:
            continue
        name = re.sub(r"(?:께는|에게는|에게|께|은|는|이|가|의)$", "", group["group"])
        kind = "support" if fee[0] in {"지원금", "지원액", "수당", "보조금"} else "fee"
        groups.setdefault((name, kind), set()).update(_money_values(amount[0]))
    return groups


def _group_amount_changed(text: str, source: str) -> bool:
    source_groups = _group_amounts(source)
    for (group, kind), amounts in _group_amounts(text).items():
        expected = source_groups.get((group, kind), set())
        if len(expected) != 1 or len(amounts) != 1 or amounts == expected:
            continue
        # A different known recipient's exact amount is a clear reassignment.
        if any(amounts == other_amounts and other_kind == kind and other_group != group
               for (other_group, other_kind), other_amounts in source_groups.items()):
            return True
    return False


def _notes_reasons(text: str, source: str) -> list[str]:
    reasons = []
    source_amounts = _money_values(source)
    card_amounts = _money_values(text)
    if _FEE_WORD.search(source):
        if source_amounts - card_amounts:
            reasons.append("card_notes_amount_omitted")
        if card_amounts - source_amounts:
            reasons.append("card_notes_amount_added")
        if any(amount > 0 for amount in source_amounts) and _FREE.search(text):
            if not _WAIVER.search(source) or _conditional_free_weakened(text, source):
                reasons.append("card_notes_false_free")
    elif card_amounts and _FEE_WORD.search(text):
        reasons.append("card_notes_amount_added")
    if _group_amount_changed(text, source):
        reasons.append("card_notes_group_amount_changed")
    source_ages = _age_values(source)
    card_ages = _age_values(text)
    if card_ages - source_ages:
        reasons.append("card_notes_age_added")
    if source_ages - card_ages:
        reasons.append("card_notes_age_omitted")
    if _age_bound_changed(text, source):
        reasons.append("card_notes_age_bound_changed")
    if (
        _REFUND_RESTRICTION.search(source)
        and _CANCELLED_CLASS.search(source)
        and _FULL_REFUND.search(source)
        and not _NEGATED_FULL_REFUND.search(source)
        and (
            not (_CANCELLED_CLASS.search(text) and _FULL_REFUND.search(text))
            or _NEGATED_FULL_REFUND.search(text)
        )
    ):
        reasons.append("card_notes_refund_exception_omitted")
    return reasons


def _conditional_free_weakened(text: str, source: str) -> bool:
    """Do not let a source's conditional fee waiver approve universal free cost."""
    source_clauses = re.split(r"[\n.!?;；]", source)
    card_clauses = re.split(r"[\n.!?;；]", text)
    full_waiver = re.compile(r"면제(?!\s*(?:가|는|이)?\s*(?:아니|아닌|아님|불가))")
    free_sources = [
        clause for clause in source_clauses
        if _FREE.search(clause) or full_waiver.search(clause)
    ]
    if not free_sources:
        return True  # Subsidy/discount wording alone does not establish free cost.
    qualifiers = []
    for clause in free_sources:
        fee = re.search(r"참가비|수강료|이용료|비용|요금|회비", clause)
        if fee is None:
            return False  # This narrow check cannot derive a qualified fee relationship.
        prefix = clause[:fee.start()].strip(" :：")
        prefix = re.sub(r"(?:은|는|이|가)$", "", prefix).strip()
        if not prefix:
            return False  # The source itself declares the fee free without a qualifier.
        qualifiers.append(prefix)
    unrestricted = re.compile(
        r"누구나|모든\s*(?:참가자|신청자|수강생|대상자|사람|주민|시민|구민)|"
        r"(?:조건|제한)\s*없이"
    )
    return any(
        unrestricted.search(clause)
        or not any(qualifier in clause for qualifier in qualifiers)
        for clause in card_clauses if _FREE.search(clause)
    )


def _required_action_weakened(text: str, source: str) -> bool:
    """Scope optional wording to the required document/action's specific nouns."""
    weak_clauses = [
        clause for clause in re.split(r"[\n.!?;；]", text)
        if _NOT_REQUIRED.search(clause) or _OPTIONAL.search(clause)
    ]
    if not weak_clauses:
        return False
    generic = {
        "신청", "신청자", "참여", "참여자", "제출", "준비", "필수", "의무",
        "반드시", "할일", "사람", "대상자", "해야", "필요", "여부", "행동",
    }
    nouns = {
        re.sub(r"(?:은|는|이|가|을|를|에|의|와|과|도)$", "", token)
        for token in re.findall(r"[가-힣]{2,}", source)
    } - generic
    if any(any(noun and noun in clause for noun in nouns) for clause in weak_clauses):
        return True
    # Preserve the earlier broad fallback when no distinct mandatory claim is present.
    return not _REQUIRED.search(text)


def card_claim_review_reasons(summary: NoticeSummary, notice: NoticeInput) -> tuple[str, ...]:
    """Return safe codes for a bounded set of clear card/source inconsistencies.

    Call after grounding. Only field references marked text_matched and still
    literally found in body/attachment text establish these source facts. This
    does not inspect media or change any source field, quote, or card wording.

    Checks cover broad eligibility added to restricted audiences, clear resident
    area substitutions/omissions, explicit age numbers added/omitted and age-bound
    changes; new numeric dates/clocks, omitted explicit closing clocks and a
    reversed explicit range when there is exactly one source period; weakening a
    required document/action despite an unrelated mandatory statement;
    added/omitted currency amounts and false/unqualified free-cost claims; and omission of a
    full-refund exception when a class/event is cancelled. Equivalent numeric
    currency units and 24-hour/Korean clocks are accepted. Natural paraphrases
    need not quote the source verbatim. Explicit role-labelled date ranges and
    explicit recipient-labelled fee/support amounts are compared independently.
    Ambiguous group relationships and arbitrary Korean claims remain outside
    this bounded check. It is not complete
    semantic validation and cannot establish that every other card claim is true.
    """
    cards = summary.card_summaries
    if cards is None:
        return ()
    reasons = []
    audience = _field_facts(summary, notice, "audience")
    if cards.audience is not None and audience:
        reasons.extend(_audience_reasons(
            cards.audience, "\n".join(audience), summary.audience_scope,
        ))
    dates = _field_facts(summary, notice, "dates")
    if cards.deadline is not None and dates:
        expected_range = None
        if len(summary.dates) == 1:
            entry = summary.dates[0]
            if entry.start_date and entry.end_date and entry.start_date != entry.end_date:
                expected_range = (
                    tuple(int(part) for part in entry.start_date.split("-")),
                    tuple(int(part) for part in entry.end_date.split("-")),
                )
        expected_end_times = {
            tuple(int(part) for part in entry.end_time.split(":"))
            for entry in summary.dates
            if entry.kind in {"application", "submission", "payment"}
            and entry.end_time is not None
        }
        expected_end_times &= _time_values("\n".join(dates))
        reasons.extend(_deadline_reasons(
            cards.deadline, "\n".join(dates), expected_range, expected_end_times,
        ))
        reasons.extend(_deadline_relation_reasons(cards.deadline, summary.dates))
    action = _field_facts(summary, notice, "action")
    if cards.action is not None and action and (
        summary.action_requirement == "required" or _REQUIRED.search("\n".join(action))
    ):
        if _required_action_weakened(cards.action, "\n".join(action)):
            reasons.append("card_action_required_weakened")
    notes = _field_facts(summary, notice, "notes")
    if cards.notes is not None and notes:
        reasons.extend(_notes_reasons(cards.notes, "\n".join(notes)))
    return tuple(dict.fromkeys(reasons))
