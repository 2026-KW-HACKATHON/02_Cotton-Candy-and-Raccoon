"""Helpers shared from test_guarded_failure_preservation.py."""

import os
from collections.abc import Iterator
from dataclasses import replace

import psycopg
import pytest
from psycopg.rows import dict_row

from pipeline.storage.summaries import begin_summary_execution, save_notice_summary
from pipeline.storage.summary_metadata import SummaryAttachmentText, build_summary_metadata
from pipeline.transform.gemini_prompt import SUMMARY_PROMPT_VERSION
from support.summary_execution_storage import OLD_BODY, _completed, _notice

__all__ = [
    "_metadata",
    "_publish",
    "preserved_notice",
]


@pytest.fixture
def preserved_notice() -> Iterator[tuple[psycopg.Connection, int]]:
    dsn = os.environ.get("AUDIT_TEST_DATABASE_URL") or os.environ.get("PIPELINE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("A disposable AUDIT_TEST_DATABASE_URL is required")
    conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True, connect_timeout=5)
    if conn.info.host != "127.0.0.1" or not conn.info.dbname.startswith("pipeline_schema_test_"):
        conn.close()
        pytest.fail("Use a disposable loopback pipeline_schema_test_* database")
    notice_id = _notice(conn)
    try:
        conn.execute(
            "insert into notice_files(notice_id,kind,file_id,file_key,file_name,url) "
            "values (%s,'attachment','hwp','id:hwp','attachment.hwp',"
            "'https://www.nowon.kr/audit.hwp')", (notice_id,),
        )
        yield conn, notice_id
    finally:
        conn.execute("reset role")
        conn.execute("delete from notices where id=%s", (notice_id,))
        conn.close()


def _metadata(*, readable: bool):
    return build_summary_metadata(
        body_text=OLD_BODY,
        attachment_texts=(SummaryAttachmentText("id:hwp", "참가비 무료"),) if readable else (),
        total_file_count=1, read_file_count=int(readable), model="gemini-preservation-test",
        prompt_version=SUMMARY_PROMPT_VERSION,
    )


def _publish(conn, notice_id):
    token = begin_summary_execution(conn, notice_id)
    save_notice_summary(conn, replace(
        _completed(notice_id, OLD_BODY, "2026-10-20", execution_token=token),
        metadata=_metadata(readable=True),
    ))
