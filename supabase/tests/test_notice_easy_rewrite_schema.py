"""#85 question-section rewrite column, old-worker protection and app reads."""

from contextlib import contextmanager

import psycopg
import pytest
from psycopg.types.json import Jsonb

APP_ROLES = ("anon", "authenticated")


@contextmanager
def as_role(db: psycopg.Connection, role: str):
    db.execute("set role " + role)
    try:
        yield db
    finally:
        db.execute("reset role")


def _notice_id(db: psycopg.Connection, post_sn: str) -> int:
    return db.execute("select id from notices where post_sn = %s", (post_sn,)).fetchone()[0]


REWRITE_NOTICE = "20260901000000010"


@pytest.mark.parametrize("value", [[], "text", 1, True])
def test_easy_result_must_be_an_object_or_null(db: psycopg.Connection, value) -> None:
    notice_id = _notice_id(db, REWRITE_NOTICE)
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute(
            "update notice_easy_texts set easy_result = %s where notice_id = %s",
            (Jsonb(value), notice_id),
        )


@pytest.mark.parametrize("role", APP_ROLES)
def test_app_roles_read_easy_result_but_not_private_columns(db, role) -> None:
    assert db.execute(
        "select has_column_privilege(%s, 'public.notice_easy_texts', 'easy_result', 'select'), "
        "has_column_privilege(%s, 'public.notice_easy_texts', 'easy_result', 'update'), "
        "has_column_privilege(%s, 'public.notice_easy_texts', 'prompt_version', 'select')",
        (role, role, role),
    ).fetchone() == (True, False, False)


def test_old_worker_conversion_update_clears_a_stale_rewrite_and_candidates(db) -> None:
    notice_id = _notice_id(db, REWRITE_NOTICE)
    db.execute(
        "update notice_easy_texts set dictionary_candidates = '[]'::jsonb where notice_id = %s",
        (notice_id,),
    )
    # A worker that predates easy_result rewrites easy_text but never sends the column.
    db.execute(
        "update notice_easy_texts set easy_text = original_text, changes = '[]'::jsonb, "
        "generated_at = generated_at + interval '1 second' where notice_id = %s",
        (notice_id,),
    )
    assert db.execute(
        "select easy_result, dictionary_candidates from notice_easy_texts where notice_id = %s",
        (notice_id,),
    ).fetchone() == (None, None)


def test_scope_only_update_keeps_the_rewrite(db) -> None:
    notice_id = _notice_id(db, REWRITE_NOTICE)
    before = db.execute(
        "select easy_result from notice_easy_texts where notice_id = %s", (notice_id,)
    ).fetchone()[0]
    db.execute(
        "update notice_easy_texts set body_text_present = true where notice_id = %s",
        (notice_id,),
    )
    assert before is not None
    assert db.execute(
        "select easy_result from notice_easy_texts where notice_id = %s", (notice_id,)
    ).fetchone() == (before,)


@pytest.mark.parametrize("role", APP_ROLES)
def test_has_easy_text_is_true_only_for_a_current_rewrite(db, role) -> None:
    rewritten = _notice_id(db, REWRITE_NOTICE)
    stale = _notice_id(db, "20260901000000011")
    with as_role(db, role):
        before = dict(db.execute(
            "select id, has_easy_text from app_notice_list where id = any(%s)",
            ([rewritten, stale],),
        ))
        detail = db.execute(
            "select easy_result is not null from app_notice_detail where id = %s", (rewritten,)
        ).fetchone()
    assert before == {rewritten: True, stale: False}
    assert detail == (True,)
    # A legacy replacement row (no easy_result) stays readable but is not a rewrite.
    db.execute(
        "update notice_easy_texts set easy_result = null where notice_id = %s", (rewritten,)
    )
    with as_role(db, role):
        assert db.execute(
            "select l.has_easy_text, d.easy_text is not null, d.easy_result "
            "from app_notice_list l join app_notice_detail d using (id) where l.id = %s",
            (rewritten,),
        ).fetchone() == (False, True, None)


def test_hidden_notice_rewrite_is_not_visible(db) -> None:
    notice_id = _notice_id(db, REWRITE_NOTICE)
    db.execute("update notices set is_visible = false where id = %s", (notice_id,))
    with as_role(db, "anon"):
        assert db.execute(
            "select count(*) from app_notice_detail where id = %s", (notice_id,)
        ).fetchone() == (0,)
        assert db.execute(
            "select count(*) from notice_easy_texts where notice_id = %s", (notice_id,)
        ).fetchone() == (0,)


def test_easy_result_is_the_last_detail_column(db) -> None:
    assert db.execute(
        "select column_name from information_schema.columns where table_schema = 'public' "
        "and table_name = 'app_notice_detail' order by ordinal_position desc limit 1"
    ).fetchone() == ("easy_result",)


@pytest.mark.parametrize("role", APP_ROLES)
def test_rewrite_trigger_function_is_not_executable_by_the_app(db, role) -> None:
    assert db.execute(
        "select has_function_privilege(%s, 'public.invalidate_notice_easy_rewrite()', 'execute')",
        (role,),
    ).fetchone() == (False,)
