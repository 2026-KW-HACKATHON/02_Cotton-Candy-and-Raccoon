"""Validate #14's real table constraints, incremental migration, and app RLS."""

from datetime import UTC, datetime

import psycopg
import pytest
from psycopg.types.json import Jsonb

from pipeline.storage.summary_record import FAILURE_CODES
from pipeline.transform.summary_schema import NoticeSummary

PUBLIC_COLUMNS = (
    "notice_id",
    "status",
    "category",
    "category_code",
    "deadline_on",
    "result",
    "attachment_status",
    "generated_at",
)
PRIVATE_COLUMNS = (
    "source_hash",
    "model",
    "prompt_version",
    "attempt_count",
    "last_error_code",
    "updated_at",
)
SUBJECT_CODES = (21, 22, 23, 24, 25, 26, 27, 30)


def insert_notice(
    db: psycopg.Connection, *, post: str = "summary-test", visible: bool = True
) -> int:
    return db.execute(
        "insert into notices(category,source_board,post_sn,title,registered_on,url,is_visible) "
        "values ('nowon','1001',%s,'test',current_date,'https://www.nowon.kr/test',%s) "
        "returning id",
        (post, visible),
    ).fetchone()[0]


def summary_json(db: psycopg.Connection, *, code: int = 27) -> dict:
    result = db.execute(
        "select result from notice_summaries where status='summarized' limit 1"
    ).fetchone()[0]
    result["category_code"] = code
    return result


def insert_summary(db: psycopg.Connection, notice_id: int, **overrides: object) -> None:
    values = {
        "notice_id": notice_id,
        "status": "summarized",
        "result": summary_json(db),
        "category": "living",
        "category_code": 27,
        "deadline_on": None,
        "attachment_status": "none",
        "source_hash": "c" * 64,
        "model": "gemini-2.5-flash",
        "prompt_version": "test-v2",
        "attempt_count": 1,
        "last_error_code": None,
        "generated_at": datetime(2026, 10, 6, tzinfo=UTC),
    }
    values.update(overrides)
    if values["result"] is not None:
        values["result"] = Jsonb(values["result"])
    columns = ",".join(values)
    db.execute(
        f"insert into notice_summaries ({columns}) values ({','.join(['%s'] * len(values))})",
        tuple(values.values()),
    )


def test_seed_four_states_and_full_valid_summary(db: psycopg.Connection) -> None:
    assert set(db.execute("select distinct status from notice_summaries").fetchall()) == {
        ("pending",),
        ("summarized",),
        ("needs_review",),
        ("failed",),
    }
    assert db.execute(
        "select attachment_status,result,category,category_code,deadline_on "
        "from notice_summaries where status='needs_review'"
    ).fetchone() == ("partial", None, None, None, None)
    for (result,) in db.execute("select result from notice_summaries where result is not null"):
        assert NoticeSummary.model_validate(result).category_code == 27
    assert (
        db.execute(
            "select count(*) from notice_summaries s join notices n on n.id=s.notice_id "
            "where not n.is_visible"
        ).fetchone()[0]
        == 1
    )


def test_new_notice_content_timestamp_default_and_not_null(db: psycopg.Connection) -> None:
    key = insert_notice(db)
    assert db.execute(
        "select content_updated_at is not null,content_updated_at=created_at "
        "from notices where id=%s",
        (key,),
    ).fetchone() == (True, True)
    with pytest.raises(psycopg.errors.NotNullViolation):
        db.execute("update notices set content_updated_at=null where id=%s", (key,))


@pytest.mark.parametrize("code", SUBJECT_CODES)
def test_eight_subject_codes_stored_as_json_integers(db: psycopg.Connection, code: int) -> None:
    key = insert_notice(db)
    insert_summary(db, key, result=summary_json(db, code=code), category_code=code)
    assert db.execute(
        "select category_code,jsonb_typeof(result->'category_code'),result->>'category_code' "
        "from notice_summaries where notice_id=%s",
        (key,),
    ).fetchone() == (code, "number", str(code))


