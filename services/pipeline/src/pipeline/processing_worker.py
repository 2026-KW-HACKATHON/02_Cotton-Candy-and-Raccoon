"""One supervised processing claim. Credentials enter over stdin; output is safe JSON only."""

import contextlib
import json
import os
import sys
from datetime import datetime
from uuid import UUID

import psycopg

from pipeline.config import DatabaseSettings
from pipeline.glossary.easy_language import (
    EasyLanguageAPIError,
    EasyLanguageConfigurationError,
    EasyLanguageValidationError,
    NoNoticeBodyError,
)
from pipeline.glossary.easy_language_service import simplify_and_store_notice
from pipeline.glossary.notice_service import load_notice_glossary_input
from pipeline.processing_runner import ProcessingOutcome, failure_outcome
from pipeline.storage.notice_easy_text import EasyTextStorageError
from pipeline.storage.processing_context import processing_claim
from pipeline.storage.processing_jobs import Claim, ProcessingClaimSuperseded, assert_active_claim
from pipeline.storage.summaries import load_stored_summary
from pipeline.summary_run import summarize_one


def process_claim(
    database: DatabaseSettings, claim: Claim, *, api_key: str,
) -> ProcessingOutcome:
    """Reuse the existing services; a claim fence protects every published result write."""
    try:
        model = json.loads(claim.contract_key)["model"]
        with processing_claim(claim):
            with psycopg.connect(database.database_url, connect_timeout=5) as conn:
                with conn.transaction():
                    assert_active_claim(conn, claim)
            if claim.feature == "summary":
                result = summarize_one(database, claim.notice_id, api_key=api_key, model=model)
                if result.execution_status == "superseded":
                    return ProcessingOutcome("superseded", "processing_claim_superseded")
                if result.execution_status == "not_found":
                    return ProcessingOutcome("superseded", "notice_not_found_or_hidden")
                if result.execution_status not in {"summarized", "needs_review"}:
                    if result.reason_code == "summary_source_has_no_content":
                        return ProcessingOutcome("skipped", "summary_source_has_no_content")
                    return failure_outcome(result)
                # A storage refusal may preserve an old good result while the public
                # report still says summarized. Do not mislabel that attempt as success.
                with psycopg.connect(database.database_url, connect_timeout=5) as conn:
                    with conn.transaction():
                        assert_active_claim(conn, claim)
                        stored = load_stored_summary(conn, claim.notice_id)
                if stored is not None and stored.last_error_code == "summary_information_loss":
                    return ProcessingOutcome("failed", "summary_information_loss")
                return ProcessingOutcome("succeeded")
            with psycopg.connect(database.database_url, connect_timeout=5) as conn:
                with conn.transaction():
                    source = load_notice_glossary_input(conn, claim.notice_id)
                if not source.body_text_present:
                    return ProcessingOutcome("skipped", "no_body_text")
                simplify_and_store_notice(conn, source, refresh=False, api_key=api_key, model=model)
            return ProcessingOutcome("succeeded")
    except ProcessingClaimSuperseded:
        return ProcessingOutcome("superseded", "processing_claim_superseded")
    except NoNoticeBodyError:
        return ProcessingOutcome("skipped", "no_body_text")
    except EasyLanguageAPIError as error:
        return failure_outcome(error, default_reason="api_error")
    except EasyLanguageValidationError:
        return ProcessingOutcome("failed", "invalid_response")
    except EasyLanguageConfigurationError:
        return ProcessingOutcome("failed", "configuration_error")
    except EasyTextStorageError:
        return ProcessingOutcome("failed", "easy_text_storage_failed")
    except psycopg.Error:
        return ProcessingOutcome("failed", "db_unavailable")
    except Exception:
        return ProcessingOutcome("failed", "processing_failed")


def main() -> int:
    """An internal executable, deliberately without environment or command-line credentials."""
    try:
        payload = json.loads(sys.stdin.buffer.read(65537))
        claim_data = payload["claim"]
        claim_data["claim_token"] = UUID(claim_data["claim_token"])
        claim_data["lease_expires_at"] = datetime.fromisoformat(claim_data["lease_expires_at"])
        claim = Claim(**claim_data)
        database = DatabaseSettings(payload["database_url"])
        # Third-party diagnostics never enter the parent protocol or leak raw inputs.
        with open(os.devnull, "w", encoding="utf-8") as sink, \
                contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            outcome = process_claim(database, claim, api_key=payload["api_key"])
    except Exception:
        outcome = ProcessingOutcome("failed", "worker_failed")
    sys.stdout.write(json.dumps(outcome.report(), ensure_ascii=True))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
