"""Collect committed IDs now; run postprocessing after collection has finished.

This invocation-local buffer is not a durable queue. The #61 runner owns source
versions, claims, cache decisions, retry state and recovery after interruption.
Its adapter must also scan due work when this collection saved no new notices.
"""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Literal

from pipeline.after_collect import AfterCollectEasyText
from pipeline.config import ConfigError, DatabaseSettings

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
    database: DatabaseSettings, *, source: str,
) -> CollectionPostprocessing:
    """Integration point for the #61 runner using #60's bounded AI calls.

    Once available, the adapter must own two independent features, recover due
    work for ``source`` even with no new IDs, and return committed result reports.
    Until then, reject the combined mode before collecting or calling any APIs.
    """
    raise ConfigError(
        "--process-ai는 공통 실행기(#61)와 전체 시간 제한(#60) 연결 후 사용할 수 있습니다."
    )
