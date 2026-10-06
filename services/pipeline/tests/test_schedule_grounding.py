"""Check schedule roles and source context without calling Gemini or a database."""

import json
from pathlib import Path

import pytest

from pipeline.transform.grounding import REVIEW_NOTE, ground_summary, unknown_summary
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summary_schema import DateEntry, NoticeSummary


def _entry(**overrides: str | None) -> DateEntry:
    data: dict[str, str | None] = {
        "kind": "event",
        "label": None,
        "text": None,
        "start_date": None,
        "end_date": None,
        "start_time": None,
        "end_time": None,
    }
    data.update(overrides)
    return DateEntry.model_validate(data)


def _ground(
    source: str,
    entry: DateEntry,
    *,
    category: str = "event",
    status: str = "unknown",
    excerpt: str | None = None,
) -> NoticeSummary:
    headline = "참가자 모집" if category == "application" else "행사 안내"
    notice = NoticeInput.model_validate(
        {
            "title": headline,
            "body_text": f"{headline}\n{source}",
            "reference_datetime": "2026-09-26T12:00:00+09:00",
        }
    )
    data = unknown_summary(notice).model_dump()
    data.update(
        category=category,
        summary=headline,
        status=status,
        dates=[entry.model_dump()],
        uncertainties=[],
        evidence=[
            {"field": "summary", "excerpt": headline},
            {"field": "dates", "excerpt": source if excerpt is None else excerpt},
        ],
    )
    return ground_summary(NoticeSummary.model_validate(data), notice)


@pytest.mark.parametrize(
    ("start_date", "end_date"),
    [("2026-09-25", None), (None, "2026-09-30")],
)
def test_partial_range_preserves_the_correct_date_role(
    start_date: str | None, end_date: str | None
) -> None:
    entry = _entry(start_date=start_date, end_date=end_date)

    result = _ground("행사: 2026-09-25~2026-09-30", entry)

    assert result.dates == [entry]
    assert result.uncertainties == []


@pytest.mark.parametrize(
    ("start_date", "end_date", "status"),
    [(None, "2026-09-25", "ended"), ("2026-09-30", None, "upcoming")],
)
def test_partial_range_rejects_swapped_date_roles_and_false_status(
    start_date: str | None, end_date: str | None, status: str
) -> None:
    result = _ground(
        "행사: 2026-09-25~2026-09-30",
        _entry(start_date=start_date, end_date=end_date),
        status=status,
    )

    assert result.dates == []
    assert result.status == "unknown"
    assert result.uncertainties == [REVIEW_NOTE]
    assert all(item.field != "dates" for item in result.evidence)


@pytest.mark.parametrize("recurrence", ["매주 토요일", "매월 첫째 월요일"])
def test_unresolved_recurring_schedule_preserves_literal_rule(recurrence: str) -> None:
    entry = _entry(text=recurrence)

    result = _ground(f"행사: {recurrence}", entry, status="check_required")

    assert result.dates == [entry]
    assert result.status == "check_required"
    assert result.uncertainties == []
    assert any(item.field == "dates" for item in result.evidence)


def test_unresolved_recurring_schedule_rejects_invented_rule() -> None:
    result = _ground("행사: 매주 토요일", _entry(text="매주 일요일"))

    assert result.dates == []
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("time_range", ["오전 9시~오후 6시", "09:00~18:00"])
def test_same_day_time_range_preserves_korean_and_numeric_times(time_range: str) -> None:
    entry = _entry(
        start_date="2026-09-26",
        end_date="2026-09-26",
        start_time="09:00",
        end_time="18:00",
    )

    result = _ground(f"행사: 2026-09-26 {time_range}", entry, status="ongoing")

    assert result.dates == [entry]
    assert result.status == "ongoing"
    assert result.uncertainties == []


@pytest.mark.parametrize("time_range", ["오전 9시~오후 6시", "09:00~18:00"])
def test_same_day_time_range_rejects_swapped_times(time_range: str) -> None:
    result = _ground(
        f"행사: 2026-09-26 {time_range}",
        _entry(
            start_date="2026-09-26",
            end_date="2026-09-26",
            start_time="18:00",
            end_time="09:00",
        ),
        status="ongoing",
    )

    assert result.dates == []
    assert result.status == "unknown"
    assert result.uncertainties == [REVIEW_NOTE]


def test_single_korean_start_time_keeps_the_day_schedule() -> None:
    entry = _entry(start_date="2026-09-26", end_date="2026-09-26", start_time="10:00")

    result = _ground("행사: 2026-09-26 오전 10시", entry)

    assert result.dates == [entry]
    assert result.uncertainties == []


