"""Helpers shared from test_threepass_database_audit.py."""

import os
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.rows import dict_row

from support.summary_execution_storage import (
    _notice as _base_notice,
)

__all__ = [
    "_notice",
    "live_db",
]


_OWNED_NOTICES: dict[int, list[int]] = {}


def _notice(conn: psycopg.Connection) -> int:
    notice_id = _base_notice(conn)
    _OWNED_NOTICES[id(conn)].append(notice_id)
    return notice_id


@pytest.fixture
def live_db() -> Iterator[psycopg.Connection]:
    dsn = os.environ.get("AUDIT_TEST_DATABASE_URL") or os.environ.get("PIPELINE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("A disposable AUDIT_TEST_DATABASE_URL is required")
    conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True, connect_timeout=5)
    if conn.info.host != "127.0.0.1" or not conn.info.dbname.startswith("pipeline_schema_test_"):
        conn.close()
        pytest.fail("Use a disposable loopback pipeline_schema_test_* database")
    owned = _OWNED_NOTICES[id(conn)] = []
    try:
        yield conn
    finally:
        conn.execute("reset role")
        conn.execute("delete from notices where id=any(%s)", (owned,))
        _OWNED_NOTICES.pop(id(conn))
        conn.close()