@pytest.mark.parametrize("result_code", ["27", "복지", 99, 27.0, 27.5, None, True])
def test_invalid_json_subject_code_rejected(
    db: psycopg.Connection,
    result_code: object,
) -> None:
    key = insert_notice(db)
    result = summary_json(db)
    result["category_code"] = result_code
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, result=result)


@pytest.mark.parametrize("json_category", [None, 27, True, "event", "unknown"])
def test_invalid_or_mismatched_json_category_rejected(
    db: psycopg.Connection,
    json_category: object,
) -> None:
    key = insert_notice(db)
    result = summary_json(db)
    result["category"] = json_category
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, result=result)


@pytest.mark.parametrize("value", [None, [], "text", 27, {}])
def test_non_object_or_missing_summary_fields_rejected(
    db: psycopg.Connection,
    value: object,
) -> None:
    key = insert_notice(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, result=value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"category_code": None},
        {"category_code": 99},
        {"category_code": 21},
        {"category": None},
        {"category": "event"},
        {"category": "unknown"},
        {"status": "bad"},
        {"attachment_status": "bad"},
        {"generated_at": None},
        {"source_hash": "A" * 64},
        {"source_hash": "a" * 63},
        {"source_hash": None},
        {"model": " model"},
        {"model": ""},
        {"model": None},
        {"prompt_version": "\nversion"},
        {"prompt_version": ""},
        {"attempt_count": -1},
        {"last_error_code": "raw exception / secret"},
    ],
)
def test_invalid_storage_contract_rejected(db: psycopg.Connection, overrides: dict) -> None:
    key = insert_notice(db)
    with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation)):
        insert_summary(db, key, **overrides)


@pytest.mark.parametrize("status", ["pending", "needs_review", "failed"])
@pytest.mark.parametrize("leaked_column", ["result", "category", "category_code", "deadline_on"])
def test_unpublished_states_cannot_leak_summary_fields(
    db: psycopg.Connection,
    status: str,
    leaked_column: str,
) -> None:
    key = insert_notice(db)
    values = {
        "status": status,
        "result": None,
        "category": None,
        "category_code": None,
        "deadline_on": None,
        "generated_at": datetime(2026, 10, 6, tzinfo=UTC) if status == "needs_review" else None,
        "last_error_code": "api_timeout" if status == "failed" else None,
    }
    values[leaked_column] = {
        "result": summary_json(db),
        "category": "living",
        "category_code": 27,
        "deadline_on": "2026-10-31",
    }[leaked_column]
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, **values)


