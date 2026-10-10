"""Bounded, repeatable historical collection for the verified Wolgye 1 board."""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from time import sleep

import psycopg

from pipeline.collect_wolgye1 import WolgyeFailure, _fetch_list_with_retry, _save_entries
from pipeline.config import ConfigError, DatabaseSettings, WolgyeSettings
from pipeline.sources.wolgye1_board import BoardEntry, WolgyeSourceError


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "backfill-wolgye1", help="collect a bounded historical range, preserving existing results",
    )
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--end-page", type=int, required=True)
    parser.add_argument("--since", type=date.fromisoformat, help="inclusive YYYY-MM-DD")
    parser.add_argument("--until", type=date.fromisoformat, help="inclusive YYYY-MM-DD")
    parser.add_argument("--limit", type=int, help="maximum selected unique Wolgye 1 notices")
    parser.add_argument("--dry-run", action="store_true", help="list and count without DB writes")


def _counts(database: DatabaseSettings) -> dict[str, int]:
    with psycopg.connect(database.database_url, connect_timeout=5) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        stored = conn.execute(
            "SELECT count(*) FROM notices WHERE category = 'dong' "
            "AND source_board = '1042' AND dong_group = 'wolgye1'",
        ).fetchone()[0]
        # The security-invoker app view must be measured as the real public reader.
        conn.execute("SET LOCAL ROLE anon")
        public = conn.execute(
            "SELECT count(*) FROM app_notice_list "
            "WHERE source = 'dong' AND dong_group = 'wolgye1'",
        ).fetchone()[0]
    return {"stored": stored, "public": public}


def run(args: argparse.Namespace) -> int:
    if (
        args.start_page < 1 or args.end_page < args.start_page
        or (args.limit is not None and args.limit < 1)
        or (args.since and args.until and args.since > args.until)
    ):
        print(
            "설정 오류: 페이지·건수는 양수이며 시작 범위는 종료 범위 이하여야 합니다.",
            file=sys.stderr,
        )
        return 2
    try:
        return _run(args, WolgyeSettings.from_env(), DatabaseSettings.from_env())
    except ConfigError as error:
        print(f"설정 오류: {error}", file=sys.stderr)
        return 2
    except WolgyeSourceError as error:
        print(f"목록 수집 실패: {error}", file=sys.stderr)
        return 1
    except psycopg.Error:
        print("DB 조회/저장 실패: 연결 및 anon 조회 권한을 확인하세요.", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace, settings: WolgyeSettings, database: DatabaseSettings) -> int:
    before = _counts(database)
    first = _fetch_list_with_retry(settings, 1)
    if args.end_page > first.total_pages:
        print(f"설정 오류: 게시판의 마지막 페이지는 {first.total_pages}입니다.", file=sys.stderr)
        return 2
    entries: dict[str, BoardEntry] = {}
    conflicts: set[str] = set()
    failed_pages: list[int] = []
    pages_read: list[int] = []
    duplicates = 0
    regular_seen: set[str] = set()
    for number in range(args.start_page, args.end_page + 1):
        if number != 1:
            sleep(1)
        try:
            page = first if number == 1 else _fetch_list_with_retry(settings, number)
        except WolgyeSourceError as error:
            if error.rate_limited:
                raise
            failed_pages.append(number)
            continue
        pages_read.append(number)
        if (page.total_count, page.total_pages, page.page_size) != (
            first.total_count, first.total_pages, first.page_size,
        ):
            failed_pages.append(number)
            continue
        # Numbered rows should be disjoint across pages. Repeated pinned rows are normal.
        if regular_seen.intersection(page.regular_post_sns) or (
            len(set(page.regular_post_sns)) != len(page.regular_post_sns)
        ):
            failed_pages.append(number)
        regular_seen.update(page.regular_post_sns)
        for entry in page.entries:
            # Shared pinned announcements from other districts must not consume the limit.
            if not entry.department.startswith("월계1동"):
                continue
            previous = entries.get(entry.post_sn)
            if previous is not None:
                duplicates += 1
                if (previous.title, previous.department, previous.registered_on) != (
                    entry.title, entry.department, entry.registered_on,
                ):
                    conflicts.add(entry.post_sn)
                entry = BoardEntry(
                    entry.post_sn, entry.title, entry.department, entry.registered_on,
                    previous.is_pinned or entry.is_pinned,
                )
            entries[entry.post_sn] = entry
    sleep(1)
    try:
        snapshot_stable = _fetch_list_with_retry(settings, 1) == first
    except WolgyeSourceError as error:
        if error.rate_limited:
            raise
        snapshot_stable = False
        failed_pages.append(1)
    eligible: list[BoardEntry] = []
    invalid_dates: list[WolgyeFailure] = []
    for entry in entries.values():
        try:
            registered_on = date.fromisoformat(entry.registered_on)
        except ValueError:
            invalid_dates.append(WolgyeFailure(entry.post_sn, "listing", "invalid_registered_on"))
            continue
        if (args.since is None or registered_on >= args.since) and (
            args.until is None or registered_on <= args.until
        ):
            eligible.append(entry)
    candidates = tuple(eligible)
    selected = candidates[:args.limit] if args.limit is not None else candidates
    saved, failures = 0, ()
    if not args.dry_run and selected:
        conn = psycopg.connect(database.database_url, connect_timeout=5, autocommit=True)
        saved, failures = _save_entries(
            conn, selected, settings, database.database_url,
            conflicts=tuple(conflicts), paced=True,
        )
    failures = (*invalid_dates, *failures)
    after = _counts(database)
    limited = len(selected) < len(candidates)
    complete = (
        snapshot_stable and not failed_pages and not conflicts and not limited
        and not failures and (args.dry_run or saved == len(selected))
    )
    print(json.dumps({
        "source": "wolgye1", "dry_run": args.dry_run,
        "start_page": args.start_page, "end_page": args.end_page,
        "since": args.since.isoformat() if args.since else None,
        "until": args.until.isoformat() if args.until else None,
        "limit": args.limit, "board_total_count": first.total_count,
        "board_total_pages": first.total_pages, "pages_read": pages_read,
        "failed_pages": failed_pages, "snapshot_stable": snapshot_stable,
        "duplicate_count": duplicates, "conflicting_post_sns": sorted(conflicts),
        "candidate_count": len(candidates), "selected_count": len(selected),
        "selected_post_sns": [e.post_sn for e in selected],
        "saved_count": saved, "limited": limited, "scope_complete": complete,
        "before": before, "after": after,
        "delta": {key: after[key] - before[key] for key in before},
        "failures": [asdict(failure) for failure in failures],
    }, ensure_ascii=True))
    return 0 if complete else 1
