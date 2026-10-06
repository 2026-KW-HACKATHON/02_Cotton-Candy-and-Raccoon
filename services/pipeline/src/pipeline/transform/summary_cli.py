"""Run the Gemini summarizer for one JSON input file."""

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from pipeline.transform.gemini_client import DEFAULT_MODEL, GeminiRequestError
from pipeline.transform.gemini_input import GeminiInputError
from pipeline.transform.gemini_prompt import GeminiConfigurationError
from pipeline.transform.notice_input import NoticeInput
from pipeline.transform.summarize import summarize_notice
from pipeline.transform.summary_schema import SummaryValidationError


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Summarize one notice JSON file with Gemini.")
    parser.add_argument("input_file", type=Path, help="JSON matching the NoticeInput contract")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Gemini model ID")
    args = parser.parse_args()

    try:
        notice = NoticeInput.model_validate_json(args.input_file.read_text(encoding="utf-8-sig"))
        result = summarize_notice(notice, model=args.model)
    except ValidationError as exc:
        fields = sorted({".".join(map(str, error["loc"])) or "root" for error in exc.errors()})
        print(f"Invalid notice input at: {', '.join(fields)}", file=sys.stderr)
        return 2
    except (
        GeminiConfigurationError,
        GeminiRequestError,
        GeminiInputError,
        SummaryValidationError,
    ) as exc:
        print(f"Summary failed: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Cannot read input file: {exc.strerror}", file=sys.stderr)
        return 2

    print(result.model_dump_json(indent=2))
    return 0
