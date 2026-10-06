"""Check text claims and keep distinctly marked references to supplied visual files."""

import re
from dataclasses import dataclass
from datetime import date, datetime, time

from pipeline.transform.file_only_summary import (
    REVIEW_NOTE,
    is_file_only_notice,
    preserve_file_only_summary,
    reversed_end_fields,
)
from pipeline.transform.notice_input import KST, NoticeInput
from pipeline.transform.summary_schema import (
    FIELD_TEXT_LIMITS,
    DateEntry,
    Evidence,
    MediaSource,
    NoticeSummary,
    evidence_reference_valid,
    validate_evidence,
)

DATE_TOKEN = re.compile(
    r"(?<!\d)(?:(?P<year>\d{4})\s*(?:년|[./-])\s*)?"
    r"(?P<month>\d{1,2})\s*(?:월|[./-])\s*(?P<day>\d{1,2})(?:일)?(?!\d)"
)
DATE_KIND_WORDS = {
    "application": ("신청", "접수", "모집"),
    "event": ("행사", "축제", "공연", "개최", "일시", "캠프", "전시", "체험"),
    "operation": ("수업", "운영", "교육", "강좌"),
    "payment": ("납부", "결제", "입금"),
    "submission": ("제출", "보완", "서류"),
    "effective": ("시행", "적용", "개정"),
    "disruption": ("통제", "휴관", "중단"),
    "result": ("결과", "발표", "선정", "합격"),
}
TIME_TOKEN = re.compile(
    r"(?<!\d)(?:(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)(?!\d)"
    r"|(?P<period>오전|오후)\s*(?P<korean_hour>1[0-2]|0?\d)\s*시"
    r"(?:\s*(?P<korean_minute>[0-5]?\d)\s*분)?)"
)
SCHEDULE_BOUNDARY = re.compile(
    r"\n|[;；]|(?<!\d)\.(?=\s|$)|[!?。](?=\s|$)|\s+/\s+|[•●○■□]"
    r"|(?=(?:"
    + "|".join(word for words in DATE_KIND_WORDS.values() for word in words)
    + r")(?:\s*(?:기간|일시|일정|시간|시작일|종료일|마감일))?\s*[:：])"
)
CANCELLED_NOTICE = re.compile(
    r"(?:행사|공연|축제|모집|접수|운영|강좌|교육|수업|사업|공고)"
    r"(?:이|가|은|는|을|를)?\s*(?:전면\s*)?취소"
)
NEGATED_CANCELLATION = re.compile(
    r"\s*(?:은|는|이|가)?\s*(?:신청|접수|방법|절차|가능|요청|문의|아님|아닌|아닙|아니|불가|없|되지\s*않|하지\s*않|시\b|될\s*경우|되는\s*경우|경우)"
)
ONGOING_INTAKE = re.compile(r"상시\s*(?:접수|모집|신청)")
NEGATED_INTAKE = re.compile(
    r"\s*(?:은|는|이|가)?\s*(?:불가|금지|중단|종료|마감|하지\s*않|하지\s*못|할\s*수\s*없|안\s*(?:되|됩)|아님|아닙|없)"
)
NEGATED_UPDATE = re.compile(
    r"(?:변경|수정|정정|연장)\s*(?:사항\s*)?(?:없|하지\s*않|하지\s*못|안\s*(?:되|됩)|불가|아님|아니|아닙)"
)
OBSOLETE_CONTEXT = re.compile(r"변경\s*전\s*[:：]")
RETRACTED_CANCELLATION = re.compile(
    r"취소\s*(?:공고|공지|안내)?\s*(?:를|을)?\s*(?:정정|철회)|정상\s*진행|예정대로\s*진행"
)
ENDED_ONGOING_INTAKE = re.compile(r"상시\s*(?:접수|모집|신청)\s*(?:였으나|이었으나)")
TABLE_LABELS = (
    "모집기간",
    "신청기간",
    "접수기간",
    "운영기간",
    "교육기간",
    "캠프기간",
    "행사기간",
    "납부기간",
    "결제기간",
    "제출기간",
    "결과발표",
    "사업명",
    "개최지",
    "행사장",
    "기간",
    "장소",
    "위치",
    "일시",
    "일정",
    "일자",
    "시간",
    "대상",
    "자격",
    "신청",
    "접수",
    "모집",
    "운영",
    "교육",
    "납부",
    "결제",
    "제출",
)
TABLE_LABEL_PATTERNS = tuple(
    (label, re.compile(r"(?<![가-힣])" + r"[^\S\r\n]*".join(label) + r"(?![가-힣])"))
    for label in TABLE_LABELS
)
INTAKE_WORDS = ("신청", "접수", "모집", "채용", "공모")
EVENT_WORDS = ("행사", "축제", "공연", "참여", "개방", "개최", "캠프", "전시", "체험")
NEGATED_CATEGORY_TERM = re.compile(
    r"\s*(?:은|는|이|가)?\s*(?:(?:참여|신청|접수)\s*)?"
    r"(?:불가|불가능|금지|불필요|필요(?:가)?\s*없|없이|하지\s*않|받지\s*않|없)"
)


def _normalize_table_labels(value: str) -> str:
    """Normalize known labels for comparison only; never change source spans/quotes."""
    for label, pattern in TABLE_LABEL_PATTERNS:
        value = pattern.sub(label, value)
    return value


def _intake_mentioned(value: str) -> bool:
    """A negated/no-application instruction is not a participant recruitment."""
    return any(
        not NEGATED_CATEGORY_TERM.match(value[match.end() : match.end() + 24])
        for word in INTAKE_WORDS
        for match in re.finditer(word, value)
    )


def unknown_summary(notice: NoticeInput, *, has_media: bool = False) -> NoticeSummary:
    """Return a neutral result when the model output cannot be used safely."""
    has_source = bool(notice.body_text.strip() or notice.attachments or has_media)
    publisher = notice.publisher
    if publisher is not None and len(publisher) > FIELD_TEXT_LIMITS["publisher"]:
        publisher = None
    return NoticeSummary(
        category="unknown",
        category_code=None,
        summary=REVIEW_NOTE if has_source else "공지 확인 불가",
        publisher=publisher,
        applicable_area=None,
        audience=None,
        audience_scope="unknown",
        action=None,
        action_requirement="unknown",
        location=None,
        dates=[],
        status="unknown",
        status_detail=None,
        notice_update="unknown",
        changed_details=None,
        notes=[],
        topics=[],
        uncertainties=[REVIEW_NOTE],
        evidence=[],
    )


