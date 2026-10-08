"""Helpers shared from test_summary_execution_storage.py."""

from datetime import UTC, date, datetime
from uuid import uuid4

import psycopg

from pipeline.storage.summary_metadata import compute_source_hash
from pipeline.storage.summary_record import SummaryMetadata, SummaryRecord
from pipeline.transform.summary_schema import CardSummaries, DateEntry, Evidence, NoticeSummary

__all__ = [
    "NEW_BODY",
    "OLD_BODY",
    "_completed",
    "_metadata",
    "_notice",
    "_row",
    "_summary",
]


OLD_BODY = "지원 사업 신청. 신청 마감: 2026-10-20."


NEW_BODY = "지원 사업 신청. 신청 마감: 2026-11-30."


def _notice(conn: psycopg.Connection) -> int:
    return conn.execute(
        "insert into notices(category,source_board,post_sn,title,registered_on,url,body_html) "
        "values ('nowon','1001',%s,'지원 사업 신청',current_date,"
        "'https://www.nowon.kr/audit',%s) returning id",
        ("execution-audit-" + uuid4().hex, OLD_BODY),
    ).fetchone()["id"]


def _metadata(body: str, *, generation: str = "v4") -> SummaryMetadata:
    return SummaryMetadata(
        source_hash=compute_source_hash(body),
        model="gemini-audit-" + generation,
        prompt_version="notice-summary-" + generation,
        attachment_status="none",
    )


def _summary(deadline: str) -> NoticeSummary:
    return NoticeSummary(
        category="application",
        category_code=27,
        summary="지원 사업 신청",
        publisher=None,
        applicable_area=None,
        audience=None,
        audience_scope="unknown",
        action=None,
        action_requirement="unknown",
        location=None,
        dates=[
            DateEntry(
                kind="application", label=None, text=None, start_date=None,
                end_date=deadline, start_time=None, end_time=None,
            )
        ],
        status="open",
        status_detail=None,
        notice_update="new",
        changed_details=None,
        notes=[],
        topics=[],
        uncertainties=[],
        evidence=[
            Evidence(field="summary", excerpt="지원 사업 신청", verification="text_matched"),
            Evidence(field="category_code", excerpt="지원 사업 신청", verification="text_matched"),
            Evidence(
                field="dates", excerpt="신청 마감: " + deadline, verification="text_matched"
            ),
        ],
        card_summaries=CardSummaries(
            audience=None, deadline=deadline + "까지 신청", action=None, notes=None
        ),
    )


def _completed(
    notice_id: int,
    body: str,
    deadline: str,
    *,
    generation: str = "v4",
    execution_token: int | None = None,
) -> SummaryRecord:
    return SummaryRecord(
        notice_id=notice_id,
        status="summarized",
        metadata=_metadata(body, generation=generation),
        result=_summary(deadline),
        deadline_on=date.fromisoformat(deadline),
        generated_at=datetime.now(UTC),
        execution_token=execution_token,
    )


def _row(conn: psycopg.Connection, notice_id: int) -> dict:
    return conn.execute(
        "select * from notice_summaries where notice_id=%s", (notice_id,)
    ).fetchone()
