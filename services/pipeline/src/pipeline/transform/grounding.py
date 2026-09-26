"""Keep only notice claims that can be checked against the supplied source text."""

import re
from datetime import date, datetime, time

from pipeline.transform.notice_input import KST, NoticeInput
from pipeline.transform.summary_schema import (
    MAX_SHORT_TEXT_LENGTH,
    DateEntry,
    Evidence,
    NoticeSummary,
    validate_evidence,
)

REVIEW_NOTE = "원문 확인 필요"
DATE_TOKEN = re.compile(
    r"(?<!\d)(?:(?P<year>\d{4})\s*(?:년|[./-])\s*)?"
    r"(?P<month>\d{1,2})\s*(?:월|[./-])\s*(?P<day>\d{1,2})(?:일)?(?!\d)"
)
DATE_KIND_WORDS = {
    "application": ("신청", "접수", "모집"),
    "event": ("행사", "축제", "공연", "개최", "일시"),
    "operation": ("수업", "운영", "교육", "강좌"),
    "payment": ("납부", "결제", "입금"),
    "submission": ("제출", "보완", "서류"),
    "effective": ("시행", "적용", "개정"),
    "disruption": ("통제", "휴관", "중단"),
    "result": ("결과", "발표", "선정", "합격"),
}
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


def unknown_summary(notice: NoticeInput) -> NoticeSummary:
    """Return a neutral result when the model output cannot be used safely."""
    has_source = bool(notice.body_text.strip() or notice.attachments)
    publisher = notice.publisher
    if publisher is not None and len(publisher) > MAX_SHORT_TEXT_LENGTH:
        publisher = None
    return NoticeSummary(
        category="unknown",
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


def _category_supported(category: str, summary: str, evidence: list[Evidence]) -> bool:
    if category == "application":
        return any(
            word in summary and word in excerpt
            for excerpt in _excerpts(evidence, "summary")
            for word in ("신청", "접수", "모집", "지원", "채용", "공모")
        )
    if category == "event":
        return any(
            word in summary and word in excerpt
            for excerpt in _excerpts(evidence, "summary")
            for word in ("행사", "축제", "공연", "참여", "개방", "개최")
        )
    return True


def _location_supported(value: str, evidence: list[Evidence]) -> bool:
    for excerpt in _excerpts(evidence, "location"):
        if value not in excerpt:
            continue
        before, after = excerpt.split(value, 1)
        is_named_place = value.endswith(
            ("공원", "센터", "회관", "도서관", "구청", "동주민센터", "역", "홀", "학교")
        )
        if (
            is_named_place
            and after.startswith(("에서", "에 위치", " 소재"))
            and not re.search(r"온라인|인터넷|접수|신청|문의", excerpt)
        ):
            return True
        if re.search(r"(?:장소|위치|개최지|행사장)\s*[:：]?\s*$", before):
            return True
    return False


def _audience_supported(value: str, evidence: list[Evidence], sources: list[str]) -> bool:
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
        found.append((value, match.start(), match.end()))
    return found


def _date_windows(entry: DateEntry, excerpt: str) -> list[tuple[str, str, str]]:
    """Pair a date with its nearby schedule label and its own time values."""
    found = _dates_in_excerpt(excerpt)
    start = date.fromisoformat(entry.start_date) if entry.start_date else None
    end = date.fromisoformat(entry.end_date) if entry.end_date else None
    if start is None and end is None:
        return []

    windows = []
    for index, (value, begin, finish) in enumerate(found):
        if value != (start or end):
            continue
        final_index = index
        if start is not None and end is not None and end != start:
            if index + 1 >= len(found) or found[index + 1][0] != end:
                continue
            between = excerpt[finish : found[index + 1][1]]
            if not re.search(r"~|∼|부터|까지|[–—-]", between):
                continue
            final_index += 1

        line_start = excerpt.rfind("\n", 0, begin) + 1
        previous_end = found[index - 1][2] if index else 0
        prefix = excerpt[max(line_start, previous_end) : begin]
        line_end = excerpt.find("\n", found[final_index][2])
        if line_end < 0:
            line_end = len(excerpt)
        following_date = found[final_index + 1][1] if final_index + 1 < len(found) else line_end
        local_end = min(line_end, following_date)
        if final_index == index:
            first_time_text = excerpt[finish:local_end]
            last_time_text = first_time_text
        else:
            first_time_text = excerpt[finish : found[final_index][1]]
            last_time_text = excerpt[found[final_index][2] : local_end]
        windows.append((prefix, first_time_text, last_time_text))
    return windows


def _time_mentioned(value: str, excerpt: str) -> bool:
    hour, minute = (int(part) for part in value.split(":"))
    if re.search(rf"(?<!\d)0?{hour}:{minute:02d}(?!\d)", excerpt):
        return True
    if minute:
        return False
    if hour < 12:
        return bool(re.search(rf"오전\s*{hour}시", excerpt))
    if hour == 12:
        return bool(re.search(r"오후\s*12시", excerpt))
    return bool(re.search(rf"오후\s*{hour - 12}시", excerpt))


def _ground_date(
    entry: DateEntry, evidence: list[Evidence], sources: list[str]
) -> DateEntry | None:
    excerpts = _excerpts(evidence, "dates")
    if not excerpts:
        return None

    kind_words = DATE_KIND_WORDS.get(entry.kind)
    matching = []
    for excerpt in excerpts:
        for source in sources:
            position = source.find(excerpt)
            if position < 0:
                continue
            line_start = source.rfind("\n", 0, position) + 1
            line_end = source.find("\n", position + len(excerpt))
            line = source[line_start : line_end if line_end >= 0 else len(source)]
            if any(word in line for word in ("취소", "변경 전")):
                continue
            for prefix, first_time_text, last_time_text in _date_windows(entry, line):
                if kind_words is not None and not any(word in prefix for word in kind_words):
                    continue
                if kind_words is None and (entry.label is None or entry.label not in prefix):
                    continue
                if entry.start_time and not _time_mentioned(entry.start_time, first_time_text):
                    continue
                if entry.end_time and not _time_mentioned(entry.end_time, last_time_text):
                    continue
                if entry.start_date == entry.end_date and entry.start_time and entry.end_time:
                    start_at = first_time_text.find(entry.start_time)
                    end_at = first_time_text.find(entry.end_time)
                    if start_at < 0 or end_at <= start_at:
                        continue
                matching.append(line)
                break
    if not matching:
        return None
    if not any((entry.start_date, entry.end_date, entry.start_time, entry.end_time)) and not any(
        (entry.label and entry.label in excerpt) or (entry.text and entry.text in excerpt)
        for excerpt in matching
    ):
        return None
    return entry.model_copy(
        update={
            "label": entry.label
            if entry.label and any(entry.label in excerpt for excerpt in matching)
            else None,
            "text": entry.text
            if entry.text and any(entry.text in excerpt for excerpt in matching)
            else None,
        }
    )


def ground_summary(summary: NoticeSummary, notice: NoticeInput) -> NoticeSummary:
    """Drop unsupported fields and leave a single instruction to consult the source."""
    sources = [notice.body_text, *(attachment.text for attachment in notice.attachments)]
    evidence = [
        item for item in summary.evidence if any(item.excerpt in source for source in sources)
    ]
    context_evidence = _source_line_evidence(evidence, sources)
    data = summary.model_dump()
    changed = len(evidence) != len(summary.evidence)

    if notice.publisher is not None and len(notice.publisher) <= MAX_SHORT_TEXT_LENGTH:
        data["publisher"] = notice.publisher
        evidence = [item for item in evidence if item.field != "publisher"]
    elif data["publisher"] and not _literal_supported(data["publisher"], evidence, "publisher"):
        data["publisher"] = None
        changed = True

    if not _summary_supported(data["summary"], context_evidence) or not _category_supported(
        data["category"], data["summary"], context_evidence
    ):
        data["summary"] = REVIEW_NOTE
        data["category"] = "unknown"
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
            supported = supported and _location_supported(value, evidence)
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
        local_contexts = []
        for excerpt in _excerpts(evidence, "action"):
            for match in re.finditer(re.escape(data["action"]), excerpt):
                local_contexts.append(excerpt[max(0, match.start() - 8) : match.end() + 8])
        requirement_words = {
            "required": ("의무", "반드시", "필수", "해야"),
            "optional": ("신청", "모집", "희망", "참여", "가능", "선택"),
            "recommended": ("권고", "권장", "주의", "우회", "삼가"),
        }.get(data["action_requirement"])
        if (
            requirement_words is None
            or not any(word in context for context in local_contexts for word in requirement_words)
            or (
                data["action_requirement"] == "required"
                and any(re.search(r"선택|희망|가능", context) for context in local_contexts)
            )
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
                r"\s*(?:[은는이가]\s*)?(?:아님|아니|아닙|않|불가|금지|X|❌|하지\s*않)",
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
        grounded = _ground_date(entry, evidence, sources)
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
        for field, empty in (
            ("action", None),
            ("dates", []),
            ("notes", []),
            ("status_detail", None),
        ):
            if data[field] != empty:
                changed = True
            data[field] = empty
        if data["action_requirement"] != "unknown" or data["notice_update"] != "unknown":
            changed = True
        data["action_requirement"] = "unknown"
        data["status"] = "unknown"
        data["notice_update"] = "unknown"

    retained = {
        field
        for field in (
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
            or any(_ground_date(entry, [item], sources) is not None for entry in grounded_dates)
        )
    ]
    data["uncertainties"] = [REVIEW_NOTE] if changed or summary.uncertainties else []
    grounded = NoticeSummary.model_validate(data)
    validate_evidence(
        grounded,
        body_text=notice.body_text,
        attachment_texts=[attachment.text for attachment in notice.attachments],
    )
    return grounded
