"""Convert notice terms with Gemini and reuse saved notice conversions."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg
from pydantic import ValidationError

from pipeline.config import ConfigError, DatabaseSettings
from pipeline.gemini_execution import (
    ExecutionStats,
    GeminiExecutionError,
    capture_execution_stats,
)
from pipeline.glossary.easy_language import (
    DEFAULT_MODEL,
    EasyLanguageConfigurationError,
    EasyLanguageValidationError,
    NoNoticeBodyError,
    simplify_notice,
)
from pipeline.glossary.easy_language_service import simplify_and_store_notice
from pipeline.glossary.notice_service import load_notice_glossary_input
from pipeline.glossary.source import NoticeGlossaryInput
from pipeline.storage.notice_easy_text import EasyTextStorageError
from pipeline.transform.gemini_prompt import GeminiConfigurationError, load_gemini_api_key


def _report_failure(error: GeminiExecutionError, execution: ExecutionStats | None) -> int:
    print(json.dumps({
        "status": "failed",
        "execution_failure": error.to_dict(),
        "gemini_requests": execution.logical_requests if execution else 0,
        "gemini_http_attempts": execution.http_attempts if execution else 0,
    }), file=sys.stderr)
    return 2 if error.reason_code == "configuration_error" else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Return Gemini conversions; DB mode reuses the saved original and changes."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Gemini notice terms in context")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--input", type=Path, help="JSON with the exact original notice text")
    inputs.add_argument("--notice-id", type=int, help="Read, convert and store this DB notice")
    parser.add_argument("--save", action="store_true", help="Store a file input for its notice_id")
    parser.add_argument("--refresh", action="store_true", help="Explicitly request an API refresh")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, help="Write the complete result JSON")
    args = parser.parse_args(argv)
    if args.notice_id is not None and args.notice_id < 1:
        parser.error("--notice-id must be positive.")
    if args.refresh and not (args.notice_id or args.save):
        parser.error("--refresh requires DB mode.")
    # Never overwrite input while producing output, including equivalent path spellings.
    if args.input and args.output and args.input.resolve() == args.output.resolve():
        parser.error("--output must differ from the input file.")
    execution: ExecutionStats | None = None
    try:
        if args.notice_id or args.save:
            with psycopg.connect(DatabaseSettings.from_env().database_url) as conn:
                if args.input:
                    source = NoticeGlossaryInput.model_validate_json(
                        args.input.read_text("utf-8-sig")
                    )
                else:
                    with conn.transaction():
                        source = load_notice_glossary_input(conn, args.notice_id)
                with capture_execution_stats() as execution:
                    result = simplify_and_store_notice(
                        conn,
                        source,
                        refresh=args.refresh,
                        model=args.model,
                    )
                rendered = result.model_dump_json(indent=2)
                if args.output:
                    args.output.write_text(rendered, encoding="utf-8")
        else:
            source = NoticeGlossaryInput.model_validate_json(args.input.read_text("utf-8-sig"))
            with capture_execution_stats() as execution:
                result = simplify_notice(source, api_key=load_gemini_api_key(), model=args.model)
            rendered = result.model_dump_json(indent=2)
            if args.output:
                args.output.write_text(rendered, encoding="utf-8")
    except NoNoticeBodyError:
        rendered = json.dumps(
            {"notice_id": source.notice_id, "status": "skipped", "reason_code": "no_body_text"},
            ensure_ascii=False,
            indent=2,
        )
        if args.output:
            try:
                args.output.write_text(rendered, encoding="utf-8")
            except OSError:
                print("출력 파일을 기록하지 못했습니다.", file=sys.stderr)
                return 2
        print(rendered)
        return 0
    except GeminiExecutionError as error:
        return _report_failure(error, execution)
    except EasyLanguageValidationError:
        return _report_failure(GeminiExecutionError("response_validation_failed"), execution)
    except (
        ValidationError,
        ValueError,
        ConfigError,
        GeminiConfigurationError,
        EasyLanguageConfigurationError,
    ):
        print("공지 입력 또는 API·DB 설정이 올바르지 않습니다.", file=sys.stderr)
        return 2
    except (psycopg.Error, EasyTextStorageError):
        print("DB 처리에 실패했습니다. 기존 저장 결과는 보존됩니다.", file=sys.stderr)
        return 1
    except (OSError, UnicodeError):
        print("입력 또는 출력 파일을 처리하지 못했습니다.", file=sys.stderr)
        return 2
    if execution is not None:
        print(json.dumps({
            "gemini_requests": execution.logical_requests,
            "gemini_http_attempts": execution.http_attempts,
        }), file=sys.stderr)
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
