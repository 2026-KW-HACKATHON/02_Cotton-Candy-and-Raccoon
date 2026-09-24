import argparse
import json
import sys
from collections.abc import Sequence

from pipeline.attachments.nowon_html import (
    AttachmentError,
    extract_files,
    extract_page_files,
    merge_files,
)
from pipeline.config import ConfigError, NowonSettings, Settings
from pipeline.sources.nowon_api import NowonSourceError, collect_one
from pipeline.sources.nowon_page import NowonPageError, fetch_notice_page


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Nowon notice collection pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check-config", help="validate required environment variables")
    check.add_argument("--source", choices=["nowon"], help="check only this source's settings")
    collect = subparsers.add_parser("collect-one", help="read one notice without saving to DB")
    collect.add_argument("--source", choices=["nowon"], required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "check-config":
        try:
            if args.source == "nowon":
                NowonSettings.from_env()
            else:
                Settings.from_env()
        except ConfigError as error:
            print(f"설정 오류: {error}", file=sys.stderr)
            return 2
        print("환경 변수 형식을 확인했습니다. API 인증과 DB 연결은 확인하지 않았습니다.")
        return 0

    if args.command == "collect-one":
        try:
            settings = NowonSettings.from_env()
        except ConfigError as error:
            print(f"설정 오류: {error}", file=sys.stderr)
            return 2
        try:
            notice = collect_one(settings)
            body_files = extract_files(notice)
            page_url, page_html = fetch_notice_page(notice, settings)
            page_files = extract_page_files(notice, page_html, page_url)
            files = merge_files(body_files, page_files)
        except (NowonSourceError, NowonPageError, AttachmentError) as error:
            hint = (
                " 잠시 후 다시 실행할 수 있습니다."
                if isinstance(error, (NowonSourceError, NowonPageError)) and error.retryable else ""
            )
            print(settings.redact(f"수집 실패: {error}{hint}"), file=sys.stderr)
            return 1
        # Print a bounded summary; retain full original HTML in the returned model.
        summary = {
            "category": notice.category, "post_sn": notice.post_sn,
            "title": notice.title, "registered_on": notice.registered_on,
            "department": notice.department, "url": notice.url,
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