def _excerpts(evidence: list[Evidence], field: str) -> list[str]:
    return [item.excerpt for item in evidence if item.field == field]


def _source_line_evidence(evidence: list[Evidence], sources: list[str]) -> list[Evidence]:
    """Restore the surrounding source line so a short quote cannot hide a negation."""
    expanded = []
    for item in evidence:
        for source in sources:
            start = 0
            while (position := source.find(item.excerpt, start)) >= 0:
                line_start = source.rfind("\n", 0, position) + 1
                line_end = source.find("\n", position + len(item.excerpt))
                expanded.append(
                    Evidence(
                        field=item.field,
                        excerpt=source[line_start : line_end if line_end >= 0 else len(source)],
                    )
                )
                start = position + len(item.excerpt)
    return expanded


def _literal_supported(value: str, evidence: list[Evidence], field: str) -> bool:
    return any(value in excerpt for excerpt in _excerpts(evidence, field))


def _explicit_cancellation(excerpt: str) -> bool:
    if OBSOLETE_CONTEXT.search(excerpt) or RETRACTED_CANCELLATION.search(excerpt):
        return False
    return any(
        not NEGATED_CANCELLATION.match(excerpt[match.end() : match.end() + 20])
        for match in CANCELLED_NOTICE.finditer(excerpt)
    )


def _explicit_ongoing_intake(excerpt: str) -> bool:
    if OBSOLETE_CONTEXT.search(excerpt) or ENDED_ONGOING_INTAKE.search(excerpt):
        return False
    return any(
        not NEGATED_INTAKE.match(excerpt[match.end() : match.end() + 20])
        for match in ONGOING_INTAKE.finditer(excerpt)
    )


def _summary_supported(value: str, evidence: list[Evidence]) -> bool:
    tokens = re.findall(r"[가-힣A-Za-z0-9]+", value)

    def in_order(excerpt: str) -> bool:
        position = 0
        first_position = None
        for token in tokens:
            found = excerpt.find(token, position)
            if found < 0:
                return False
            if first_position is None:
                first_position = found
            position = found + len(token)
        matched = excerpt[first_position:position]
        if re.search(r"불가능|불가|금지|아니|아닙|않|없|취소|중단|못", matched) and not re.search(
            r"불가능|불가|금지|아니|아닙|않|없|취소|중단|못", value
        ):
            return False
        after = excerpt[position : position + 20]
        return not re.match(
            r"\s*(?:은|는|을|를|이|가)?\s*(?:하지\s*마|하지\s*않|하지\s*못|할\s*수\s*없|하실\s*수\s*없|안\s*(?:되|됩)|불가|금지|아님|아닙|아니|않|없|취소|중단|못)",
            after,
        )

    return bool(tokens) and any(in_order(excerpt) for excerpt in _excerpts(evidence, "summary"))


def _category_supported(
    category: str,
    summary: str,
    evidence: list[Evidence],
    *,
    context_evidence: list[Evidence] | None = None,
) -> bool:
    # Classification describes the source independently of the generated headline.
    quotes = [item for item in evidence if item.field in ("category", "summary")]
    if category not in ("application", "event"):
        # Other categories have no independent semantic rule here. A rejected
        # headline must not make an arbitrary quote a verified duty/news category.
        return (
            category == "unknown"
            or bool(quotes)
            and _summary_supported(
                summary, context_evidence if context_evidence is not None else evidence
            )
        )
    words = (*INTAKE_WORDS, "지원") if category == "application" else EVENT_WORDS
    for item in quotes:
        if category == "event" and _intake_mentioned(item.excerpt):
            continue
        for word in words:
            for match in re.finditer(word, item.excerpt):
                contexts = [
                    context.excerpt
                    for context in context_evidence or [item]
                    if context.field == item.field and item.excerpt in context.excerpt
                ]
                # A short quote cannot conceal a negation just after it in the source.
                if any(
                    not NEGATED_CATEGORY_TERM.match(
                        context[position + match.end() : position + match.end() + 24]
                    )
                    for context in contexts
                    for position in (
                        found.start() for found in re.finditer(re.escape(item.excerpt), context)
                    )
                ):
                    return True
    return False


def _claim_context(source: str, start: int, end: int, *, quote_start: int) -> tuple[str, int, int]:
    """Expand one claim only within its source sentence or list item."""
    boundary = re.compile(
        r"\n|[;；•●○■□※]|[!?。](?=\s|$)|(?<!\d)\.(?=\s|$)"
        r"|(?P<label>(?<![가-힣])[가-힣](?:[가-힣]|[^\S\r\n]){0,12}\s*[:：])"
    )
    left, right = 0, len(source)
    for match in boundary.finditer(source):
        if match.end() <= start:
            left = match.start() if match.group("label") else match.end()
        elif match.start() >= end:
            right = match.start()
            break
    # Preserve a label-only preceding line when it is part of the model's actual
    # quote. A short value alone cannot borrow another list item's previous line.
    line_start = source.rfind("\n", 0, start) + 1
    if line_start > quote_start and not source[line_start:start].strip():
        previous_start = source.rfind("\n", 0, line_start - 1) + 1
        label_start = max(previous_start, quote_start)
        if re.fullmatch(
            r"\s*[■□•●○]?\s*"
            r"(?:장소|위치|개최지|행사장|신청방법|접수방법|의무|반드시|필수)\s*[:：]?\s*",
            _normalize_table_labels(source[label_start:start]),
        ):
            left = label_start
    return source[left:right], start - left, end - left


def _field_claim_contexts(
    value: str, evidence: list[Evidence], field: str, sources: list[str]
) -> list[list[tuple[str, int, int]]]:
    """Keep all possible origins of each quote together, without borrowing quotes.

    Text quotes have no source offset/ID. If the same quote occurs with conflicting
    meanings, every possible origin must support a claim before it is retained.
    File quotes have no local text to expand, so only that exact quote is used.
    """
    groups = []
    for item in evidence:
        if item.field != field or value not in item.excerpt:
            continue
        origins = (
            [
                _claim_context(item.excerpt, claim.start(), claim.end(), quote_start=0)
                for claim in re.finditer(re.escape(value), item.excerpt)
            ]
            if item.source_type != "text"
            else []
        )
        if item.source_type == "text":
            for source in sources:
                for match in re.finditer(re.escape(item.excerpt), source):
                    for claim in re.finditer(re.escape(value), item.excerpt):
                        origins.append(
                            _claim_context(
                                source,
                                match.start() + claim.start(),
                                match.start() + claim.end(),
                                quote_start=match.start(),
                            )
                        )
        if origins:
            groups.append(origins)
    return groups