@pytest.mark.parametrize("excerpt", [None, "2026-09-26"])
def test_date_recognizes_its_label_on_the_preceding_line(excerpt: str | None) -> None:
    entry = _entry(label="일시", start_date="2026-09-26", end_date="2026-09-26")

    result = _ground("일시:\n2026-09-26", entry, excerpt=excerpt)

    assert result.dates == [entry]
    assert result.uncertainties == []


def test_date_recognizes_event_kind_after_the_date() -> None:
    entry = _entry(start_date="2026-09-26", end_date="2026-09-26")

    result = _ground("2026-09-26 행사 개최", entry)

    assert result.dates == [entry]
    assert result.uncertainties == []


def test_application_range_recognizes_recruitment_after_dates() -> None:
    entry = _entry(kind="application", start_date="2026-10-05", end_date="2026-10-12")

    result = _ground(
        "2026년 10월 5일부터 10월 12일까지 참가자를 모집합니다.",
        entry,
        category="application",
    )

    assert result.dates == [entry]
    assert result.uncertainties == []


def test_application_datetime_label_preserves_dates_and_open_status() -> None:
    source = "신청 일시: 2026-09-26~2026-09-30"
    entry = _entry(
        kind="application",
        label="신청 일시",
        start_date="2026-09-26",
        end_date="2026-09-30",
    )

    result = _ground(source, entry, category="application", status="open")

    assert result.dates == [entry]
    assert result.status == "open"
    assert result.uncertainties == []
    assert any(item.field == "dates" and item.excerpt == source for item in result.evidence)


@pytest.mark.parametrize(
    ("label", "supported_kind", "wrong_kind"),
    [
        ("접수 일시", "application", "event"),
        ("모집일시", "application", "event"),
        ("신청 시간", "application", "event"),
        ("행사 신청 일시", "application", "event"),
        ("신청 행사 일시", "event", "application"),
        ("신청 결과 발표 일시", "result", "application"),
        ("납부 일시", "payment", "event"),
        ("제출 일시", "submission", "event"),
        ("운영 일시", "operation", "event"),
        ("수업 일시", "operation", "application"),
        ("시행 일시", "effective", "event"),
        ("휴관 일시", "disruption", "event"),
    ],
)
@pytest.mark.parametrize("use_wrong_kind", [False, True])
def test_compound_time_label_keeps_only_its_explicit_schedule_kind(
    label: str, supported_kind: str, wrong_kind: str, use_wrong_kind: bool
) -> None:
    kind = wrong_kind if use_wrong_kind else supported_kind
    entry = _entry(kind=kind, label=label, start_date="2026-09-26", end_date="2026-09-30")

    result = _ground(f"{label}: 2026-09-26~2026-09-30", entry)

    assert result.dates == ([] if use_wrong_kind else [entry])
    assert result.uncertainties == ([REVIEW_NOTE] if use_wrong_kind else [])


@pytest.mark.parametrize("kind", ["event", "application"])
def test_generic_datetime_label_does_not_borrow_an_earlier_application_word(kind: str) -> None:
    entry = _entry(kind=kind, label="일시", start_date="2026-09-26", end_date="2026-09-30")

    result = _ground("신청 없이 참여 가능, 일시: 2026-09-26~2026-09-30", entry)

    assert result.dates == ([entry] if kind == "event" else [])
    assert result.uncertainties == ([] if kind == "event" else [REVIEW_NOTE])


@pytest.mark.parametrize("separator", ["\n", " "])
@pytest.mark.parametrize(
    ("kind", "start_date", "end_date"),
    [
        ("event", "2026-09-26", "2026-09-30"),
        ("application", "2026-10-01", "2026-10-02"),
    ],
)
def test_compound_time_labels_cannot_lend_another_schedule_their_dates(
    separator: str, kind: str, start_date: str, end_date: str
) -> None:
    source = f"신청 일시: 2026-09-26~2026-09-30{separator}행사 일시: 2026-10-01~2026-10-02"

    result = _ground(source, _entry(kind=kind, start_date=start_date, end_date=end_date))

    assert result.dates == []
    assert result.uncertainties == [REVIEW_NOTE]


