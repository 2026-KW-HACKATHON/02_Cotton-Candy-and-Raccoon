import argparse
import json
import sys
from collections.abc import Sequence

import psycopg

from pipeline.attachments.nowon_html import (
    AttachmentError,
    extract_files,
    extract_page_files,
    merge_files,
)
from pipeline.collect_nowon import collect_and_save_nowon, collect_and_save_nowon_scheduled
from pipeline.collect_wolgye1 import (
    collect_and_save_wolgye1,
    collect_and_save_wolgye1_scheduled,
    collect_one_wolgye1,
)
from pipeline.config import ConfigError, DatabaseSettings, NowonSettings, Settings, WolgyeSettings
from pipeline.sources.nowon_api import NowonSourceError, collect_one
from pipeline.sources.nowon_page import NowonPageError, fetch_notice_page
from pipeline.sources.wolgye1_board import WolgyeSourceError
from pipeline.storage.notice_bundle import save_notice_with_files
from pipeline.transform.dong import DongTransformError
from pipeline.transform.nowon import TransformError, transform_nowon_notice


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Nowon notice collection pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check-config", help="validate required environment variables")
    check.add_argument(
        "--source", choices=["nowon", "wolgye1"], help="check only this source's settings",
    )
    collect = subparsers.add_parser("collect-one", help="collect and save one notice to DB")
    collect.add_argument("--source", choices=["nowon", "wolgye1"], required=True)
    collect.add_argument("--post-sn", help="select a Wolgye 1-dong post on the chosen list page")
    collect.add_argument("--page", type=int, default=1, help="Wolgye 1-dong list page (default: 1)")
    collect_many = subparsers.add_parser(
        "collect", help="collect and save source notices independently to DB",
    )
    collect_many.add_argument("--source", choices=["nowon", "wolgye1"], required=True)
    collect_many.add_argument(
        "--limit", type=int, help="process only the first N notices; partial run",
    )
    collect_many.add_argument(
        "--mode", choices=["new", "refresh"],
        help="new posts at 09/13 or recent-post refresh at 17",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "check-config":
        try:
            if args.source == "nowon":
                NowonSettings.from_env()
            elif args.source == "wolgye1":
                WolgyeSettings.from_env()
            else:
                Settings.from_env()
        except ConfigError as error:
            print(f"설정 오류: {error}", file=sys.stderr)
            return 2
        print("환경 변수 형식을 확인했습니다. API 인증과 DB 연결은 확인하지 않았습니다.")
        return 0

    if args.command == "collect":
        if args.limit is not None and args.limit < 1:
            print("설정 오류: --limit은 1 이상의 정수여야 합니다.", file=sys.stderr)
            return 2
        if args.mode and args.limit is not None:
            print("설정 오류: --mode와 --limit은 함께 사용할 수 없습니다.", file=sys.stderr)
            return 2
        if args.source == "wolgye1":
            try:
                settings = WolgyeSettings.from_env()
                database = DatabaseSettings.from_env()
            except ConfigError as error:
                print(f"설정 오류: {error}", file=sys.stderr)
                return 2
            try:
                if args.mode:
                    scheduled = collect_and_save_wolgye1_scheduled(
                        settings, database, mode=args.mode,
                    )
                    print(json.dumps({
                        "mode": scheduled.mode,
                        "total_count": scheduled.total_count,
                        "selected_count": scheduled.selected_count,
                        "saved_count": scheduled.saved_count,
                        "pages_read": scheduled.pages_read,
                        "initial_baseline": scheduled.initial_baseline,
                        "listing_complete": scheduled.listing_complete,
                        "failed_pages": scheduled.failed_pages,
                        "complete": scheduled.complete,
                        "failures": [
                            {"post_sn": item.post_sn, "stage": item.stage,
                             "reason_code": item.reason_code}
                            for item in scheduled.failures
                        ],
                    }, ensure_ascii=True))
                    return 0 if scheduled.complete else 1
                result = collect_and_save_wolgye1(settings, database, limit=args.limit)
            except WolgyeSourceError as error:
                print(f"목록 수집 실패: {error}", file=sys.stderr)
                return 1
            except psycopg.Error:
                print("DB 연결 실패: 연결 설정을 확인하세요.", file=sys.stderr)
                return 1
            print(json.dumps({
                "total_count": result.total_count,
                "listed_count": result.listed_count,
                "attempted_count": result.attempted_count,
                "saved_count": result.saved_count,
                "listing_complete": result.listing_complete,
                "limited": result.limited,
                "complete": result.complete,
                "failed_pages": result.failed_pages,
                "duplicate_count": result.duplicate_count,
                "failures": [
                    {"post_sn": item.post_sn, "stage": item.stage,
                     "reason_code": item.reason_code}
                    for item in result.failures
                ],
            }, ensure_ascii=True))
            return 0 if result.complete else 1
        try:
            settings = NowonSettings.from_env()
            database = DatabaseSettings.from_env()
        except ConfigError as error:
            print(f"설정 오류: {error}", file=sys.stderr)
            return 2
        try:
            if args.mode:
                scheduled = collect_and_save_nowon_scheduled(
                    settings, database, mode=args.mode,
                )
                print(json.dumps({
                    "mode": scheduled.mode,
                    "total_count": scheduled.total_count,
                    "selected_count": scheduled.selected_count,
                    "saved_count": scheduled.saved_count,
                    "pages_read": scheduled.pages_read,
                    "initial_baseline": scheduled.initial_baseline,
                    "listing_complete": scheduled.listing_complete,
                    "failed_ranges": scheduled.failed_ranges,
                    "complete": scheduled.complete,
                    "failures": [
                        {"post_sn": settings.redact(item.post_sn), "stage": item.stage,
                         "reason_code": item.reason_code}
                        for item in scheduled.failures
                    ],
                }, ensure_ascii=True))
                return 0 if scheduled.complete else 1
            result = collect_and_save_nowon(settings, database, limit=args.limit)
        except (NowonSourceError, ValueError) as error:
            print(settings.redact(f"목록 수집 실패: {error}"), file=sys.stderr)
            return 1
        except psycopg.Error:
            print("DB 연결 실패: 연결 설정을 확인하세요.", file=sys.stderr)
            return 1
        failures = [
            {
                "post_sn": settings.redact(failure.post_sn),
                "stage": failure.stage,
                "reason_code": failure.reason_code,
            }
            for failure in result.failures
        ]
        print(json.dumps({
            "total_count": result.total_count,
            "listed_count": result.listed_count,
            "attempted_count": result.attempted_count,
            "saved_count": result.saved_count,
            "listing_complete": result.listing_complete,
            "limited": result.limited,
            "complete": result.complete,
            "failed_pages": result.failed_pages,
            "failures": failures,
        }, ensure_ascii=True))
        return 0 if result.complete else 1

    if args.command == "collect-one":
        if args.source == "wolgye1":
            if args.page < 1 or (args.page != 1 and args.post_sn is None):
                print("설정 오류: --page는 1 이상이며 2페이지부터 --post-sn이 필요합니다.",
                      file=sys.stderr)
                return 2
            try:
                settings = WolgyeSettings.from_env()
                database = DatabaseSettings.from_env()
            except ConfigError as error:
                print(f"설정 오류: {error}", file=sys.stderr)
                return 2
            try:
                record, files = collect_one_wolgye1(
                    settings, post_sn=args.post_sn, page=args.page,
                )
            except (WolgyeSourceError, AttachmentError, DongTransformError) as error:
                print(f"수집 실패: {error}", file=sys.stderr)
                return 1
            try:
                with psycopg.connect(database.database_url, connect_timeout=5) as conn:
                    notice_id = save_notice_with_files(conn, record, files)
            except (psycopg.Error, ValueError):
                print("DB 저장 실패: 연결 또는 저장 작업을 확인하세요.", file=sys.stderr)
                return 1
            print(json.dumps({
                "notice_id": notice_id, "category": record.category,
                "post_sn": record.post_sn, "title": record.title,
                "dong_group": record.dong_group, "is_pinned": record.is_pinned,
                "registered_on": record.registered_on.isoformat(),
                "body_html_length": len(record.body_html or ""),
                "attachment_count": sum(file.kind == "attachment" for file in files),
                "inline_image_count": sum(file.kind == "inline_image" for file in files),
            }, ensure_ascii=True))
            return 0

        if args.post_sn is not None or args.page != 1:
            print("설정 오류: --post-sn·--page는 wolgye1 출처에만 사용할 수 있습니다.",
                  file=sys.stderr)
            return 2
        try:
            settings = NowonSettings.from_env()
            database = DatabaseSettings.from_env()
        except ConfigError as error:
            print(f"설정 오류: {error}", file=sys.stderr)
            return 2
        try:
            notice = collect_one(settings)
            body_files = extract_files(notice)
            page_url, page_html = fetch_notice_page(notice, settings)
            page_files = extract_page_files(notice, page_html, page_url)
            files = merge_files(body_files, page_files)
            record = transform_nowon_notice(notice)
        except (NowonSourceError, NowonPageError, AttachmentError, TransformError) as error:
            hint = (
                " 잠시 후 다시 실행할 수 있습니다."
                if isinstance(error, (NowonSourceError, NowonPageError)) and error.retryable else ""
            )
            print(settings.redact(f"수집 실패: {error}{hint}"), file=sys.stderr)
            return 1
        try:
            with psycopg.connect(database.database_url, connect_timeout=5) as conn:
                notice_id = save_notice_with_files(conn, record, files)
        except psycopg.Error:
            print("DB 저장 실패: 연결 또는 저장 작업을 확인하세요.", file=sys.stderr)
            return 1
        except ValueError as error:
            print(settings.redact(f"DB 저장 실패: {error}"), file=sys.stderr)
            return 1
        # Print a bounded summary; retain full original HTML in the returned model.
        summary = {
            "notice_id": notice_id,
            "category": notice.category, "post_sn": notice.post_sn,
            "title": record.title, "registered_on": record.registered_on.isoformat(),
            "department": record.department, "url": record.url,
            "license_type": notice.license_type,
            "body_html_length": len(notice.body_html or ""),
            "attachment_count": sum(file.kind == "attachment" for file in files),
            "inline_image_count": sum(file.kind == "inline_image" for file in files),
        }
        safe_summary = {
            name: settings.redact(value) if isinstance(value, str) else value
            for name, value in summary.items()
        }
        print(json.dumps(safe_summary, ensure_ascii=True))
        return 0

    return 2
