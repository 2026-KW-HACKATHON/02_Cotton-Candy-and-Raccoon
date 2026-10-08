"""Run the Gemini summarizer for one JSON input file."""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from pipeline.gemini_execution import GeminiExecutionError, execution_budget
from pipeline.transform.gemini_client import (
    DEFAULT_MODEL,
    GeminiRequestError,
    count_summary_requests,
)
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

    active = None
    requests = [0]
    try:
        notice = NoticeInput.model_validate_json(args.input_file.read_text(encoding="utf-8-sig"))
        with execution_budget() as active, count_summary_requests() as requests:
            result = summarize_notice(notice, model=args.model)
    except ValidationError as exc:
        fields = sorted({".".join(map(str, error["loc"])) or "root" for error in exc.errors()})
        print(f"Invalid notice input at: {', '.join(fields)}", file=sys.stderr)
        return 2
    except GeminiExecutionError as exc:
        details = {
            "execution_failure": exc.to_dict(),
            "gemini_requests": requests[0],
            "gemini_http_attempts": active.http_attempts if active is not None else 0,
        }
        print(f"Summary failed: {json.dumps(details)}", file=sys.stderr)
        return 1
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
    if result._correction_failure_code is not None:
        details = {
            "execution_failure": active.last_failure.to_dict() if active.last_failure else {
                "reason_code": result._correction_failure_code,
            },
            "gemini_requests": requests[0],
            "gemini_http_attempts": active.http_attempts,
        }
        print(f"Summary failed: {json.dumps(details)}", file=sys.stderr)
        return 1
    return 0