def test_repository_notice_example_preserves_application_and_event_schedules() -> None:
    example_path = Path(__file__).resolve().parents[1] / "examples" / "notice.json"
    notice = NoticeInput.model_validate(json.loads(example_path.read_text(encoding="utf-8")))
    entries = [
        _entry(kind="application", start_date="2026-10-05", end_date="2026-10-12"),
        _entry(
            start_date="2026-10-17",
            end_date="2026-10-17",
            start_time="10:00",
            end_time="12:00",
        ),
    ]
    data = unknown_summary(notice).model_dump()
    data.update(
        category="event",
        summary="가을 걷기 행사",
        dates=[entry.model_dump() for entry in entries],
        uncertainties=[],
        evidence=[
            {"field": "summary", "excerpt": "가을 걷기 행사"},
            {"field": "dates", "excerpt": notice.body_text},
        ],
    )

    result = ground_summary(NoticeSummary.model_validate(data), notice)

    assert result.dates == entries
    assert result.uncertainties == []


@pytest.mark.parametrize(
    "source",
    [
        "신청: 2026-10-01~2026-10-19 / 행사: 2026-10-27~2026-10-30",
        "신청: 2026-10-01~2026-10-19\n행사: 2026-10-27~2026-10-30",
    ],
)
def test_event_cannot_borrow_application_dates(source: str) -> None:
    result = _ground(source, _entry(start_date="2026-10-01", end_date="2026-10-19"))

    assert result.dates == []
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize(
    "source",
    [
        "행사: 2026-09-26\n신청: 2026-09-26 오전 9시~오후 6시",
        "신청: 2026-09-26 오전 9시~오후 6시\n행사: 2026-09-26",
    ],
)
def test_event_cannot_borrow_times_from_a_different_schedule(source: str) -> None:
    result = _ground(
        source,
        _entry(
            start_date="2026-09-26",
            end_date="2026-09-26",
            start_time="09:00",
            end_time="18:00",
        ),
    )

    assert result.dates == []
    assert result.uncertainties == [REVIEW_NOTE]


def test_preceding_label_cannot_cross_a_different_schedule_label() -> None:
    result = _ground("행사:\n신청: 2026-09-26", _entry(start_date="2026-09-26"))

    assert result.dates == []
    assert result.uncertainties == [REVIEW_NOTE]


def test_unresolved_recurring_schedule_cannot_borrow_another_kind() -> None:
    result = _ground("수업: 매주 토요일", _entry(text="매주 토요일"))

    assert result.dates == []
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("context", ["행사 취소", "변경 전 행사"])
def test_cancelled_or_obsolete_schedule_is_not_restored(context: str) -> None:
    result = _ground(
        f"{context}: 2026-09-25~2026-09-30",
        _entry(start_date="2026-09-25", end_date="2026-09-30"),
    )

    assert result.dates == []
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize(
    ("kind", "day", "start_time", "end_time"),
    [
        ("event", "2026-09-27", "10:00", "17:00"),
        ("application", "2026-09-26", "09:00", "18:00"),
        ("event", "2026-09-26", "10:00", "17:00"),
        ("application", "2026-09-27", "09:00", "18:00"),
    ],
)
def test_adjacent_same_line_schedules_cannot_lend_dates_or_times(
    kind: str, day: str, start_time: str, end_time: str
) -> None:
    source = "행사: 2026-09-26 오전 9시~오후 6시 신청: 2026-09-27 오전 10시~오후 5시"
    result = _ground(
        source,
        _entry(
            kind=kind,
            start_date=day,
            end_date=day,
            start_time=start_time,
            end_time=end_time,
        ),
    )

    assert result.dates == []
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize(
    ("kind", "day", "start_time", "end_time"),
    [
        ("event", "2026-09-26", "09:00", "18:00"),
        ("application", "2026-09-27", "10:00", "17:00"),
    ],
)
def test_adjacent_same_line_schedules_keep_their_own_dates_and_times(
    kind: str, day: str, start_time: str, end_time: str
) -> None:
    source = "행사: 2026-09-26 오전 9시~오후 6시 신청: 2026-09-27 오전 10시~오후 5시"
    entry = _entry(
        kind=kind, start_date=day, end_date=day, start_time=start_time, end_time=end_time
    )

    result = _ground(source, entry)

    assert result.dates == [entry]
    assert result.uncertainties == []


def test_date_range_accepts_trailing_dots_and_inherited_year() -> None:
    entry = _entry(start_date="2026-09-25", end_date="2026-09-30")

    result = _ground("행사: 2026.9.25. ~ 9.30.", entry)

    assert result.dates == [entry]
    assert result.uncertainties == []


def test_korean_time_range_accepts_minutes() -> None:
    entry = _entry(
        start_date="2026-09-26",
        end_date="2026-09-26",
        start_time="09:30",
        end_time="18:15",
    )

    result = _ground("행사: 2026-09-26 오전 9시 30분~오후 6시 15분", entry)

    assert result.dates == [entry]
    assert result.uncertainties == []


