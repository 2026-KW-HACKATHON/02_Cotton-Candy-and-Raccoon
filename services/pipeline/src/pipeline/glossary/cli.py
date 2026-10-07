"""Default to Gemini conversion and cached Ourmalsam word definitions."""

import argparse
import json
import sys
from pathlib import Path

import psycopg
from pydantic import ValidationError

from pipeline.config import ConfigError, DatabaseSettings
from pipeline.glossary.document import RULES_VERSION, NoticeGlossaryResult
from pipeline.glossary.notice_service import (
    load_notice_glossary_input,
    process_and_store_notice_glossary,
)
from pipeline.glossary.process import process_notice_glossary
from pipeline.glossary.service import query_glossary
from pipeline.glossary.source import NoticeGlossaryInput, source_hash
from pipeline.storage.glossary import GlossaryStorageError
from pipeline.storage.notice_glossary import NoticeGlossaryStorageError


def _resume_result(path: Path) -> NoticeGlossaryResult | None:
    """Discard a checked old rule generation before applying current term rules."""
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict):
        version = payload.get("rules_version")
        if isinstance(version, str) and version.strip() and version != RULES_VERSION:
            original = NoticeGlossaryInput(
                notice_id=payload.get("notice_id"),
                text=payload.get("original_text"),
                notice_revision=payload.get("notice_revision"),
            )
            if payload.get("source_hash") != source_hash(original):
                raise ValueError("이전 처리 결과의 원문 해시가 일치하지 않습니다.")
            return None
    return NoticeGlossaryResult.model_validate(payload)


def _dictionary_main() -> int:
    """Retain the earlier dictionary-only implementation outside the default CLI."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Replace verified official terms; keep original text."
    )
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--input", type=Path, help="JSON with text and optional notice_id")
    inputs.add_argument(
        "--notice-id", type=int, help="Read title and body from the existing database"
    )
    parser.add_argument("--save", action="store_true", help="Save results using DATABASE_URL")
    parser.add_argument(
        "--resume", type=Path, help="Resume a previous result JSON without a database"
    )
    parser.add_argument("--output", type=Path, help="Save full result JSON to this file")
    parser.add_argument("--max-queries", type=int, default=100)
    parser.add_argument("--refresh", action="store_true", help="Rebuild a saved notice result")
    args = parser.parse_args()
    if args.notice_id is not None and args.notice_id < 1:
        parser.error("--notice-id must be a positive integer.")
    if args.max_queries < 1 or (args.resume and (args.save or args.notice_id)):
        parser.error("Use a positive limit; --resume is for file input without --save.")
    if args.refresh and not (args.save or args.notice_id):
        parser.error("--refresh requires database mode.")
    try:
        source = (
            NoticeGlossaryInput.model_validate_json(args.input.read_text(encoding="utf-8-sig"))
            if args.input
            else None
        )
        if args.save or args.notice_id:
            settings = DatabaseSettings.from_env()
            with psycopg.connect(settings.database_url) as conn:
                if source is None:
                    source = load_notice_glossary_input(conn, args.notice_id)
                result = process_and_store_notice_glossary(
                    conn,
                    source,
                    max_queries=args.max_queries,
                    refresh=args.refresh,
                )
                # This command owns the transaction; rollback on output-file errors too.
                rendered = result.model_dump_json(indent=2)
                if args.output:
                    args.output.write_text(rendered, encoding="utf-8")
        else:
            previous = _resume_result(args.resume) if args.resume else None
            result = process_notice_glossary(
                source,
                query_glossary,
                previous=previous,
                max_queries=args.max_queries,
            )
            rendered = result.model_dump_json(indent=2)
            if args.output:
                args.output.write_text(rendered, encoding="utf-8")
    except (ValidationError, ValueError, ConfigError):
        print("공지 입력 또는 설정이 올바르지 않습니다.", file=sys.stderr)
        return 2
    except (psycopg.Error, GlossaryStorageError, NoticeGlossaryStorageError, RuntimeError):
        print(
            "공지 용어 처리·저장에 실패했습니다. 기존 원문은 변경하지 않았습니다.", file=sys.stderr
        )
        return 1
    except (OSError, UnicodeError):
        print("입력·결과 파일을 읽거나 저장할 수 없습니다.", file=sys.stderr)
        return 2
    print(rendered)
    return 0 if result.status == "completed" else 3


def main() -> int:
    """Use the Gemini + Ourmalsam route for normal CLI invocations."""
    from pipeline.glossary.easy_language_cli import main as easy_language_main

    return easy_language_main()


if __name__ == "__main__":
    raise SystemExit(main())