def _location_context_supported(value: str, context: tuple[str, int, int]) -> bool:
    excerpt, start, end = context
    before, after = excerpt[:start], excerpt[end:]
    if re.match(
        r"\s*(?:(?:의|에서|에)\s*)?(?:공식\s*)?"
        r"(?:홈페이지|누리집|웹사이트|인터넷|온라인)",
        after,
    ) or re.match(
        r"\s*(?:[은는이가]\s*)?(?:(?:행사\s*)?장소(?:가)?\s*)?"
        r"(?:아님|아니|아닙|불가)"
        r"|\s*(?:에서|[은는이가])?\s*(?:(?:행사|캠프)(?:를|가|는|이)?\s*)?"
        r"(?:(?:개최|진행|사용)(?:하지|되지)\s*않|열리지\s*않)",
        after,
    ):
        return False
    is_named_place = value.endswith(
        ("공원", "센터", "회관", "도서관", "구청", "동주민센터", "역", "홀", "학교")
    )
    local_after = re.split(r"[,，]", after, maxsplit=1)[0]
    physical_event = re.match(
        r"에서\s*(?:(?:행사|캠프|축제|공연)(?:가|를|는|이)?\s*)?"
        r"(?:개최|진행|열림|열립니다)(?=하|함|되|된|[\s,，.]|$)",
        after,
    )
    return bool(
        (
            is_named_place
            and after.startswith(("에서", "에 위치", " 소재"))
            and (physical_event or not re.search(r"온라인|인터넷|접수|신청|문의", local_after))
        )
        or re.search(r"(?:장소|위치|개최지|행사장)\s*[:：]?\s*$", _normalize_table_labels(before))
    )


def _location_supported(value: str, evidence: list[Evidence], sources: list[str]) -> bool:
    return any(
        all(_location_context_supported(value, context) for context in origins)
        for origins in _field_claim_contexts(value, evidence, "location", sources)
    )


_ACTION_ROLE_WORDS = (
    ("신청", "접수", "등록"),
    ("제출", "보완"),
    ("지참", "소지"),
    ("납부", "결제", "입금"),
    ("연락", "문의"),
    ("참여", "방문", "출석"),
    ("확인",),
)
_ACTION_ROLE_PATTERNS = tuple(
    re.compile(r"(?:" + "|".join(words) + r")(?=$|[\s,，:：;；.()!?]|[은는이가을를하할해한했])")
    for words in _ACTION_ROLE_WORDS
)
_ACTION_ACTOR = re.compile(
    r"(?P<actor>참여자|참가자|신청자|접수자|선정자|당첨자|방문자|대상자|주민|학생|이용자|희망자)"
    r"(?:는|은|만|의)"
)
_ACTION_ROUTES = tuple(
    re.compile(pattern)
    for pattern in (r"온라인|인터넷|홈페이지|누리집", r"현장|방문", r"전화", r"우편", r"팩스")
)
_SHARED_ACTION_REQUIREMENT = re.compile(
    r"(?:모두|각각|둘\s*다|전부)\s*(?:[은는이가]\s*)?(?:의무|필수|반드시|해야)"
)


def _action_requirement_context(value: str, origin: tuple[str, int, int]) -> tuple[str, int, int]:
    """Keep obligations in this action's clause or an explicit continuation of it."""
    context, start, end = origin
    roles = {index for index, pattern in enumerate(_ACTION_ROLE_PATTERNS) if pattern.search(value)}
    if not roles or re.match(r"\s*(?:의무|필수|반드시)\s*[:：]", context):
        return origin
    boundaries = [
        match
        for match in re.finditer(r"[,，]|(?:가능|선택)(?:하며|하고|이며|이고)", context)
        if match.end() <= start or match.start() >= end
    ]
    own_left = max((match.end() for match in boundaries if match.end() <= start), default=0)
    own_right = next(
        (
            match.start() if match.group() in (",", "，") else match.end()
            for match in boundaries
            if match.start() >= end
        ),
        len(context),
    )
    own_clause = context[own_left:own_right]
    explicit_requirement = (
        re.search(r"가능|선택|희망|의무|필수|반드시|해야", own_clause) is not None
    )
    own_actor = _ACTION_ACTOR.search(own_clause)
    own_routes = {
        index for index, pattern in enumerate(_ACTION_ROUTES) if pattern.search(own_clause)
    }
    left, right = 0, len(context)
    edges = [0, *(match.end() for match in boundaries), len(context)]
    for index, match in enumerate(boundaries):
        # A bare continuation (e.g. '반드시 해야 합니다') has no other action
        # role and remains connected. A different action's clause is excluded.
        if match.end() <= start:
            clause = context[edges[index] : match.start()]
        else:
            clause = context[match.end() : edges[index + 2]]
        # A subject such as '신청자' is not itself an application instruction.
        clause_roles = {
            role for role, pattern in enumerate(_ACTION_ROLE_PATTERNS) if pattern.search(clause)
        }
        other_actor = _ACTION_ACTOR.search(clause)
        other_routes = {
            index for index, pattern in enumerate(_ACTION_ROUTES) if pattern.search(clause)
        }
        different_actor = bool(
            own_actor and other_actor and own_actor.group("actor") != other_actor.group("actor")
        )
        different_route = bool(own_routes and other_routes and own_routes.isdisjoint(other_routes))
        different_scope = different_actor or different_route
        if clause_roles and (roles.isdisjoint(clause_roles) or different_scope):
            separate_actor = bool(other_actor and (own_actor is None or different_actor))
            # A shared instruction can govern a list of different actions. Only
            # detach a clause with a distinct actor or its own requirement here.
            # Repeating the same actor does not break '모두 필수' for a list.
            if not (different_scope or explicit_requirement or separate_actor) or (
                _SHARED_ACTION_REQUIREMENT.search(clause) and not separate_actor
            ):
                continue
            if match.end() <= start:
                left = match.end()
            else:
                right = match.end() if match.group() not in (",", "，") else match.start()
                break
    return context[left:right], start - left, end - left


