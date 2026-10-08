"""Process committed notices without coupling failure to the raw collection."""

from dataclasses import dataclass, field

import psycopg

from pipeline.config import ConfigError, DatabaseSettings
from pipeline.gemini_execution import (
    ExecutionStats,
    GeminiExecutionError,
    capture_execution_stats,
)
from pipeline.glossary.easy_language import (
    EasyLanguageConfigurationError,
    EasyLanguageValidationError,
    NoNoticeBodyError,
)
from pipeline.glossary.easy_language_service import simplify_and_store_notice
from pipeline.glossary.notice_service import load_notice_glossary_input
from pipeline.storage.notice_easy_text import EasyTextStorageError
from pipeline.transform.gemini_prompt import GeminiConfigurationError


@dataclass(slots=True)
class AfterCollectEasyText:
    """Callable post-save processor; success includes reuse of a current cache.

    Each notice uses a separate connection with short DB transactions; Gemini
    runs between them. Reports contain IDs, counters and safe retry metadata,
    never source text or exception details.
    """

    database: DatabaseSettings
    attempted_count: int = field(default=0, init=False)
    successful_count: int = field(default=0, init=False)
    gemini_requests: int = field(default=0, init=False)
    gemini_http_attempts: int = field(default=0, init=False)
    _skipped: list[dict[str, int | str]] = field(default_factory=list, init=False)
    _failures: list[dict[str, object]] = field(default_factory=list, init=False)

    @property
    def complete(self) -> bool:
        return not self._failures

    def __call__(self, notice_id: int) -> None:
        """Run only after the collector has committed the original notice."""
        self.attempted_count += 1
        execution: ExecutionStats | None = None
        failure_details: dict[str, object] = {}
        try:
            with psycopg.connect(
                self.database.database_url,
                connect_timeout=5,
                autocommit=False,
            ) as conn:
                with conn.transaction():
                    source = load_notice_glossary_input(conn, notice_id)
                skipped = not source.body_text_present
                if not skipped:
                    with capture_execution_stats() as execution:
                        simplify_and_store_notice(conn, source, refresh=False)
        except NoNoticeBodyError:
            # The source can become bodyless between the loader and service check.
            self._skip(notice_id)
            return
        except GeminiExecutionError as error:
            reason = (
                "configuration_error"
                if error.reason_code == "configuration_error"
                else "api_failed"
            )
            failure_details["execution_failure"] = error.to_dict()
        except EasyLanguageValidationError:
            reason = "invalid_response"
            failure_details["execution_failure"] = GeminiExecutionError(
                "response_validation_failed"
            ).to_dict()
        except (psycopg.Error, EasyTextStorageError):
            reason = "db_processing_failed"
        except (
            ConfigError,
            GeminiConfigurationError,
            EasyLanguageConfigurationError,
            OSError,
            UnicodeError,
        ):
            reason = "configuration_error"
        except ValueError:
            reason = "invalid_notice"
        except Exception:
            # The raw notice is already committed. Unexpected downstream failures
            # must remain bounded and allow collection of later notices to continue.
            reason = "processing_failed"
        else:
            if skipped:
                self._skip(notice_id)
            else:
                self.successful_count += 1
            return
        finally:
            if execution is not None:
                self.gemini_requests += execution.logical_requests
                self.gemini_http_attempts += execution.http_attempts
        self._failures.append({
            "notice_id": notice_id,
            "reason_code": reason,
            **failure_details,
        })

    def _skip(self, notice_id: int) -> None:
        self._skipped.append({"notice_id": notice_id, "reason_code": "no_body_text"})

    def report(self) -> dict[str, object]:
        """Return separate processing outcomes, including IDs for later retries."""
        return {
            "status": "complete" if self.complete else "failed",
            "complete": self.complete,
            "attempted_count": self.attempted_count,
            "successful_count": self.successful_count,
            "gemini_requests": self.gemini_requests,
            "gemini_http_attempts": self.gemini_http_attempts,
            "skipped_count": len(self._skipped),
            "failed_count": len(self._failures),
            "skipped": list(self._skipped),
            "failures": list(self._failures),
        }
