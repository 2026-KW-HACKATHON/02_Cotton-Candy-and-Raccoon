"""Source-version constraints and app privileges after the incremental migration."""

import psycopg
import pytest


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_revision_registry_and_trigger_functions_remain_private(db, role):
    assert db.execute(
        "select has_table_privilege(%s,'public.notice_summary_executions','select'),"
        "has_function_privilege(%s,'public.invalidate_summary_on_source_change()','execute'),"
        "has_function_privilege(%s,'public.invalidate_summary_on_source_file_change()','execute')",
        (role, role, role),
    ).fetchone() == (False, False, False)
    assert db.execute(
        "select has_column_privilege(%s,'public.notices','content_revision','select'),"
        "has_column_privilege(%s,'public.notices','content_revision','update')", (role, role),
    ).fetchone() == (True, False)


def test_source_revision_backfill_and_constraints(db):
    assert db.execute(
        "select count(*) from notices where content_revision is null or content_revision<=0"
    ).fetchone() == (0,)
    assert db.execute(
        "select is_nullable,column_default from information_schema.columns "
        "where table_schema='public' and table_name='notices' and column_name='content_revision'"
    ).fetchone() == ("NO", "1")
    assert db.execute(
        "select is_nullable from information_schema.columns where table_schema='public' "
        "and table_name='notice_summary_executions' and column_name='source_revision'"
    ).fetchone() == ("NO",)
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute(
            "insert into notices(category,source_board,post_sn,title,registered_on,url,"
            "content_revision) values ('nowon','1001','invalid-counter','title',current_date,"
            "'https://www.nowon.kr/test',0)"
        )


def test_triggers_are_enabled_and_service_policy_does_not_widen_app_writes(db):
    assert db.execute(
        "select tgname,tgenabled from pg_trigger where not tgisinternal and tgname in "
        "('notices_summary_source_change','notice_files_summary_source_change') order by tgname"
    ).fetchall() == [
        ("notice_files_summary_source_change", "O"), ("notices_summary_source_change", "O"),
    ]
    policies = db.execute(
        "select tablename,cmd,roles from pg_policies where policyname in "
        "('service role manages notice sources','service role manages notice source files') "
        "order by tablename"
    ).fetchall()
    assert policies == [
        ("notice_files", "ALL", ["service_role"]), ("notices", "ALL", ["service_role"]),
    ]
    for role in ("anon", "authenticated"):
        assert db.execute(
            "select has_table_privilege(%s,'public.notices','insert'),"
            "has_table_privilege(%s,'public.notices','update'),"
            "has_table_privilege(%s,'public.notice_files','delete')", (role, role, role),
        ).fetchone() == (False, False, False)
