"""Collect committed IDs now; run postprocessing after collection has finished.

This invocation-local buffer is not a durable queue. The #61 runner owns source
versions, claims, cache decisions, retry state and recovery after interruption.
Its adapter must also scan due work when this collection saved no new notices.
"""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Literal

import psycopg
from psycopg.rows import dict_row

from pipeline.after_collect import AfterCollectEasyText
from pipeline.config import ConfigError, DatabaseSettings
from pipeline.gemini_execution import GeminiExecutionError, execution_budget
from pipeline.glossary.notice_dictionary_service import enrich_notice_dictionary
from pipeline.processing_runner import run_processing
from pipeline.storage.notice_dictionary import dictionary_work
from pipeline.storage.processing_jobs import readiness_counts
from pipeline.transform.gemini_prompt import GeminiConfigurationError, load_gemini_api_key

Feature = Literal["summary", "easy_text"]
FeatureReports = dict[Feature, dict[str, object]]
BatchRunner = Callable[[tuple[int, ...]], FeatureReports]


@dataclass(slots=True)
class CollectionPostprocessing:
    """Collector callback and explicit, idempotent post-collection boundary.

    The runner must return per-feature reports derived from committed results.
    A failed feature must not prevent the other feature from running. Reporting
    success requires an explicit boolean ``complete`` for every selected feature.
    Calling the callback itself performs no API requests or database operations.
    """

    features: tuple[Feature, ...]
    runner: BatchRunner = field(repr=False)
    _notice_ids: dict[int, None] = field(default_factory=dict, init=False, repr=False)
    _finished: bool = field(default=False, init=False)
    _reports: FeatureReports = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            not self.features
            or len(set(self.features)) != len(self.features)
            or any(feature not in ("summary", "easy_text") for feature in self.features)
        ):
            raise ValueError("invalid_processing_features")

    def __call__(self, notice_id: int) -> None:
        if self._finished:
            raise RuntimeError("collection_processing_already_finished")
        if type(notice_id) is not int or not 0 < notice_id <= 2**63 - 1:
            raise ValueError("invalid_notice_id")
        self._notice_ids[notice_id] = None

    def finish(self) -> None:
        """Run once, including empty collections, after collector connections close."""
        if self._finished:
            return
        self._finished = True
        try:
            reports = self.runner(tuple(self._notice_ids))
            if set(reports) != set(self.features) or any(
                type(report.get("complete")) is not bool for report in reports.values()
            ):
                raise ValueError("invalid_processing_report")
            self._reports = deepcopy(reports)
        except Exception:
            # Raw notices are already committed. Do not leak arbitrary runner errors
            # or claim AI success when the adapter did not return a complete report.
            self._reports = {
                feature: {
                    "status": "failed",
                    "complete": False,
                    "reason_code": "postprocessing_failed",
                }
                for feature in self.features
            }

    @property
    def complete(self) -> bool:
        return self._finished and bool(self._reports) and all(
            report["complete"] is True for report in self._reports.values()
        )

    def report(self) -> FeatureReports:
        if not self._finished:
            raise RuntimeError("collection_processing_not_finished")
        return deepcopy(self._reports)


def create_easy_text_processing(database: DatabaseSettings) -> CollectionPostprocessing:
    """Retain the existing easy-text report while moving API work after collection."""
    def run(notice_ids: tuple[int, ...]) -> FeatureReports:
        processor = AfterCollectEasyText(database)
        for notice_id in notice_ids:
            processor(notice_id)
        return {"easy_text": processor.report()}

    return CollectionPostprocessing(("easy_text",), run)


def create_ai_processing(
    database: DatabaseSettings, *, source: str, limit: int = 100,
    features: tuple[Feature, ...] = ("summary", "easy_text"),
) -> CollectionPostprocessing:
    """Validate before collecting, then give each feature an independent batch."""
    categories = {"nowon": "nowon", "wolgye1": "dong", "seoul": "seoul"}
    if source not in categories or type(limit) is not int or not 1 <= limit <= 10000:
        raise ConfigError("후처리 출처 또는 작업 상한이 올바르지 않습니다.")
    if features not in (("summary", "easy_text"), ("easy_text",)):
        raise ConfigError("지원하지 않는 후처리 기능입니다.")
    try:
        api_key = load_gemini_api_key()
        with execution_budget():
            pass
    except (GeminiConfigurationError, GeminiExecutionError):
        raise ConfigError("자동 후처리의 GEMINI_API_KEY와 실행 시간 설정을 확인하세요.") from None

    def run(notice_ids: tuple[int, ...]) -> FeatureReports:
        reports: FeatureReports = {}
        published_ids = set(notice_ids)
        for feature in features:
            report: dict[str, object] = {"complete": False}
            reports[feature] = report
            try:
                result = run_processing(
                    database, api_key=api_key, features=(feature,),
                    source=categories[source], limit=limit,
                )
                report.update(result.report())
                published_ids.update(r["notice_id"] for r in result.records)
                with _report_connection(database) as conn:
                    counts = readiness_counts(conn, feature=feature, source=categories[source])
                report["readiness"] = counts
                report["complete"] = report["complete"] and not any(
                    counts[state] for state in
                    ("pending", "running", "retry_wait", "blocked", "exhausted")
                )
            except Exception:
                # Preserve committed execution counts/records even if a later
                # readiness query fails. Reporting failure is not execution failure.
                report.update(complete=False, error_code="postprocessing_failed")
            if feature == "easy_text":
                report["dictionary"] = []
                try:
                    with _report_connection(database) as conn:
                        ids, _ = dictionary_work(conn, source=categories[source], limit=limit)
                    published_ids.update(ids)
                    for notice_id in ids:
                        try:
                            dictionary = enrich_notice_dictionary(database, notice_id)
                            status = dictionary["dictionary_status"] if dictionary else "pending"
                        except Exception:
                            status = "partial"
                        report["dictionary"].append({"notice_id": notice_id, "status": status})
                        if status != "complete":
                            report["complete"] = False
                    with _report_connection(database) as conn:
                        _, remaining = dictionary_work(conn, source=categories[source], limit=limit)
                    report["dictionary_remaining_count"] = remaining
                    if remaining:
                        report["complete"] = False
                except Exception:
                    report.update(
                        complete=False, dictionary_error_code="dictionary_processing_failed",
                    )
        try:
            published = read_published(database, sorted(published_ids))
        except Exception:
            for report in reports.values():
                report.update(
                    complete=False, published=None, published_error_code="published_read_failed",
                )
        else:
            for report in reports.values():
                report["published"] = published
        return reports

    return CollectionPostprocessing(features, run)


def _report_connection(database: DatabaseSettings) -> psycopg.Connection:
    return psycopg.connect(
        database.database_url, connect_timeout=5,
        options="-c statement_timeout=10000 -c lock_timeout=5000",
    )


def read_published(database: DatabaseSettings, notice_ids: list[int]) -> list[dict[str, object]]:
    """Return only app-visible readiness, never private queue or model metadata."""
    with psycopg.connect(
        database.database_url, connect_timeout=5,
        options="-c statement_timeout=10000 -c lock_timeout=5000",
    ) as conn, conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute("set local role anon")
        rows = cursor.execute(
            "select id, display_status, has_easy_text, url "
            "from public.app_notice_detail where id = any(%s::bigint[]) order by id",
            (notice_ids,),
        ).fetchall()
    return [dict(row) for row in rows]
