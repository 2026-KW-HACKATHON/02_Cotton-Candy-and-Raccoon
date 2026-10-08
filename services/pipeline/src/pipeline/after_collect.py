"""Process committed notices without coupling failure to the raw collection."""

from dataclasses import dataclass, field

import psycopg

from pipeline.config import ConfigError, DatabaseSettings
from pipeline.glossary.easy_language import (
    EasyLanguageAPIError,
    EasyLanguageConfigurationError,
    EasyLanguageValidationError,
    NoNoticeBodyError,
)
from pipeline.glossary.easy_language_service import simplify_and_store_notice
from pipeline.glossary.notice_dictionary_service import enrich_notice_dictionary
from pipeline.glossary.notice_service import load_notice_glossary_input
from pipeline.storage.notice_easy_text import EasyTextStorageError
from pipeline.transform.gemini_prompt import GeminiConfigurationError


@dataclass(slots=True)
class AfterCollectEasyText:
    """Callable post-save processor; success includes reuse of a current cache.

    Each notice uses a separate connection with short DB transactions; Gemini
    runs between them. Public reports contain only notice IDs and fixed reason
    codes, never source text or exception details.
    """

    database: DatabaseSettings
    attempted_count: int = field(default=0, init=False)
    successful_count: int = field(default=0, init=False)
    _skipped: list[dict[str, int | str]] = field(default_factory=list, init=False)
    _failures: list[dict[str, int | str]] = field(default_factory=list, init=False)
    _dictionary_statuses: list[str] = field(default_factory=list, init=False)
    _dictionary_failures: list[dict[str, int | str]] = field(default_factory=list, init=False)

    @property
    def complete(self) -> bool:
        return not self._failures and not self._dictionary_failures

    def __call__(self, notice_id: int) -> None:
        """Run only after the collector has committed the original notice."""
        self.attempted_count += 1
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
                    simplify_and_store_notice(conn, source, refresh=False)
        except NoNoticeBodyError:
            # The source can become bodyless between the loader and service check.
            self._skip(notice_id)
            return
        except EasyLanguageAPIError:
            reason = "api_failed"
        except EasyLanguageValidationError:
            reason = "invalid_response"
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
                self._dictionary(notice_id)
            return
        self._failures.append({"notice_id": notice_id, "reason_code": reason})

    def _dictionary(self, notice_id: int) -> None:
        """Run after easy-text commit; a lookup failure cannot roll that work back."""
        try:
            result = enrich_notice_dictionary(self.database, notice_id)
            status = str(result["dictionary_status"]) if result is not None else "pending"
        except Exception:
            # Keep the collected notice and successful conversion, and continue
            # with later notices. Only a fixed code is included in public logs.
            status = "partial"
            reason = "dictionary_processing_failed"
        else:
            reason = "dictionary_incomplete"
        self._dictionary_statuses.append(status)
        if status != "complete":
            self._dictionary_failures.append({"notice_id": notice_id, "reason_code": reason})

    def _skip(self, notice_id: int) -> None:
        self._skipped.append({"notice_id": notice_id, "reason_code": "no_body_text"})

    def report(self) -> dict[str, object]:
        """Return separate processing outcomes, including IDs for later retries."""
        return {
            "status": "complete" if self.complete else "failed",
            "complete": self.complete,
            "attempted_count": self.attempted_count,
            "successful_count": self.successful_count,
            "skipped_count": len(self._skipped),
            "failed_count": len(self._failures),
            "skipped": list(self._skipped),
            "failures": list(self._failures),
            "dictionary": {
                "attempted_count": len(self._dictionary_statuses),
                "complete_count": self._dictionary_statuses.count("complete"),
                "partial_count": self._dictionary_statuses.count("partial"),
                "pending_count": sum(
                    status in {"pending", "unprocessed"} for status in self._dictionary_statuses
                ),
                "failures": list(self._dictionary_failures),
            },
        }
