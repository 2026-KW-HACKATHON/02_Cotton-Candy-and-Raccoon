import argparse
import json
import sys
from collections.abc import Sequence

import psycopg

from pipeline.after_collect import AfterCollectEasyText
from pipeline.attachments.nowon_html import (
    AttachmentError,
    extract_files,
    extract_page_files,
    merge_files,
    recover_masked_body_urls,
)
from pipeline.attachments.seoul_html import SeoulAttachmentError
from pipeline.collect_nowon import collect_and_save_nowon, collect_and_save_nowon_scheduled
from pipeline.collect_seoul import SeoulStorageError, collect_and_save_one, prepare_one
from pipeline.collect_seoul_scheduled import collect_scheduled as collect_seoul_scheduled
from pipeline.collect_wolgye1 import (
    collect_and_save_wolgye1,
    collect_and_save_wolgye1_scheduled,
    collect_one_wolgye1,
)
from pipeline.config import (
    ConfigError,
    DatabaseSettings,
    NowonSettings,
    SeoulNewsSettings,
    Settings,
    WolgyeSettings,
)
from pipeline.sources.nowon_api import NowonSourceError, collect_one
from pipeline.sources.nowon_page import NowonPageError, NowonPageMissing, fetch_notice_page
from pipeline.sources.seoul_api import SeoulSourceError
from pipeline.sources.seoul_api import collect_one as collect_one_seoul
from pipeline.sources.wolgye1_board import WolgyeSourceError
from pipeline.storage.notice_bundle import save_notice_with_files
from pipeline.summary_run import summarize_one
from pipeline.transform.dong import DongTransformError
from pipeline.transform.gemini_prompt import GeminiConfigurationError, load_gemini_api_key
from pipeline.transform.nowon import TransformError, transform_nowon_notice
from pipeline.transform.seoul import SeoulTransformError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Nowon notice collection pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check-config", help="validate required environment variables")
    check.add_argument(
        "--source",
        choices=["nowon", "wolgye1", "seoul"],
        help="check only this source's settings",
    )
    inspect = subparsers.add_parser("inspect-one", help="read one API row without DB writes")
    inspect.add_argument("--source", choices=["nowon", "seoul"], required=True)
    prepared = subparsers.add_parser(
        "inspect-prepared",
        help="prepare one notice without DB writes",
    )
    prepared.add_argument("--source", choices=["seoul"], required=True)
    prepared.add_argument(
        "--source-board",
        choices=["21", "22", "23", "24", "25", "26", "27", "30"],
    )
    prepared.add_argument("--index", type=int, default=1, help="1-based API row, not post_sn")
    collect = subparsers.add_parser("collect-one", help="collect and save one notice to DB")
    collect.add_argument("--source", choices=["nowon", "wolgye1", "seoul"], required=True)
    collect.add_argument("--source-board", choices=["21", "22", "23", "24", "25", "26", "27", "30"])
    collect.add_argument("--index", type=int, default=1, help="Seoul API row index")
    collect.add_argument("--post-sn", help="select a Wolgye 1-dong post on the chosen list page")
    collect.add_argument("--page", type=int, default=1, help="Wolgye 1-dong list page (default: 1)")
    collect.add_argument(
        "--easy-text",
        action="store_true",
        help="convert the saved body to easy text after saving",
    )
    collect_many = subparsers.add_parser(
        "collect",
        help="collect and save source notices independently to DB",
    )
    collect_many.add_argument("--source", choices=["nowon", "wolgye1", "seoul"], required=True)
    collect_many.add_argument(
        "--source-board",
        choices=["21", "22", "23", "24", "25", "26", "27", "30"],
        help="Seoul board only; omit to process all eight boards",
    )
    collect_many.add_argument(
        "--limit",
        type=int,
        help="process only the first N notices; partial run",
    )
    collect_many.add_argument(
        "--mode",
        choices=["new", "refresh"],
        help="new posts at 09/13 or recent-post refresh at 17",
    )
    collect_many.add_argument(
        "--easy-text",
        action="store_true",
        help="convert each saved body to easy text after saving",
    )
    summarize = subparsers.add_parser(
        "summarize-one",
        help="summarize one stored notice with Gemini and save the result to DB",
    )
    summarize.add_argument("--notice-id", type=int, required=True, help="notices.id")
    return parser


def _print_summary(summary: dict[str, object], processor: AfterCollectEasyText | None) -> None:
    if processor is not None:
        summary["easy_text"] = processor.report()
    print(json.dumps(summary, ensure_ascii=True))


