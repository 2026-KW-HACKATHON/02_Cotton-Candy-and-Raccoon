"""Convert notice terms with Gemini or read cached Ourmalsam definitions."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg
from pydantic import ValidationError

from pipeline.config import ConfigError, DatabaseSettings
from pipeline.glossary.client import GlossaryAPIError
from pipeline.glossary.config import GlossaryConfigurationError
from pipeline.glossary.dictionary_service import lookup_dictionary_definition
from pipeline.glossary.easy_language import (
    DEFAULT_MODEL,
    EasyLanguageAPIError,
    EasyLanguageConfigurationError,
    EasyLanguageValidationError,
    simplify_notice,
)
from pipeline.glossary.easy_language_service import simplify_and_store_notice
from pipeline.glossary.notice_service import load_notice_glossary_input
from pipeline.glossary.source import NoticeGlossaryInput
from pipeline.storage.glossary import GlossaryStorageError
from pipeline.storage.notice_easy_text import EasyTextStorageError
from pipeline.transform.gemini_prompt import GeminiConfigurationError, load_gemini_api_key


def main(argv: Sequence[str] | None = None) -> int:
    """DB modes always reuse saved results; dictionaries never run for a notice."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Gemini term swaps + cached Ourmalsam definitions")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--input", type=Path, help="JSON with the exact original notice text")
    inputs.add_argument("--notice-id", type=int, help="Read, convert and store this DB notice")
    inputs.add_argument("--word", help="Read one word's meanings; always use the DB cache")
    parser.add_argument("--save", action="store_true", help="Store a file input for its notice_id")
    parser.add_argument("--refresh", action="store_true", help="Explicitly request an API refresh")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, help="Write the complete result JSON")
    args = parser.parse_args(argv)
    if args.word is not None and not args.word.strip():
        parser.error("--word must not be blank.")
    if args.notice_id is not None and args.notice_id < 1:
        parser.error("--notice-id must be positive.")
    if args.refresh and not (args.notice_id or args.word or args.save):
        parser.error("--refresh requires DB mode.")
    if args.save and args.word:
        parser.error("--word already uses the DB cache; omit --save.")
    # Never overwrite input while producing output, including equivalent path spellings.
    if args.input and args.output and args.input.resolve() == args.output.resolve():
        parser.error("--output must differ from the input file.")
    try:
        if args.word or args.notice_id or args.save:
            with psycopg.connect(DatabaseSettings.from_env().database_url) as conn:
                if args.word:
                    result = lookup_dictionary_definition(conn, args.word, refresh=args.refresh)
                else:
                    source = (
                        NoticeGlossaryInput.model_validate_json(args.input.read_text("utf-8-sig"))
                        if args.input
                        else load_notice_glossary_input(conn, args.notice_id)
                    )
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
            result = simplify_notice(source, api_key=load_gemini_api_key(), model=args.model)
            rendered = result.model_dump_json(indent=2)
            if args.output:
                args.output.write_text(rendered, encoding="utf-8")
    except (EasyLanguageAPIError, EasyLanguageValidationError, GlossaryAPIError):
        print("API 응답 처리에 실패했습니다. 성공 결과를 만들지 않았습니다.", file=sys.stderr)
        return 1
    except (
        ValidationError,
        ValueError,
        ConfigError,
        GeminiConfigurationError,
        GlossaryConfigurationError,
        EasyLanguageConfigurationError,
    ):
        print("공지 입력 또는 API·DB 설정이 올바르지 않습니다.", file=sys.stderr)
        return 2
    except (psycopg.Error, GlossaryStorageError, EasyTextStorageError):
        print("DB 처리에 실패했습니다. 기존 저장 결과는 보존됩니다.", file=sys.stderr)
        return 1
    except (OSError, UnicodeError):
        print("입력 또는 출력 파일을 처리하지 못했습니다.", file=sys.stderr)
        return 2
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