def test_failed_state_requires_safe_error_and_no_generation(db: psycopg.Connection) -> None:
    key = insert_notice(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(
            db,
            key,
            status="failed",
            result=None,
            category=None,
            category_code=None,
            generated_at=None,
            last_error_code=None,
        )


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_waiting_or_failed_row_cannot_claim_generation(
    db: psycopg.Connection,
    status: str,
) -> None:
    key = insert_notice(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(
            db,
            key,
            status=status,
            result=None,
            category=None,
            category_code=None,
            last_error_code="api_timeout" if status == "failed" else None,
        )


@pytest.mark.parametrize("code", sorted(FAILURE_CODES))
def test_every_python_failure_code_allowed_on_preserved_summary(
    db: psycopg.Connection,
    code: str,
) -> None:
    key = insert_notice(db)
    insert_summary(db, key, last_error_code=code)
    assert db.execute(
        "select status,last_error_code from notice_summaries where notice_id=%s", (key,)
    ).fetchone() == ("summarized", code)


def test_changed_source_review_can_retain_generation_metadata(db: psycopg.Connection) -> None:
    key = insert_notice(db)
    insert_summary(db, key)
    before = db.execute(
        "select source_hash,model,prompt_version,generated_at "
        "from notice_summaries where notice_id=%s",
        (key,),
    ).fetchone()
    db.execute(
        "update notice_summaries set status='needs_review',result=null,category=null,"
        "category_code=null,deadline_on=null,last_error_code='api_timeout' where notice_id=%s",
        (key,),
    )
    assert (
        db.execute(
            "select source_hash,model,prompt_version,generated_at "
            "from notice_summaries where notice_id=%s",
            (key,),
        ).fetchone()
        == before
    )
    assert key in {
        row[0]
        for row in db.execute(
            "select notice_id from notice_summaries "
            "where status in ('pending','failed') or last_error_code is not null"
        )
    }
    predicate = db.execute(
        "select pg_get_expr(indpred,indrelid) from pg_index "
        "where indexrelid='notice_summaries_retry_idx'::regclass"
    ).fetchone()[0]
    assert (
        "pending" in predicate
        and "failed" in predicate
        and "last_error_code IS NOT NULL" in predicate
    )


def test_one_summary_per_notice_and_delete_cascades(db: psycopg.Connection) -> None:
    key = insert_notice(db)
    insert_summary(db, key)
    db.execute("delete from notices where id=%s", (key,))
    assert (
        db.execute("select count(*) from notice_summaries where notice_id=%s", (key,)).fetchone()[0]
        == 0
    )


def test_orphan_summary_rejected(db: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        insert_summary(db, 2**63 - 1)


def test_duplicate_notice_summary_rejected(db: psycopg.Connection) -> None:
    key = insert_notice(db)
    insert_summary(db, key)
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_summary(db, key)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_app_reads_exact_public_columns_and_visible_rows(db: psycopg.Connection, role: str) -> None:
    db.execute("set local role " + role)
    rows = db.execute(f"select {','.join(PUBLIC_COLUMNS)} from notice_summaries").fetchall()
    assert len(rows) == 4
    assert {row[1] for row in rows} == {"pending", "summarized", "needs_review", "failed"}
    assert (
        db.execute("select content_updated_at from notices order by id limit 1").fetchone()[0]
        is not None
    )


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("column", [*PRIVATE_COLUMNS, "*"])
def test_app_private_metadata_and_select_star_forbidden(
    db: psycopg.Connection,
    role: str,
    column: str,
) -> None:
    db.execute("set local role " + role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute(f"select {column} from notice_summaries")


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize(
    "statement",
    [
        "insert into notice_summaries(notice_id) values (1)",
        "update notice_summaries set status='pending'",
        "delete from notice_summaries",
        "truncate notice_summaries",
    ],
)
def test_app_writes_forbidden(db: psycopg.Connection, role: str, statement: str) -> None:
    db.execute("set local role " + role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute(statement)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_default_table_privileges_revoked(db: psycopg.Connection, role: str) -> None:
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert (
            db.execute(
                "select has_table_privilege(%s,'notice_summaries',%s)", (role, privilege)
            ).fetchone()[0]
            is False
        )
    for column in PUBLIC_COLUMNS:
        assert (
            db.execute(
                "select has_column_privilege(%s,'notice_summaries',%s,'SELECT')", (role, column)
            ).fetchone()[0]
            is True
        )
    for column in PRIVATE_COLUMNS:
        assert (
            db.execute(
                "select has_column_privilege(%s,'notice_summaries',%s,'SELECT')", (role, column)
            ).fetchone()[0]
            is False
        )


def test_service_role_reads_hidden_metadata_and_writes(db: psycopg.Connection) -> None:
    key = insert_notice(db, visible=False)
    result = summary_json(db)
    db.execute("set local role service_role")
    assert db.execute("select count(*) from notice_summaries").fetchone()[0] == 5
    insert_summary(db, key, result=result)
    db.execute(
        "update notice_summaries set last_error_code='api_timeout' where notice_id=%s", (key,)
    )
    assert db.execute(
        "select source_hash,last_error_code from notice_summaries where notice_id=%s", (key,)
    ).fetchone() == ("c" * 64, "api_timeout")
    db.execute("delete from notice_summaries where notice_id=%s", (key,))