def _action_requirement_supported(
    value: str, requirement: str, evidence: list[Evidence], sources: list[str]
) -> bool:
    words = {
        "required": ("의무", "반드시", "필수", "해야"),
        "optional": ("신청", "모집", "희망", "참여", "가능", "선택"),
        "recommended": ("권고", "권장", "주의", "우회", "삼가"),
    }.get(requirement)
    if words is None:
        return False

    def supported(origin: tuple[str, int, int]) -> bool:
        context, start, end = _action_requirement_context(value, origin)
        nearby = context[max(0, start - 8) : end + 8]
        negated_obligation = re.search(
            r"(?:의무|필수)(?:\s*(?:사항|조건|절차|요건))?\s*"
            r"(?:[은는이가]\s*)?(?:아님|아니|아닙|없)"
            r"|(?:해야|하여야)\s*(?:하는|할)?\s*(?:것)?\s*"
            r"(?:[은는이가]\s*)?(?:아님|아니|아닙)"
            r"|하지\s*않(?:아도|으셔도)|안\s*해도|할\s*필요(?:가|는)?\s*없",
            context,
        )
        mandatory = not negated_obligation and any(
            not re.match(r"\s*(?:[은는이가]\s*)?(?:아님|아니|아닙|없)", context[match.end() :])
            for match in re.finditer(r"의무|반드시|필수|해야", context)
        )
        if requirement == "optional" and mandatory:
            return False
        if requirement == "required" and (
            re.search(r"선택|희망|가능", nearby) or negated_obligation
        ):
            return False
        if requirement == "optional" and negated_obligation:
            return True
        return any(word in nearby for word in words)

    return any(
        all(supported(context) for context in origins)
        for origins in _field_claim_contexts(value, evidence, "action", sources)
    )


def _text_audience_supported(value: str, evidence: list[Evidence], sources: list[str]) -> bool:
    for excerpt in _excerpts(evidence, "audience"):
        if value not in excerpt:
            continue
        for source in sources:
            position = source.find(excerpt)
            if position < 0:
                continue
            line_start = source.rfind("\n", 0, position) + 1
            line_end = source.find("\n", position)
            line = source[line_start : line_end if line_end >= 0 else len(source)]
            preceding = source[:line_start].splitlines()[-2:]
            for candidate in [*preceding, line]:
                match = re.search(r"(?:대상|자격)\s*[:：\-–]\s*(.+)", candidate)
                if match and value != match.group(1).strip().rstrip(".。 "):
                    return False
    return _literal_supported(value, evidence, "audience")


def _audience_supported(value: str, evidence: list[Evidence], sources: list[str]) -> bool:
    text_evidence = [item for item in evidence if item.source_type == "text"]
    if _text_audience_supported(value, text_evidence, sources):
        return True
    # Check the quoted conditions independently; this does not verify the file's pixels.
    return any(
        _text_audience_supported(value, [item], [item.excerpt])
        for item in evidence
        if item.source_type != "text"
    )


def _area_supported(value: str, evidence: list[Evidence]) -> bool:
    for excerpt in _excerpts(evidence, "applicable_area"):
        for match in re.finditer(re.escape(value), excerpt):
            after = excerpt[match.end() : match.end() + 30]
            if not after.startswith("청") and re.search(
                r"주소|거주|주민|관내|전역|지역|대상|영향\s*구간", after
            ):
                return True
    return False


def _status_supported(
    summary: NoticeSummary,
    dates: list[DateEntry],
    notice: NoticeInput,
    evidence: list[Evidence],
) -> bool:
    if summary.status in ("unknown", "check_required", "not_applicable"):
        return True
    status_context = [
        item.excerpt
        for item in evidence
        if item.field in ("status", "notice_update", "summary", "action", "dates")
    ]
    if summary.status == "cancelled":
        return any(_explicit_cancellation(excerpt) for excerpt in status_context)
    if summary.status == "ongoing_intake":
        return any(_explicit_ongoing_intake(excerpt) for excerpt in status_context)
    kind = "application" if summary.category == "application" else "event"
    if summary.category not in ("application", "event"):
        return True
    allowed = (
        {"upcoming", "open", "closed"}
        if summary.category == "application"
        else {"upcoming", "ongoing", "ended"}
    )
    if summary.status not in allowed:
        return False
    relevant = [entry for entry in dates if entry.kind == kind]
    if not relevant:
        return False
    now = notice.reference_datetime.astimezone(KST)
    starts = [
        datetime.combine(
            date.fromisoformat(entry.start_date),
            time.fromisoformat(entry.start_time) if entry.start_time else time.min,
            tzinfo=KST,
        )
        for entry in relevant
        if entry.start_date
    ]
    ends = [
        datetime.combine(
            date.fromisoformat(entry.end_date),
            time.fromisoformat(entry.end_time) if entry.end_time else time.max,
            tzinfo=KST,
        )
        for entry in relevant
        if entry.end_date
    ]
    if summary.status == "upcoming":
        return bool(starts) and now < min(starts)
    if summary.status in ("closed", "ended"):
        return bool(ends) and now > max(ends)
    if summary.status in ("open", "ongoing"):
        return any(
            datetime.combine(
                date.fromisoformat(entry.start_date),
                time.fromisoformat(entry.start_time) if entry.start_time else time.min,
                tzinfo=KST,
            )
            <= now
            <= datetime.combine(
                date.fromisoformat(entry.end_date),
                time.fromisoformat(entry.end_time) if entry.end_time else time.max,
                tzinfo=KST,
            )
            for entry in relevant
            if entry.start_date and entry.end_date
        )
    return False


def _dates_in_excerpt(excerpt: str) -> list[tuple[date, int, int]]:
    found = []
    current_year = None
    for match in DATE_TOKEN.finditer(excerpt):
        if match.group("year"):
            current_year = int(match.group("year"))
        if current_year is None:
            continue
        try:
            value = date(current_year, int(match.group("month")), int(match.group("day")))
        except ValueError:
            continue
        finish = match.end()
        if excerpt[finish : finish + 1] == ".":
            finish += 1
        found.append((value, match.start(), finish))
    return found


