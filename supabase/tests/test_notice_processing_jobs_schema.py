"""The durable processing queue is backend-only, including Supabase default grants."""

import psycopg
import pytest


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("privilege", ["select", "insert", "update", "delete", "truncate"])
def test_app_has_no_processing_job_privilege(
    db: psycopg.Connection, role: str, privilege: str,
) -> None:
    assert db.execute(
        "select has_table_privilege(%s, 'public.notice_processing_jobs', %s)",
        (role, privilege),
    ).fetchone() == (False,)


def test_processing_queue_has_rls_and_backend_can_use_it(db: psycopg.Connection) -> None:
    assert db.execute(
        "select relrowsecurity from pg_class where oid='public.notice_processing_jobs'::regclass"
    ).fetchone() == (True,)
    notice_id = db.execute("select min(id) from public.notices").fetchone()[0]
    db.execute("set role service_role")
    try:
        db.execute(
            "insert into public.notice_processing_jobs "
            "(notice_id,feature,input_version,contract_key) values (%s,'summary','1','test')",
            (notice_id,),
        )
        rows = db.execute("select state,attempts from public.notice_processing_jobs").fetchall()
        assert rows == [("pending", 0)]
        db.execute("update public.notice_processing_jobs set state='blocked'")
        db.execute("delete from public.notice_processing_jobs")
    finally:
        db.execute("reset role")


@pytest.mark.parametrize("assignment", [
    "state='running'", "state='retry_wait'", "attempts=-1",
    "input_version='bad'", "feature='dictionary'", "last_error_code='secret url=https://'",
])
def test_invalid_queue_state_is_rejected(db: psycopg.Connection, assignment: str) -> None:
    notice_id = db.execute("select min(id) from public.notices").fetchone()[0]
    db.execute(
        "insert into public.notice_processing_jobs "
        "(notice_id,feature,input_version,contract_key) values (%s,'summary','1','test')",
        (notice_id,),
    )
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute("update public.notice_processing_jobs set " + assignment)