def _exit_code(complete: bool, processor: AfterCollectEasyText | None) -> int:
    return 0 if complete and (processor is None or processor.complete) else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command in ("collect", "collect-one") and args.source == "seoul" and args.easy_text:
        print(
            "설정 오류: 서울시 수집 후 쉬운말 자동 처리는 아직 지원하지 않습니다. "
            "--easy-text는 nowon·wolgye1 출처에서 사용하세요.",
            file=sys.stderr,
        )
        return 2

    if args.command == "summarize-one":
        return _summarize_one(args)

    if args.command == "check-config":
        try:
            if args.source == "nowon":
                NowonSettings.from_env()
            elif args.source == "wolgye1":
                WolgyeSettings.from_env()
            elif args.source == "seoul":
                SeoulNewsSettings.from_env()
            else:
                Settings.from_env()
        except ConfigError as error:
            print(f"설정 오류: {error}", file=sys.stderr)
            return 2
        print("환경 변수 형식을 확인했습니다. API 인증과 DB 연결은 확인하지 않았습니다.")
        return 0

    if args.command == "inspect-prepared":
        if args.index < 1:
            print("설정 오류: --index는 1 이상의 정수여야 합니다.", file=sys.stderr)
            return 2
        try:
            settings = SeoulNewsSettings.from_env()
            record, files = prepare_one(settings, source_board=args.source_board, index=args.index)
        except ConfigError as error:
            print(f"설정 오류: {error}", file=sys.stderr)
            return 2
        except (
            SeoulSourceError,
            SeoulAttachmentError,
            SeoulTransformError,
        ) as error:
            print(settings.redact(f"서울시 수집·변환 실패: {error}"), file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "source": "seoul",
                    "source_board": record.source_board,
                    "post_sn": record.post_sn,
                    "title": record.title,
                    "registered_on": record.registered_on.isoformat(),
                    "url": record.url,
                    "license_type": record.license_type,
                    "body_html_length": len(record.body_html or ""),
                    "attachment_count": sum(f.kind == "attachment" for f in files),
                    "inline_image_count": sum(f.kind == "inline_image" for f in files),
                    "stored": False,
                },
                ensure_ascii=True,
            )
        )
        return 0

    if args.command == "inspect-one":
        try:
            if args.source == "seoul":
                raw = collect_one_seoul(SeoulNewsSettings.from_env())
            else:
                raw = collect_one(NowonSettings.from_env())
        except ConfigError as error:
            print(f"설정 오류: {error}", file=sys.stderr)
            return 2
        except (SeoulSourceError, NowonSourceError) as error:
            print(f"API 수집 실패: {error}", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "source": args.source,
                    "source_board": raw.source_board,
                    "post_sn": raw.post_sn,
                    "title": raw.title,
                    "registered_on": raw.registered_on,
                    "body_html_length": len(raw.body_html or ""),
                    "stored": False,
                },
                ensure_ascii=True,
            )
        )
        return 0

    if args.command == "collect":
        if args.source == "seoul":
            return _collect_seoul(args)
        if args.source_board is not None:
            print("설정 오류: --source-board는 seoul 출처에만 사용할 수 있습니다.", file=sys.stderr)
            return 2
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
            processor = AfterCollectEasyText(database) if args.easy_text else None
            after_save = {"after_save": processor} if processor is not None else {}
            try:
                if args.mode:
                    scheduled = collect_and_save_wolgye1_scheduled(
                        settings,
                        database,
                        mode=args.mode,
                        **after_save,
                    )
                    _print_summary(
                        {
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
                                {
                                    "post_sn": item.post_sn,
                                    "stage": item.stage,
                                    "reason_code": item.reason_code,
                                }
                                for item in scheduled.failures
                            ],
                        },
                        processor,
                    )
                    return _exit_code(scheduled.complete, processor)
                result = collect_and_save_wolgye1(
                    settings,
                    database,
                    limit=args.limit,
                    **after_save,
                )
            except WolgyeSourceError as error:
                print(f"목록 수집 실패: {error}", file=sys.stderr)
                return 1
            except psycopg.Error:
                print("DB 연결 실패: 연결 설정을 확인하세요.", file=sys.stderr)
                return 1
            _print_summary(
                {
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
                        {
                            "post_sn": item.post_sn,
                            "stage": item.stage,
                            "reason_code": item.reason_code,
                        }
                        for item in result.failures
                    ],
                },
                processor,
            )
            return _exit_code(result.complete, processor)
        try:
            settings = NowonSettings.from_env()
            database = DatabaseSettings.from_env()
        except ConfigError as error:
            print(f"설정 오류: {error}", file=sys.stderr)
            return 2
        processor = AfterCollectEasyText(database) if args.easy_text else None
        after_save = {"after_save": processor} if processor is not None else {}
        try:
            if args.mode:
                scheduled = collect_and_save_nowon_scheduled(
                    settings,
                    database,
                    mode=args.mode,
                    **after_save,
                )
                _print_summary(
                    {
                        "mode": scheduled.mode,
                        "total_count": scheduled.total_count,
                        "selected_count": scheduled.selected_count,
                        "saved_count": scheduled.saved_count,
                        "skipped_count": scheduled.skipped_count,
                        "skipped": [
                            {"post_sn": settings.redact(item.post_sn), "stage": item.stage,
                             "reason_code": item.reason_code} for item in scheduled.skipped
                        ],
                        "failed_count": len(scheduled.failures),
                        "pages_read": scheduled.pages_read,
                        "initial_baseline": scheduled.initial_baseline,
                        "listing_complete": scheduled.listing_complete,
                        "failed_ranges": scheduled.failed_ranges,
                        "complete": scheduled.complete,
                        "failures": [
                            {
                                "post_sn": settings.redact(item.post_sn),
                                "stage": item.stage,
                                "reason_code": item.reason_code,
                            }
                            for item in scheduled.failures
                        ],
                    },
                    processor,
                )
                return _exit_code(scheduled.complete, processor)
            result = collect_and_save_nowon(
                settings,
                database,
                limit=args.limit,
                **after_save,
            )
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
        _print_summary(
            {
                "total_count": result.total_count,
                "listed_count": result.listed_count,
                "attempted_count": result.attempted_count,
                "saved_count": result.saved_count,
                "skipped_count": result.skipped_count,
                "skipped": [
                    {"post_sn": settings.redact(item.post_sn), "stage": item.stage,
                     "reason_code": item.reason_code} for item in result.skipped
                ],
                "failed_count": len(result.failures),
                "listing_complete": result.listing_complete,
                "limited": result.limited,
                "complete": result.complete,
                "failed_pages": result.failed_pages,
                "failures": failures,
            },
            processor,
        )
        return _exit_code(result.complete, processor)

    if args.command == "collect-one":
        if args.source == "seoul":
            if args.index < 1 or args.post_sn is not None or args.page != 1:
                print("설정 오류: 서울시는 --index와 --source-board를 사용하세요.", file=sys.stderr)
                return 2
            try:
                settings = SeoulNewsSettings.from_env()
                database = DatabaseSettings.from_env()
            except ConfigError as error:
                print(f"설정 오류: {error}", file=sys.stderr)
                return 2
            try:
                notice_id, record, files = collect_and_save_one(
                    settings,
                    database,
                    source_board=args.source_board,
                    index=args.index,
                )
            except (
                SeoulSourceError,
                SeoulAttachmentError,
                SeoulTransformError,
                SeoulStorageError,
            ) as error:
                print(settings.redact(f"수집·저장 실패: {error}"), file=sys.stderr)
                return 1
            print(
                json.dumps(
                    {
                        "notice_id": notice_id,
                        "category": record.category,
                        "source_board": record.source_board,
                        "post_sn": record.post_sn,
                        "title": record.title,
                        "registered_on": record.registered_on.isoformat(),
                        "url": record.url,
                        "license_type": record.license_type,
                        "body_html_length": len(record.body_html or ""),
                        "attachment_count": sum(f.kind == "attachment" for f in files),
                        "inline_image_count": sum(f.kind == "inline_image" for f in files),
                        "stored": True,
                    },
                    ensure_ascii=True,
                )
            )
            return 0
        if args.source_board is not None or args.index != 1:
            print(
                "설정 오류: --source-board·--index는 seoul 출처에만 사용할 수 있습니다.",
                file=sys.stderr,
            )
            return 2
        if args.source == "wolgye1":
            if args.page < 1 or (args.page != 1 and args.post_sn is None):
                print(
                    "설정 오류: --page는 1 이상이며 2페이지부터 --post-sn이 필요합니다.",
                    file=sys.stderr,
                )
                return 2
            try:
                settings = WolgyeSettings.from_env()
                database = DatabaseSettings.from_env()
            except ConfigError as error:
                print(f"설정 오류: {error}", file=sys.stderr)
                return 2
            try:
                record, files = collect_one_wolgye1(
                    settings,
                    post_sn=args.post_sn,
                    page=args.page,
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
            processor = AfterCollectEasyText(database) if args.easy_text else None
            if processor is not None:
                processor(notice_id)
            _print_summary(
                {
                    "notice_id": notice_id,
                    "category": record.category,
                    "post_sn": record.post_sn,
                    "title": record.title,
                    "dong_group": record.dong_group,
                    "is_pinned": record.is_pinned,
                    "registered_on": record.registered_on.isoformat(),
                    "body_html_length": len(record.body_html or ""),
                    "attachment_count": sum(file.kind == "attachment" for file in files),
                    "inline_image_count": sum(file.kind == "inline_image" for file in files),
                },
                processor,
            )
            return _exit_code(True, processor)

        if args.post_sn is not None or args.page != 1:
            print(
                "설정 오류: --post-sn·--page는 wolgye1 출처에만 사용할 수 있습니다.",
                file=sys.stderr,
            )
            return 2
        try:
            settings = NowonSettings.from_env()
            database = DatabaseSettings.from_env()
        except ConfigError as error:
            print(f"설정 오류: {error}", file=sys.stderr)
            return 2
        try:
            notice = collect_one(settings)
            page_url, page_html = fetch_notice_page(notice, settings)
            notice = recover_masked_body_urls(notice, page_html)
            body_files = extract_files(notice)
            page_files = extract_page_files(notice, page_html, page_url)
            files = merge_files(body_files, page_files)
            record = transform_nowon_notice(notice)
        except NowonPageMissing:
            _print_summary({
                "selected_count": 1, "saved_count": 0, "skipped_count": 1,
                "failed_count": 0, "complete": True, "failures": [],
                "skipped": [{"post_sn": settings.redact(notice.post_sn),
                             "stage": "page_missing", "reason_code": "source_page_missing"}],
            }, None)
            return 0
        except (NowonSourceError, NowonPageError, AttachmentError, TransformError) as error:
            hint = (
                " 잠시 후 다시 실행할 수 있습니다."
                if isinstance(error, (NowonSourceError, NowonPageError)) and error.retryable
                else ""
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
        processor = AfterCollectEasyText(database) if args.easy_text else None
        if processor is not None:
            processor(notice_id)
        # Print a bounded summary; retain full original HTML in the returned model.
        summary = {
            "notice_id": notice_id,
            "category": notice.category,
            "post_sn": notice.post_sn,
            "title": record.title,
            "registered_on": record.registered_on.isoformat(),
            "department": record.department,
            "url": record.url,
            "license_type": notice.license_type,
            "body_html_length": len(notice.body_html or ""),
            "attachment_count": sum(file.kind == "attachment" for file in files),
            "inline_image_count": sum(file.kind == "inline_image" for file in files),
        }
        safe_summary = {
            name: settings.redact(value) if isinstance(value, str) else value
            for name, value in summary.items()
        }
        _print_summary(safe_summary, processor)
        return _exit_code(True, processor)

    return 2


def _collect_seoul(args: argparse.Namespace) -> int:
    if args.mode is None or args.limit is not None:
        print("설정 오류: 서울시는 --mode가 필요하며 --limit은 미지원입니다.", file=sys.stderr)
        return 2
    try:
        settings = SeoulNewsSettings.from_env()
        database = DatabaseSettings.from_env()
        result = collect_seoul_scheduled(
            settings,
            database,
            mode=args.mode,
            source_board=args.source_board,
        )
    except (ConfigError, ValueError) as error:
        print(f"서울시 수집 실패: {error}", file=sys.stderr)
        return 1 if isinstance(error, SeoulStorageError) else 2
    print(
        json.dumps(
            {
                "mode": result.mode,
                "selected_count": sum(b.selected_count for b in result.boards),
                "saved_count": sum(b.saved_count for b in result.boards),
                "complete": result.complete,
                "boards": [
                    {
                        "source_board": b.source_board,
                        "total_count": b.total_count,
                        "selected_count": b.selected_count,
                        "saved_count": b.saved_count,
                        "pages_read": b.pages_read,
                        "initial_baseline": b.initial_baseline,
                        "listing_complete": b.listing_complete,
                        "complete": b.complete,
                        "failures": [
                            {"post_sn": f.post_sn, "stage": f.stage, "reason_code": f.reason_code}
                            for f in b.failures
                        ],
                    }
                    for b in result.boards
                ],
            },
            ensure_ascii=True,
        )
    )
    return 0 if result.complete else 1


def _summarize_one(args: argparse.Namespace) -> int:
    """Exit codes: 0 summarized/needs_review, 1 failed, 2 arguments or configuration,
    3 not_found, 4 superseded, 5 storage_failed. See README "공지 ID로 요약 실행"."""
    if not 0 < args.notice_id <= 2**63 - 1:
        print("설정 오류: --notice-id는 1 이상의 정수여야 합니다.", file=sys.stderr)
        return 2
    try:
        database = DatabaseSettings.from_env()
        api_key = load_gemini_api_key()
    except (ConfigError, GeminiConfigurationError) as error:
        print(f"설정 오류: {error}", file=sys.stderr)
        return 2
    result = summarize_one(database, args.notice_id, api_key=api_key)
    print(json.dumps(result.report(), ensure_ascii=True))
    return result.exit_code