def _times_in_excerpt(excerpt: str) -> list[tuple[str, int, int]]:
    """Normalize clock values while retaining their positions in the source."""
    found = []
    for match in TIME_TOKEN.finditer(excerpt):
        if match.group("period"):
            hour = int(match.group("korean_hour"))
            if not 1 <= hour <= 12:
                continue
            hour = hour % 12 + (12 if match.group("period") == "오후" else 0)
            minute = int(match.group("korean_minute") or 0)
        else:
            hour, minute = int(match.group("hour")), int(match.group("minute"))
        found.append((f"{hour:02d}:{minute:02d}", match.start(), match.end()))
    return found


def _range_connector(excerpt: str) -> bool:
    """Accept range punctuation, but never bridge an intervening schedule label."""
    remaining = TIME_TOKEN.sub("", excerpt)
    remaining = re.sub(r"\([월화수목금토일](?:요일)?\)", "", remaining)
    return bool(re.fullmatch(r"\s*(?:부터\s*)?(?:[~∼～–—-]\s*)?", remaining)) and bool(
        re.search(r"부터|[~∼～–—-]", remaining)
    )


def _schedule_label(excerpt: str) -> bool:
    value = _normalize_table_labels(excerpt.strip())
    if not value or len(value) > 40 or DATE_TOKEN.search(value) or TIME_TOKEN.search(value):
        return False
    if not any(word in value for words in DATE_KIND_WORDS.values() for word in words) and not (
        re.fullmatch(r"(?:기간|일정|일자|날짜|시간)\s*[:：]?", value)
    ):
        return False
    return bool(
        value.endswith((":", "："))
        or re.fullmatch(
            r"[가-힣 ]*(?:기간|일시|일정|일자|날짜|시간|시작일|종료일|마감일|접수|모집|행사|수업)",
            value,
        )
    )


def _schedule_blocks(source: str) -> list[tuple[int, int, str]]:
    """Keep sentences/rows separate; attach only an immediately preceding label row."""
    blocks = []
    begin = 0
    previous_begin = 0
    previous_text = ""
    previous_boundary = ""
    for boundary in [*SCHEDULE_BOUNDARY.finditer(source), None]:
        finish = boundary.start() if boundary else len(source)
        text = source[begin:finish]
        if (
            boundary
            and boundary.start() == boundary.end()
            and not (
                DATE_TOKEN.search(text)
                or TIME_TOKEN.search(text)
                or ":" in text
                and any(word in text for words in DATE_KIND_WORDS.values() for word in words)
            )
        ):
            continue
        block_begin = begin
        if previous_boundary == "\n" and _schedule_label(previous_text) and text.strip():
            block_begin = previous_begin
        if text.strip():
            blocks.append((block_begin, finish, source[block_begin:finish]))
        previous_begin, previous_text = block_begin, source[block_begin:finish]
        previous_boundary = boundary.group() if boundary else ""
        begin = boundary.end() if boundary else len(source)
    return blocks


def _kind_supported(
    entry: DateEntry, prefix: str, suffix: str, *, context_kind: str | None = None
) -> bool:
    prefix = _normalize_table_labels(prefix)
    suffix = _normalize_table_labels(suffix)
    if entry.kind not in DATE_KIND_WORDS:
        return bool(entry.label and _normalize_table_labels(entry.label) in prefix)
    # Prefer the closest label before the date. A later schedule cannot lend its kind.
    before = [
        (match.start(), match.end(), kind, word)
        for kind, words in DATE_KIND_WORDS.items()
        for word in words
        for match in re.finditer(re.escape(word), prefix)
    ]
    if before:
        closest = max(before)
        if closest[3] == "일시":
            # In a compound label, "신청 일시" keeps its explicit application kind.
            # Only adjacent words qualify; an earlier sentence cannot lend its kind.
            explicit = [
                match
                for match in before
                if match[3] != "일시" and re.fullmatch(r"\s*", prefix[match[1] : closest[0]])
            ]
            closest = max(explicit, default=closest)
        return closest[2] == entry.kind
    # In prose, "행사 참가자를 모집합니다" describes an application, not an event time.
    verbs = {
        "application": r"(?:신청|접수|모집)(?:합니다|하세요|해|받|하|중|할)",
        "event": r"개최|열립니다|열리|진행합니다",
        "operation": r"(?:수업|운영|교육|강좌)(?:합니다|을|를|하|중)",
        "payment": r"(?:납부|결제|입금)(?:합니다|하세요|하|해야)",
        "submission": r"(?:제출|보완)(?:합니다|하세요|하|해야)",
        "effective": r"(?:시행|적용|개정)(?:합니다|됩니다|하|되)",
        "disruption": r"(?:통제|휴관|중단)(?:합니다|됩니다|하|되)",
        "result": r"(?:발표|선정)(?:합니다|됩니다|하|되)",
    }
    after = [kind for kind, pattern in verbs.items() if re.search(pattern, suffix)]
    if after:
        return after == [entry.kind]
    kinds = {
        kind for kind, words in DATE_KIND_WORDS.items() if any(word in suffix for word in words)
    }
    if kinds:
        return kinds == {entry.kind}
    # A generic table label alone never establishes a schedule's kind.
    return bool(context_kind == entry.kind and _generic_schedule_label(prefix))


def _generic_schedule_label(prefix: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(?:기간|일정|일자|날짜|시간)\s*[:：]?\s*", _normalize_table_labels(prefix)
        )
    )


def _schedule_context_kind(excerpts: list[str]) -> str | None:
    """Use an unambiguous heading, never category alone or another dated schedule."""
    kinds = {
        kind
        for excerpt in excerpts
        if not DATE_TOKEN.search(excerpt) and not TIME_TOKEN.search(excerpt)
        for kind, words in DATE_KIND_WORDS.items()
        if any(word in _normalize_table_labels(excerpt) for word in words)
    }
    return next(iter(kinds)) if len(kinds) == 1 else None


def _adjacent_schedule_context(source: str, begin: int) -> str | None:
    """Stop at a paragraph, unrelated row, or another dated schedule."""
    if begin == 0 or source[begin - 1] != "\n":
        return None
    headings = []
    for line in reversed(source[:begin].splitlines()[-2:]):
        if not line.strip() or DATE_TOKEN.search(line) or TIME_TOKEN.search(line):
            break
        if not any(
            word in _normalize_table_labels(line)
            for words in DATE_KIND_WORDS.values()
            for word in words
        ):
            break
        headings.append(line)
    return _schedule_context_kind(headings)