@pytest.mark.parametrize(
    ("label", "date_field", "supported"),
    [
        ("행사 시작", "start_date", True),
        ("행사 시작", "end_date", False),
        ("행사 마감", "end_date", True),
        ("행사 마감", "start_date", False),
    ],
)
def test_single_date_respects_explicit_start_or_deadline_role(
    label: str, date_field: str, supported: bool
) -> None:
    entry = _entry(**{date_field: "2026-09-26"})

    result = _ground(f"{label}: 2026-09-26", entry)

    assert result.dates == ([entry] if supported else [])
    assert result.uncertainties == ([] if supported else [REVIEW_NOTE])


@pytest.mark.parametrize("time_field", ["start_time", "end_time"])
def test_single_time_until_phrase_means_end_time(time_field: str) -> None:
    entry = _entry(start_date="2026-09-26", end_date="2026-09-26", **{time_field: "10:00"})

    result = _ground("행사: 2026-09-26 오전 10시까지", entry)

    assert result.dates == ([entry] if time_field == "end_time" else [])
    assert result.uncertainties == ([] if time_field == "end_time" else [REVIEW_NOTE])


def test_unresolved_schedule_needs_text_not_just_a_label() -> None:
    result = _ground("행사: 매주 토요일", _entry(label="행사"))

    assert result.dates == []
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize("recurring", [False, True])
def test_event_cannot_borrow_application_times_expressed_in_prose(recurring: bool) -> None:
    when = "매주 토요일" if recurring else "2026-09-26"
    entry = _entry(
        text=when if recurring else None,
        start_date=None if recurring else when,
        end_date=None if recurring else when,
        start_time="09:00",
        end_time="18:00",
    )

    result = _ground(f"행사: {when} 신청은 오전 9시~오후 6시까지 받습니다", entry)

    assert result.dates == []
    assert result.uncertainties == [REVIEW_NOTE]


@pytest.mark.parametrize(
    ("verb", "date_field", "supported"),
    [
        ("종료됩니다", "start_date", False),
        ("종료됩니다", "end_date", True),
        ("시작됩니다", "start_date", True),
        ("시작됩니다", "end_date", False),
    ],
)
def test_single_date_role_can_follow_the_date_in_prose(
    verb: str, date_field: str, supported: bool
) -> None:
    entry = _entry(**{date_field: "2026-09-26"})

    result = _ground(f"행사는 2026-09-26에 {verb}", entry)

    assert result.dates == ([entry] if supported else [])
    assert result.uncertainties == ([] if supported else [REVIEW_NOTE])


@pytest.mark.parametrize(
    ("suffix", "date_field", "supported"),
    [
        ("까지", "start_date", False),
        ("까지", "end_date", True),
        ("부터", "start_date", True),
        ("부터", "end_date", False),
    ],
)
def test_weekday_between_date_and_role_does_not_hide_the_role(
    suffix: str, date_field: str, supported: bool
) -> None:
    entry = _entry(**{date_field: "2026-09-26"})

    result = _ground(f"행사: 2026-09-26(토){suffix}", entry)

    assert result.dates == ([entry] if supported else [])
    assert result.uncertainties == ([] if supported else [REVIEW_NOTE])


@pytest.mark.parametrize("time_field", ["start_time", "end_time"])
def test_single_time_role_can_follow_the_clock_value_in_prose(time_field: str) -> None:
    entry = _entry(end_date="2026-09-26", **{time_field: "18:00"})

    result = _ground("행사: 2026-09-26 오후 6시에 종료", entry)

    assert result.dates == ([entry] if time_field == "end_time" else [])
    assert result.uncertainties == ([] if time_field == "end_time" else [REVIEW_NOTE])


def test_repeated_same_day_date_does_not_allow_reversed_clock_range() -> None:
    result = _ground(
        "행사: 2026-09-26 오후 6시~2026-09-26 오전 9시",
        _entry(
            start_date="2026-09-26",
            end_date="2026-09-26",
            start_time="18:00",
            end_time="09:00",
        ),
    )

    assert result.dates == []
    assert result.uncertainties == [REVIEW_NOTE]


def test_cancelled_heading_applies_to_the_following_schedule_label() -> None:
    result = _ground(
        "행사 취소:\n일시:\n2026-09-26",
        _entry(label="일시", start_date="2026-09-26", end_date="2026-09-26"),
    )

    assert result.dates == []
    assert result.uncertainties == [REVIEW_NOTE]
