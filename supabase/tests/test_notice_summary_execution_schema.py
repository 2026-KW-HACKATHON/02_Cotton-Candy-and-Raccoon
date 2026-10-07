"""Keep execution order private while backend guarded writes remain available."""

import psycopg
import pytest

from pipeline.storage.summaries import begin_summary_execution


def _notice(db: psycopg.Connection) -> int:
    return db.execute(
        "insert into notices(category,source_board,post_sn,title,registered_on,url,is_visible) "
        "values ('nowon','1001','schema-execution','test',current_date,"
        "'https://www.nowon.kr/test/execution',false) returning id"
    ).fetchone()[0]


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize(
    "statement",
    [
        "select notice_id,execution_token from notice_summary_executions",
        "insert into notice_summary_executions(notice_id) values (1)",
        "update notice_summary_executions set execution_token=1",
        "delete from notice_summary_executions",
        "truncate notice_summary_executions",
        "select last_value from notice_summary_execution_token_seq",
        "select nextval('notice_summary_execution_token_seq')",
        "select setval('notice_summary_execution_token_seq',1)",
    ],
)
def test_app_cannot_access_execution_registry_or_sequence(
    db: psycopg.Connection, role: str, statement: str,
) -> None:
    db.execute("set local role " + role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute(statement)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_broad_default_grants_revoked_for_execution_storage(
    db: psycopg.Connection, role: str,
) -> None:
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert db.execute(
            "select has_table_privilege(%s,'notice_summary_executions',%s)", (role, privilege)
        ).fetchone() == (False,)
    for privilege in ("SELECT", "USAGE", "UPDATE"):
        assert db.execute(
            "select has_sequence_privilege(%s,'notice_summary_execution_token_seq',%s)",
            (role, privilege),
        ).fetchone() == (False,)


def test_service_role_can_register_tokens_for_hidden_notices_without_bypassrls(
    db: psycopg.Connection,
) -> None:
    notice_id = _notice(db)
    db.execute("set local role service_role")
    first = begin_summary_execution(db, notice_id)
    latest = begin_summary_execution(db, notice_id)
    assert latest > first
    assert db.execute(
        "select execution_token from notice_summary_executions where notice_id=%s", (notice_id,)
    ).fetchone() == (latest,)
    assert db.execute(
        "select relrowsecurity from pg_class where oid='notice_summary_executions'::regclass"
    ).fetchone() == (True,)


def test_execution_registry_delete_cascades_with_notice(db: psycopg.Connection) -> None:
    notice_id = _notice(db)
    begin_summary_execution(db, notice_id)
    db.execute("delete from notices where id=%s", (notice_id,))
    assert db.execute(
        "select execution_token from notice_summary_executions where notice_id=%s", (notice_id,)
    ).fetchone() is None


@pytest.mark.parametrize("value", [0, -1])
def test_execution_tokens_must_be_positive(db: psycopg.Connection, value: int) -> None:
    notice_id = _notice(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "insert into notice_summary_executions(notice_id,execution_token,source_revision) "
            "values (%s,%s,1)",
            (notice_id, value),
        )