@dataclass(frozen=True)
class _DateWindow:
    prefix: str
    suffix: str
    first_time_text: str
    last_time_text: str
    paired: bool
    same_day: bool = False


def _endpoint_roles(prefix: str, suffix: str) -> tuple[bool, bool]:
    after = re.sub(r"^\s*\([월화수목금토일](?:요일)?\)\s*", "", suffix)
    start_only = bool(
        re.search(r"시작|개시", prefix) or re.match(r"\s*(?:부터|(?:에\s*)?(?:시작|개시))", after)
    )
    end_only = bool(
        re.search(r"마감|종료", prefix) or re.match(r"\s*(?:까지|(?:에\s*)?(?:마감|종료))", after)
    )
    return start_only, end_only


def _date_windows(entry: DateEntry, excerpt: str) -> list[_DateWindow]:
    """Match source endpoints to their roles, including partially returned periods."""
    found = _dates_in_excerpt(excerpt)
    start = date.fromisoformat(entry.start_date) if entry.start_date else None
    end = date.fromisoformat(entry.end_date) if entry.end_date else None
    windows = []
    index = 0
    while index < len(found):
        value, begin, finish = found[index]
        final_index = index
        if index + 1 < len(found) and _range_connector(excerpt[finish : found[index + 1][1]]):
            final_index += 1
        last_value, _, last_finish = found[final_index]
        paired = final_index != index
        previous_end = found[index - 1][2] if index else 0
        prefix = excerpt[previous_end:begin]
        local_end = found[final_index + 1][1] if final_index + 1 < len(found) else len(excerpt)
        suffix = excerpt[last_finish:local_end]
        first_time_text = excerpt[finish : found[final_index][1]] if paired else suffix
        start_only, end_only = _endpoint_roles(prefix, suffix)
        if (
            last_value >= value
            and (start is None or start == value)
            and (end is None or end == last_value)
            and (paired or not (start and end_only) and not (end and start_only))
        ):
            windows.append(
                _DateWindow(prefix, suffix, first_time_text, suffix, paired, value == last_value)
            )
        index = final_index + 1
    return windows


def _times_supported(entry: DateEntry, window: _DateWindow) -> bool:
    if not entry.start_time and not entry.end_time:
        return True
    first = _times_in_excerpt(window.first_time_text)
    last = _times_in_excerpt(window.last_time_text)
    # A new kind in the time's own clause cannot supply another schedule's clock values.
    for fragment, tokens in ((window.first_time_text, first), (window.last_time_text, last)):
        if tokens:
            before_time = fragment[: tokens[0][1]]
            if any(word in before_time for words in DATE_KIND_WORDS.values() for word in words):
                if not _kind_supported(entry, window.prefix + before_time, window.suffix):
                    return False
    if window.paired:
        if window.same_day and first and last and first[0][0] > last[0][0]:
            return False
        return (not entry.start_time or bool(first) and entry.start_time == first[0][0]) and (
            not entry.end_time or bool(last) and entry.end_time == last[0][0]
        )
    pairs = [
        (left, right)
        for left, right in zip(first, first[1:], strict=False)
        if _range_connector(window.first_time_text[left[2] : right[1]])
    ]
    if pairs:
        return any(
            left[0] <= right[0]
            and (not entry.start_time or entry.start_time == left[0])
            and (not entry.end_time or entry.end_time == right[0])
            for left, right in pairs
        )
    if entry.start_time and entry.end_time:
        return False
    for value, _, finish in first:
        start_only, end_only = _endpoint_roles(window.prefix, window.first_time_text[finish:])
        if entry.start_time == value and not end_only:
            return True
        if entry.end_time == value and end_only and not start_only:
            return True
    return False


def _ground_date(
    entry: DateEntry,
    evidence: list[Evidence],
    sources: list[str],
    *,
    context_kind: str | None = None,
) -> DateEntry | None:
    excerpts = _excerpts(evidence, "dates")
    if not excerpts:
        return None

    matching = []
    for source in sources:
        quoted_spans = [
            (match.start(), match.end())
            for excerpt in excerpts
            for match in re.finditer(re.escape(excerpt), source)
        ]
        for begin, finish, block in _schedule_blocks(source):
            if not any(left < finish and right > begin for left, right in quoted_spans):
                continue
            if any(word in block for word in ("취소", "변경 전")):
                continue
            # Only adjacent, undated heading lines may explain a generic label.
            local_kind = context_kind or _adjacent_schedule_context(source, begin)
            if not entry.start_date and not entry.end_date:
                # A recurring/unresolved schedule needs its actual original expression.
                if not entry.text or entry.text not in block:
                    continue
                position = block.find(entry.text)
                window = _DateWindow(block[:position], block[position:], block, block, False)
                if _kind_supported(
                    entry, window.prefix, window.suffix, context_kind=local_kind
                ) and _times_supported(entry, window):
                    matching.append((block, entry.kind))
                continue
            for window in _date_windows(entry, block):
                kind = entry.kind
                if not _kind_supported(
                    entry, window.prefix, window.suffix, context_kind=local_kind
                ):
                    # Keep verified endpoints without guessing what a bare period means.
                    if local_kind is not None or not _generic_schedule_label(window.prefix):
                        continue
                    kind = "other"
                if not _times_supported(entry, window):
                    continue
                matching.append((block, kind))
                break
    if not matching:
        return None
    return entry.model_copy(
        update={
            "kind": entry.kind if any(kind == entry.kind for _, kind in matching) else "other",
            "label": entry.label
            if entry.label and any(entry.label in excerpt for excerpt, _ in matching)
            else None,
            "text": entry.text
            if entry.text and any(entry.text in excerpt for excerpt, _ in matching)
            else None,
        }
    )


