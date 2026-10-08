"""Private subprocess entrypoint: stdin credentials, stdout safe protocol, no DB."""

from __future__ import annotations

import json
import sys
from contextlib import redirect_stdout

from pipeline import gemini_execution
from pipeline.gemini_execution import GeminiExecutionError


def main() -> int:
    output = sys.stdout
    attempts = 0
    emitted_bytes = 0

    def emit(event: dict[str, object]) -> None:
        nonlocal emitted_bytes
        frame = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        size = len(frame.encode("utf-8"))
        if emitted_bytes + size > gemini_execution.MAX_WORKER_OUTPUT_BYTES:
            raise GeminiExecutionError("response_incomplete")
        output.write(frame)
        output.flush()
        emitted_bytes += size

    def dispatch() -> None:
        nonlocal attempts
        if attempts >= 1:
            raise GeminiExecutionError("configuration_error")
        attempts += 1
        emit({"event": "dispatch", "attempt": attempts})

    gemini_execution._dispatch_sink = dispatch
    try:
        request = json.loads(sys.stdin.buffer.read())
        if not isinstance(request, dict) or not isinstance(request.get("payload"), dict):
            raise GeminiExecutionError("configuration_error")
        with redirect_stdout(sys.stderr):
            if request.get("operation") == "summary":
                from pipeline.transform.gemini_client import _generate_summary_json_direct

                value = _generate_summary_json_direct(**request["payload"])
            elif request.get("operation") == "easy_language":
                from pipeline.glossary.easy_language_client import (
                    _generate_easy_language_json_direct,
                )

                value = _generate_easy_language_json_direct(**request["payload"])
            else:
                raise GeminiExecutionError("configuration_error")
        if not isinstance(value, str):
            raise GeminiExecutionError("empty_response")
        if len(value) > gemini_execution.MAX_WORKER_OUTPUT_BYTES:
            raise GeminiExecutionError("response_incomplete")
        emit({"event": "result", "ok": True, "value": value})
    except GeminiExecutionError as error:
        emit({"event": "result", "ok": False, "error": error.to_dict()})
    except Exception:
        emit({"event": "result", "ok": False,
              "error": GeminiExecutionError("api_error").to_dict()})
    finally:
        gemini_execution._dispatch_sink = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
