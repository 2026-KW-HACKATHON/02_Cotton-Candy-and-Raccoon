"""Post-commit easy-text processing is isolated, bounded and safe to retry."""

import json
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from pipeline.after_collect import AfterCollectEasyText
from pipeline.config import DatabaseSettings
from pipeline.glossary.easy_language import (
    EasyLanguageAPIError,
    EasyLanguageConfigurationError,
    EasyLanguageValidationError,
    NoNoticeBodyError,
)
from pipeline.glossary.source import StoredNoticeInput
from pipeline.storage.notice_easy_text import EasyTextStorageError
from pipeline.transform.gemini_prompt import GeminiConfigurationError


def _source(notice_id: int = 42, *, body_present: bool = True) -> StoredNoticeInput:
    return StoredNoticeInput(
        notice_id=notice_id,
        notice_revision="a" * 64,
        title="제목",
        text="제목\n익일 방문하세요." if body_present else "제목",
        body_text_present=body_present,
    )


@pytest.fixture
def processor() -> AfterCollectEasyText:
    return AfterCollectEasyText(DatabaseSettings("postgresql://user:secret@localhost/test"))


@pytest.fixture
def conn() -> MagicMock:
    connection = MagicMock()
    connection.__enter__.return_value = connection
    return connection


def test_separate_transaction_reuses_current_cache_without_forced_refresh(
    processor: AfterCollectEasyText,
    conn: MagicMock,
) -> None:
    source = _source()
    with (
        patch("pipeline.after_collect.psycopg.connect", return_value=conn) as connect,
        patch("pipeline.after_collect.load_notice_glossary_input", return_value=source) as load,
        patch("pipeline.after_collect.simplify_and_store_notice") as simplify,
    ):
        processor(42)
    connect.assert_called_once_with(
        processor.database.database_url,
        connect_timeout=5,
        autocommit=False,
    )
    load.assert_called_once_with(conn, 42)
    simplify.assert_called_once_with(conn, source, refresh=False)
    conn.__exit__.assert_called_once_with(None, None, None)
    assert processor.report() == {
        "status": "complete",
        "complete": True,
        "attempted_count": 1,
        "successful_count": 1,
        "skipped_count": 0,
        "failed_count": 0,
        "skipped": [],
        "failures": [],
    }


@pytest.mark.parametrize("raced", [False, True])
def test_bodyless_notice_is_skipped_without_api_work(
    processor: AfterCollectEasyText,
    conn: MagicMock,
    raced: bool,
) -> None:
    with (
        patch("pipeline.after_collect.psycopg.connect", return_value=conn),
        patch(
            "pipeline.after_collect.load_notice_glossary_input",
            return_value=_source(body_present=raced),
        ),
        patch(
            "pipeline.after_collect.simplify_and_store_notice",
            side_effect=NoNoticeBodyError("no body"),
        ) as simplify,
    ):
        processor(42)
    assert simplify.call_count == int(raced)
    report = processor.report()
    assert report["complete"] is True
    assert report["successful_count"] == report["failed_count"] == 0
    assert report["skipped_count"] == 1
    assert report["skipped"] == [{"notice_id": 42, "reason_code": "no_body_text"}]


@pytest.mark.parametrize(
    "error,reason",
    [
        (EasyLanguageAPIError("private body/key"), "api_failed"),
        (EasyLanguageValidationError("private body/key"), "invalid_response"),
        (EasyLanguageConfigurationError("private body/key"), "configuration_error"),
        (GeminiConfigurationError("private body/key"), "configuration_error"),
        (OSError("private body/key"), "configuration_error"),
        (EasyTextStorageError("private body/key"), "db_processing_failed"),
        (psycopg.OperationalError("private body/key"), "db_processing_failed"),
        (ValueError("private body/key"), "invalid_notice"),
        (RuntimeError("private body/key"), "processing_failed"),
    ],
)
def test_processing_failure_is_fixed_and_later_notice_still_runs(
    processor: AfterCollectEasyText,
    conn: MagicMock,
    error: Exception,
    reason: str,
) -> None:
    with (
        patch("pipeline.after_collect.psycopg.connect", return_value=conn),
        patch("pipeline.after_collect.load_notice_glossary_input", return_value=_source()),
        patch("pipeline.after_collect.simplify_and_store_notice", side_effect=[error, None]),
    ):
        processor(42)
        processor(43)
    report = processor.report()
    assert report["complete"] is False
    assert report["attempted_count"] == 2
    assert report["successful_count"] == report["failed_count"] == 1
    assert report["failures"] == [{"notice_id": 42, "reason_code": reason}]
    assert "private" not in json.dumps(report)
    assert "secret" not in json.dumps(report)


def test_connection_failure_is_reported_for_saved_notice(processor: AfterCollectEasyText) -> None:
    with patch("pipeline.after_collect.psycopg.connect", side_effect=psycopg.OperationalError):
        processor(42)
    assert processor.report()["failures"] == [
        {"notice_id": 42, "reason_code": "db_processing_failed"},
    ]


@pytest.mark.parametrize("body_present", [False, True])
def test_commit_failure_does_not_claim_success_or_skip(
    processor: AfterCollectEasyText,
    conn: MagicMock,
    body_present: bool,
) -> None:
    conn.__exit__.side_effect = psycopg.OperationalError("commit failed")
    with (
        patch("pipeline.after_collect.psycopg.connect", return_value=conn),
        patch(
            "pipeline.after_collect.load_notice_glossary_input",
            return_value=_source(body_present=body_present),
        ),
        patch("pipeline.after_collect.simplify_and_store_notice"),
    ):
        processor(42)
    assert processor.report()["successful_count"] == 0
    assert processor.report()["skipped_count"] == 0
    assert processor.report()["failed_count"] == 1