def _ground_date_with_media(
    entry: DateEntry,
    evidence: list[Evidence],
    sources: list[str],
    *,
    context_evidence: list[Evidence] | None = None,
) -> DateEntry | None:
    text_evidence = [item for item in evidence if item.source_type == "text"]
    grounded = _ground_date(entry, text_evidence, sources)
    if grounded is not None:
        return grounded
    # Each file quote must support this schedule by itself. Do not assemble a period
    # from different files/pages, or borrow a clock value from the text body.
    for item in evidence:
        if item.source_type != "text" and item.field == "dates":
            same_page = [
                other
                for other in (context_evidence if context_evidence is not None else evidence)
                if (other.source_type, other.source_id, other.page)
                == (item.source_type, item.source_id, item.page)
            ]
            # A single quoted schedule can use its page's unambiguous heading.
            # Multiple distinct schedules must each supply their own kind context.
            page_dates = {other.excerpt for other in same_page if other.field == "dates"}
            context_kind = (
                _schedule_context_kind(
                    [other.excerpt for other in same_page if other.field in ("summary", "category")]
                )
                if len(page_dates) == 1
                else None
            )
            grounded = _ground_date(entry, [item], [item.excerpt], context_kind=context_kind)
            if grounded is not None:
                return grounded
    return None


def ground_summary(
    summary: NoticeSummary,
    notice: NoticeInput,
    *,
    media_sources: tuple[MediaSource, ...] = (),
) -> NoticeSummary:
    """Produce the strict comparison result for internal verification.

    The caller may preserve uncertain claims in memory with
    preserve_uncertain_summary. Storage gates public output: needs_review means
    the app shows an original-notice instruction and no model claims.
    """
    if is_file_only_notice(notice, media_sources):
        return preserve_file_only_summary(summary, notice, media_sources)
    sources = [notice.body_text, *(attachment.text for attachment in notice.attachments)]
    evidence = [
        item.model_copy(
            update={
                "verification": "text_matched"
                if item.source_type == "text"
                else "file_reference_only"
            }
        )
        for item in summary.evidence
        if evidence_reference_valid(item, sources=sources, media_sources=media_sources)
    ]
    context_evidence = _source_line_evidence(
        [item for item in evidence if item.source_type == "text"], sources
    ) + [item for item in evidence if item.source_type != "text"]
    data = summary.model_dump()
    changed = len(evidence) != len(summary.evidence)

    if notice.publisher is not None and len(notice.publisher) <= FIELD_TEXT_LIMITS["publisher"]:
        data["publisher"] = notice.publisher
        evidence = [item for item in evidence if item.field != "publisher"]
    elif data["publisher"] and not _literal_supported(data["publisher"], evidence, "publisher"):
        data["publisher"] = None
        changed = True

    if not _summary_supported(data["summary"], context_evidence):
        data["summary"] = REVIEW_NOTE
        changed = True
    if not _category_supported(
        data["category"], summary.summary, evidence, context_evidence=context_evidence
    ):
        data["category"] = "unknown"
        changed = True
    if data["category"] != "unknown" and data["summary"] == REVIEW_NOTE:
        # Preserve the exact quote that supports classification after a bad headline.
        evidence.extend(
            item.model_copy(update={"field": "category"})
            for item in tuple(evidence)
            if item.field == "summary"
            and _category_supported(
                data["category"], summary.summary, [item], context_evidence=context_evidence
            )
        )

    if data["category_code"] is not None and not _excerpts(evidence, "category_code"):
        data["category_code"] = None
        changed = True

    for field in ("applicable_area", "audience", "action", "location", "changed_details"):
        value = data[field]
        if value is None:
            continue
        supported = _literal_supported(value, evidence, field)
        if field == "applicable_area":
            supported = supported and _area_supported(value, evidence)
        if field == "audience":
            supported = supported and _audience_supported(value, evidence, sources)
        if field == "location":
            supported = supported and _location_supported(value, evidence, sources)
        if field == "action":
            if re.search(
                r"(?:불가|금지|할\s*수\s*없|하지\s*못|하지\s*않|안\s*(?:되|됩))\s*$",
                value,
            ):
                supported = False
            for excerpt in _excerpts(context_evidence, "action"):
                for match in re.finditer(re.escape(value), excerpt):
                    after = excerpt[match.end() : match.end() + 20]
                    if re.match(
                        r"\s*(?:은|는|을|를|이|가)?\s*(?:받지\s*않|하지\s*마|하지\s*않|하지\s*못|할\s*수\s*없|하실\s*수\s*없|가능하지\s*않|아님|아니|아닙|불가|금지|중단|종료|마감|안\s*(?:되|됩))",
                        after,
                    ):
                        supported = False
        if not supported:
            data[field] = None
            changed = True

    if data["audience"] is None:
        data["audience_scope"] = "unknown"
    elif data["audience_scope"] == "general" and any(
        word in excerpt
        for excerpt in _excerpts(evidence, "audience")
        for word in ("주소", "거주", "이상", "이하", "학년", "수급", "자격")
    ):
        data["audience_scope"] = "unknown"
        changed = True
    if data["action"] is None:
        data["action_requirement"] = "unknown"
    else:
        if not _action_requirement_supported(
            data["action"], data["action_requirement"], evidence, sources
        ):
            data["action_requirement"] = "unknown"
            changed = True
    if data["notice_update"] in ("modified", "extended", "cancelled"):
        update_words = {
            "modified": ("수정", "정정", "변경"),
            "extended": ("연장",),
            "cancelled": ("취소",),
        }[data["notice_update"]]
        if not any(
            (
                _explicit_cancellation(excerpt)
                if data["notice_update"] == "cancelled"
                else any(word in excerpt for word in update_words)
                and not NEGATED_UPDATE.search(excerpt)
                and not OBSOLETE_CONTEXT.search(excerpt)
            )
            for excerpt in _excerpts(context_evidence, "notice_update")
        ):
            data["notice_update"] = "unknown"
            data["changed_details"] = None
            changed = True
    if data["changed_details"] is None and data["notice_update"] in (
        "modified",
        "extended",
        "cancelled",
    ):
        data["notice_update"] = "unknown"
        changed = True
    if data["notice_update"] in ("new", "unknown") and data["changed_details"] is not None:
        data["changed_details"] = None
        changed = True

    grounded_notes = []
    for note in data["notes"]:
        supported = False
        for excerpt in _excerpts(context_evidence, "notes"):
            position = excerpt.find(note)
            if position < 0:
                continue
            after = excerpt[position + len(note) : position + len(note) + 15]
            if not re.match(
                r"\s*(?:[은는이가]\s*)?(?:아님|아니|아닙|않|불가(?!피)|금지|X|❌|하지\s*않)",
                after,
            ):
                supported = True
        if supported:
            grounded_notes.append(note)
    if len(grounded_notes) != len(data["notes"]):
        data["notes"] = grounded_notes
        changed = True

    grounded_dates = []
    for entry in summary.dates:
        grounded = _ground_date_with_media(entry, evidence, sources)
        if grounded is not None:
            grounded_dates.append(grounded)
            if grounded != entry:
                changed = True
        else:
            changed = True
    data["dates"] = [entry.model_dump() for entry in grounded_dates]

    if not _status_supported(summary, grounded_dates, notice, context_evidence):
        data["status"] = "unknown"
        changed = True

    grounded_topics = []
    if data["category"] == "mixed":
        topic_titles = {topic.title for topic in summary.topics}
        for topic in summary.topics:
            if any(
                topic.title in clause
                and not any(
                    other_title in clause
                    for other_title in topic_titles
                    if other_title != topic.title
                )
                and _summary_supported(topic.summary, [Evidence(field="summary", excerpt=clause)])
                and _category_supported(
                    topic.category,
                    topic.summary,
                    [Evidence(field="summary", excerpt=clause)],
                )
                for excerpt in _excerpts(context_evidence, "topics")
                for clause in re.split(r"[,，;；|]", excerpt)
            ):
                grounded_topics.append(topic)
    if len(grounded_topics) != len(summary.topics):
        changed = True
    data["topics"] = [topic.model_dump() for topic in grounded_topics]
    if data["status_detail"] and not _summary_supported(
        data["status_detail"],
        [
            Evidence(field="summary", excerpt=item.excerpt)
            for item in context_evidence
            if item.field == "status_detail"
        ],
    ):
        data["status_detail"] = None
        changed = True
    if data["status"] == "unknown" and data["status_detail"] is not None:
        data["status_detail"] = None
        changed = True
    if (
        not data["dates"]
        and data["status"] not in ("cancelled", "ongoing_intake")
        and data["category"]
        in (
            "application",
            "event",
            "living",
            "obligation",
        )
    ):
        if data["status"] != "unknown":
            changed = True
        data["status"] = "unknown"

    if data["status"] == "unknown" and data["status_detail"] is not None:
        data["status_detail"] = None
        changed = True

    if data["category"] == "unknown":
        # Independent facts keep their own evidence; temporal status needs a known kind.
        if data["status"] != "unknown" or data["status_detail"] is not None:
            changed = True
        data["status"] = "unknown"
        data["status_detail"] = None

    retained = {
        field
        for field in (
            "category",
            "category_code",
            "publisher",
            "summary",
            "applicable_area",
            "audience",
            "action",
            "location",
            "dates",
            "notes",
            "changed_details",
            "status_detail",
            "status",
            "notice_update",
            "topics",
        )
        if data[field] not in (None, [], REVIEW_NOTE, "unknown")
    }
    data["evidence"] = [
        item.model_dump()
        for item in evidence
        if item.field in retained
        and (
            item.field != "dates"
            or any(
                _ground_date_with_media(
                    entry,
                    [item],
                    sources,
                    context_evidence=evidence,
                )
                is not None
                for entry in grounded_dates
            )
        )
    ]
    data["uncertainties"] = [REVIEW_NOTE] if changed or summary.uncertainties else []
    grounded = NoticeSummary.model_validate(data)
    validate_evidence(
        grounded,
        body_text=notice.body_text,
        attachment_texts=[attachment.text for attachment in notice.attachments],
        media_sources=media_sources,
    )
    return grounded


def preserve_uncertain_summary(
    summary: NoticeSummary,
    checked: NoticeSummary,
    notice: NoticeInput,
    *,
    media_sources: tuple[MediaSource, ...] = (),
) -> NoticeSummary:
    """Keep schema-valid claims and mark inconclusive comparisons for review.

    A literal quote match verifies the quote, not the meaning of the claim. An
    unmatched quote is retained with verification=None, including invalid file
    references; no source ID/page is guessed. Impossible endpoint ordering is
    still repaired. This function never fabricates missing model claims.
    """
    if is_file_only_notice(notice, media_sources):
        return checked
    if not notice.body_text.strip() and not notice.attachments and not media_sources:
        return unknown_summary(notice)

    data = summary.model_dump()
    uncertain = bool(checked.uncertainties or summary.summary == REVIEW_NOTE)
    for field in NoticeSummary.model_fields:
        if field not in ("publisher", "evidence", "uncertainties"):
            uncertain = uncertain or getattr(summary, field) != getattr(checked, field)

    if notice.publisher and len(notice.publisher) <= FIELD_TEXT_LIMITS["publisher"]:
        data["publisher"] = notice.publisher
    elif summary.publisher != checked.publisher:
        uncertain = True

    sources = [
        notice.body_text,
        *(attachment.text for attachment in notice.attachments),
        *(value for value in (notice.title, notice.publisher, notice.department) if value),
    ]
    evidence = []
    cited_fields = set()
    for item in summary.evidence:
        if getattr(summary, item.field) in (None, []):
            uncertain = True
            continue
        if item.field == "publisher" and notice.publisher:
            continue
        valid = evidence_reference_valid(item, sources=sources, media_sources=media_sources)
        verification = (
            ("text_matched" if item.source_type == "text" else "file_reference_only")
            if valid
            else None
        )
        uncertain = uncertain or not valid
        if valid:
            cited_fields.add(item.field)
        evidence.append(item.model_copy(update={"verification": verification}).model_dump())
    required = {"summary"} if summary.summary != REVIEW_NOTE else set()
    required.update(
        field
        for field in (
            "category_code",
            "applicable_area",
            "audience",
            "action",
            "location",
            "dates",
            "notes",
            "topics",
        )
        if getattr(summary, field) not in (None, [])
    )
    uncertain = uncertain or bool(required - cited_fields)
    data["evidence"] = evidence

    reversed_order = False
    for index, entry in enumerate(summary.dates):
        for field in reversed_end_fields(entry):
            data["dates"][index][field] = None
            reversed_order = True
    if reversed_order:
        uncertain = True
        data["status"] = "unknown"
        data["status_detail"] = None
        data["evidence"] = [
            item for item in data["evidence"] if item["field"] not in ("status", "status_detail")
        ]
    data["uncertainties"] = list(
        dict.fromkeys([*summary.uncertainties, *([REVIEW_NOTE] if uncertain else [])])
    )
    return NoticeSummary.model_validate(data)
