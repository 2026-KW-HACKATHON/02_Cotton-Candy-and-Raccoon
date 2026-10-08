"""App read contract (#58): two security_invoker views, body text, easy-text columns."""

import ast
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from pipeline.storage.notices import notice_body_text
from pipeline.storage.summary_record import summary_requires_review
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.transform.summary_schema import NoticeSummary

APP_ROLES = ("anon", "authenticated")
VIEWS = ("app_notice_list", "app_notice_detail")
LIST_COLUMNS = (
    "id", "source", "dong_group", "is_pinned", "title", "department", "registered_on",
    "content_updated_at", "is_modified", "summary_status", "display_status", "notice_type",
    "category_code", "deadline_on", "headline", "card_summaries", "attachment_status",
    "has_easy_text",
)
DETAIL_COLUMNS = LIST_COLUMNS + (
    "url", "license_type", "body_text", "result", "generated_at", "file_references",
    "preparation_omissions", "files", "easy_original_text", "easy_text", "easy_changes",
    "easy_body_text_present", "easy_attachment_content_included", "easy_generated_at",
    "easy_result",
)
PRIVATE_NAMES = {
    "file_manifest", "source_hash", "model", "prompt_version", "attempt_count",
    "last_error_code", "notice_revision", "file_name", "file_key", "file_id", "file_sn",
    "content_revision", "is_visible", "post_sn",
}


def test_migration_versions_are_unique():
    migrations = Path(__file__).resolve().parents[1] / "migrations"
    versions = [path.name.split("_", 1)[0] for path in migrations.glob("*.sql")]
    assert len(versions) == len(set(versions)), "Supabase tracks migrations by version"


@pytest.mark.parametrize("database", ["20261008150000_app_notice_views.sql"], indirect=True)
@pytest.mark.parametrize("legacy_branch", ["develop", "backend"])
def test_branch_upgrade_preserves_notices_and_dictionary_cache(db, legacy_branch):
    migrations = Path(__file__).resolve().parents[1] / "migrations"
    app = migrations / "20261008150000_app_notice_views.sql"
    cache = migrations / "20261008160000_standard_dictionary_cache.sql"
    # The old branches used the same version for different SQL. Reproduce their
    # actual schema, then apply only the missing SQL after history reconciliation.
    existing = app if legacy_branch == "develop" else cache
    db.execute(existing.read_text(encoding="utf-8"))
    before = db.execute("select id,title,content_revision from notices order by id").fetchall()
    if legacy_branch == "backend":
        db.execute(
            "insert into standard_dictionary_cache "
            "(cache_key,query_word,search_conditions,contract_version) "
            "values (repeat('b',64),'지원','{}','stdict-v1')"
        )
    for path in sorted(migrations.glob("*.sql")):
        if path.name >= app.name and path != existing:
            db.execute(path.read_text(encoding="utf-8"))
    assert db.execute(
        "select id,title,content_revision from notices order by id"
    ).fetchall() == before
    if legacy_branch == "backend":
        assert db.execute(
            "select query_word from standard_dictionary_cache where cache_key=repeat('b',64)"
        ).fetchone() == ("지원",)
    with as_role(db, "anon"):
        assert db.execute("select id from app_notice_list").fetchall()
EASY_PUBLIC = (
    "notice_id", "original_text", "easy_text", "changes", "body_text_present",
    "attachment_content_included", "generated_at",
)
EASY_PRIVATE = ("notice_revision", "source_hash", "model", "prompt_version", "attempt_count")
# Seed notices the app sees: hidden 5 and other-dong 4 are excluded.
VISIBLE_SEED = {f"2026090100000000{n}" for n in (1, 2, 3, 6, 7, 9)} | {
    "000901", "20260901000000010", "20260901000000011",
}


@contextmanager
def as_role(db: psycopg.Connection, role: str) -> Iterator[psycopg.Connection]:
    db.execute("set role " + role)
    try:
        yield db
    finally:
        db.execute("reset role")


def _post_sn_by_id(db: psycopg.Connection) -> dict[int, str]:
    return dict(db.execute("select id, post_sn from notices").fetchall())


@pytest.mark.parametrize("view", VIEWS)
def test_views_run_with_the_callers_privileges(db: psycopg.Connection, view: str) -> None:
    assert db.execute(
        "select reloptions from pg_class where oid = %s::regclass", (f"public.{view}",)
    ).fetchone() == (["security_invoker=true"],)


@pytest.mark.parametrize(("view", "columns"), [
    ("app_notice_list", LIST_COLUMNS), ("app_notice_detail", DETAIL_COLUMNS),
])
def test_view_columns_match_the_contract(
    db: psycopg.Connection, view: str, columns: tuple[str, ...]
) -> None:
    actual = tuple(name for (name,) in db.execute(
        "select column_name from information_schema.columns "
        "where table_schema = 'public' and table_name = %s order by ordinal_position",
        (view,),
    ))
    assert actual == columns
    assert not PRIVATE_NAMES & set(actual)


@pytest.mark.parametrize("role", APP_ROLES)
@pytest.mark.parametrize("view", VIEWS)
def test_app_roles_only_read_the_views(db: psycopg.Connection, role: str, view: str) -> None:
    privileges = {
        privilege: db.execute(
            "select has_table_privilege(%s, %s, %s)", (role, f"public.{view}", privilege)
        ).fetchone()[0]
        for privilege in ("select", "insert", "update", "delete", "truncate")
    }
    assert privileges == {
        "select": True, "insert": False, "update": False, "delete": False, "truncate": False,
    }


@pytest.mark.parametrize("role", APP_ROLES)
@pytest.mark.parametrize("view", VIEWS)
def test_views_show_visible_notices_except_other_dong(
    db: psycopg.Connection, role: str, view: str
) -> None:
    post_sn = _post_sn_by_id(db)
    with as_role(db, role):
        ids = [row[0] for row in db.execute(f"select id from public.{view}")]
    assert {post_sn[i] for i in ids} == VISIBLE_SEED
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("view", VIEWS)
def test_hiding_a_notice_removes_it_from_the_views(db: psycopg.Connection, view: str) -> None:
    notice_id = db.execute(
        "select id from notices where post_sn = '20260901000000006'"
    ).fetchone()[0]
    db.execute("update notices set is_visible = false where id = %s", (notice_id,))
    with as_role(db, "anon"):
        assert db.execute(
            f"select count(*) from public.{view} where id = %s", (notice_id,)
        ).fetchone() == (0,)


def test_unclassified_filter_includes_notices_without_summary(db: psycopg.Connection) -> None:
    post_sn = _post_sn_by_id(db)
    with as_role(db, "anon"):
        unclassified = {
            post_sn[i] for (i,) in db.execute(
                "select id from public.app_notice_list where category_code is null"
            )
        }
        culture = {
            post_sn[i] for (i,) in db.execute(
                "select id from public.app_notice_list where category_code = 26"
            )
        }
    assert unclassified == {
        "20260901000000001", "20260901000000002", "000901", "20260901000000009",
        "20260901000000010", "20260901000000011",
    }
    assert culture == {"20260901000000006", "20260901000000007"}


def _python_display_status(row: dict) -> str:
    if row["status"] is None:
        return "none"
    return build_notice_summary_view(
        status=row["status"], result=row["result"], attachment_status=row["attachment_status"],
        preparation_omissions=row["preparation_omissions"],
    ).status


def _assert_display_status_matches_python(db: psycopg.Connection) -> int:
    with db.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "select n.id, s.status, s.result, s.attachment_status, s.preparation_omissions "
            "from notices n left join notice_summaries s on s.notice_id = n.id "
            "where n.is_visible and n.dong_group is distinct from 'other'"
        ).fetchall()
    with as_role(db, "anon"):
        shown = dict(db.execute("select id, display_status from public.app_notice_list"))
    for row in rows:
        assert shown[row["id"]] == _python_display_status(row), row["id"]
    return len(rows)


def test_display_status_matches_python_for_seed(db: psycopg.Connection) -> None:
    # The pipeline never stores a summarized result that requires review, so the
    # view's rule is complete for rows it wrote. Check that invariant on the seed.
    for result, attachment_status in db.execute(
        "select result, attachment_status from notice_summaries where status = 'summarized'"
    ):
        assert not summary_requires_review(
            NoticeSummary.model_validate(result), attachment_status=attachment_status
        )
    assert _assert_display_status_matches_python(db) == len(VISIBLE_SEED)
    with as_role(db, "anon"):
        statuses = {s for (s,) in db.execute("select display_status from public.app_notice_list")}
    assert statuses == {"summarized", "needs_review", "pending", "failed", "none"}


def test_summarized_row_with_omissions_is_shown_as_needs_review(db: psycopg.Connection) -> None:
    notice_id, result, url = db.execute(
        "select n.id, s.result, n.url from notices n join notice_summaries s on s.notice_id = n.id "
        "where n.post_sn = '20260901000000006'"
    ).fetchone()
    manifest = {
        "notice_id": notice_id, "source_revision": 1, "original_url": url, "files": [],
        "media": [],
        "omissions": [{"notice_file_id": None, "url": url, "reason_code": "type_mismatch"}],
    }
    db.execute(
        "update notice_summaries set file_manifest = %s where notice_id = %s",
        (Jsonb(manifest), notice_id),
    )
    with as_role(db, "anon"):
        assert db.execute(
            "select summary_status, display_status from public.app_notice_list where id = %s",
            (notice_id,),
        ).fetchone() == ("summarized", "needs_review")
    assert result is not None
    _assert_display_status_matches_python(db)


def test_seed_body_text_is_the_pipeline_conversion(db: psycopg.Connection) -> None:
    rows = db.execute("select body_html, body_text from notices").fetchall()
    assert rows
    for body_html, body_text in rows:
        assert body_text == notice_body_text(body_html)


def test_detail_lists_files_without_names_and_current_easy_text(db: psycopg.Connection) -> None:
    ids = {sn: i for i, sn in _post_sn_by_id(db).items()}
    with as_role(db, "anon"), db.cursor(row_factory=dict_row) as cursor:
        files = cursor.execute(
            "select files from public.app_notice_detail where id = %s",
            (ids["20260901000000007"],),
        ).fetchone()["files"]
        easy = {
            row["id"]: row for row in cursor.execute(
                "select id, has_easy_text, easy_text, easy_changes, easy_result "
                "from public.app_notice_detail "
                "where id = any(%s)",
                ([ids["20260901000000006"], ids["20260901000000010"],
                  ids["20260901000000011"]],),
            )
        }
    assert [set(item) for item in files] == [{"id", "kind", "url"}] * 2
    assert [item["kind"] for item in files] == ["attachment", "attachment"]
    assert easy[ids["20260901000000006"]]["has_easy_text"] is True
    assert easy[ids["20260901000000010"]]["easy_changes"] == []
    assert easy[ids["20260901000000010"]]["easy_result"]["sections"][0]["heading"] == (
        "누가 참여하나요?"
    )
    # Notice 11's easy text was made for an earlier body, so the app never sees it.
    stale = easy[ids["20260901000000011"]]
    assert (stale["has_easy_text"], stale["easy_text"], stale["easy_result"]) == (
        False, None, None,
    )


@pytest.mark.parametrize("role", APP_ROLES)
def test_easy_text_private_columns_are_denied(db: psycopg.Connection, role: str) -> None:
    for column in EASY_PUBLIC:
        assert db.execute(
            "select has_column_privilege(%s, 'public.notice_easy_texts', %s, 'select')",
            (role, column),
        ).fetchone() == (True,)
    for column in EASY_PRIVATE:
        with as_role(db, role), pytest.raises(psycopg.errors.InsufficientPrivilege), \
                db.transaction():
            db.execute(f"select {column} from public.notice_easy_texts")


@pytest.mark.parametrize("role", APP_ROLES)
@pytest.mark.parametrize("table", ["notice_easy_texts", "notice_summaries", "notice_files"])
def test_select_star_on_tables_with_private_columns_is_denied(
    db: psycopg.Connection, role: str, table: str
) -> None:
    with as_role(db, role), pytest.raises(psycopg.errors.InsufficientPrivilege), \
            db.transaction():
        db.execute(f"select * from public.{table}")


def test_filling_body_text_keeps_revision_and_summary(db: psycopg.Connection) -> None:
    # Backfilling the derived text must not look like a source change to the trigger.
    notice_id = db.execute(
        "select id from notices where post_sn = '20260901000000006'"
    ).fetchone()[0]
    query = (
        "select n.content_revision, n.content_updated_at, n.is_modified, s.status, s.result "
        "from notices n join notice_summaries s on s.notice_id = n.id where n.id = %s"
    )
    before = db.execute(query, (notice_id,)).fetchone()
    db.execute("update notices set body_text = null where id = %s", (notice_id,))
    db.execute("update notices set body_text = 'refilled' where id = %s", (notice_id,))
    assert db.execute(query, (notice_id,)).fetchone() == before


@pytest.mark.parametrize("intervening_change", ["none", "html", "text", "both"])
def test_documented_backfill_preserves_updates_after_read(
    db: psycopg.Connection, intervening_change: str, pipeline_readme: str,
) -> None:
    # Run the documented SQL itself so the operational recipe cannot regress
    # independently of a copied test query.
    snippet = next(
        block.split("```", 1)[0]
        for block in pipeline_readme.split("```python\n")[1:]
        if "select id, body_html from notices where body_text is null" in block
    )
    update = next(
        node.value for node in ast.walk(ast.parse(snippet))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value.startswith("update notices set body_text")
    )
    notice_id = db.execute(
        "select id from notices where post_sn = '20260901000000006'"
    ).fetchone()[0]
    db.execute("update notices set body_text = null where id = %s", (notice_id,))
    old_html = db.execute(
        "select body_html from notices where id = %s", (notice_id,),
    ).fetchone()[0]
    if intervening_change in {"html", "both"}:
        db.execute(
            "update notices set body_html = '<p>마감: 2026-10-31</p>' where id = %s",
            (notice_id,),
        )
    if intervening_change in {"text", "both"}:
        db.execute("update notices set body_text = '최신 평문' where id = %s", (notice_id,))
    query = (
        "select n.body_html, n.body_text, n.content_revision, s.status, s.result "
        "from notices n join notice_summaries s on s.notice_id = n.id where n.id = %s"
    )
    before = db.execute(query, (notice_id,)).fetchone()
    cursor = db.execute(update, (notice_body_text(old_html), notice_id, old_html))
    after = db.execute(query, (notice_id,)).fetchone()
    if intervening_change == "none":
        assert cursor.rowcount == 1
        assert after == (before[0], notice_body_text(old_html), *before[2:])
    else:
        assert cursor.rowcount == 0
        assert after == before


@pytest.mark.parametrize("database", ["20261008220000_app_notice_views.sql"], indirect=True)
def test_backend_upgrade_preserves_raw_rows_and_can_reapply_existing_contract(db):
    before = db.execute("select id,title,content_revision from notices order by id").fetchall()
    migration = (
        Path(__file__).resolve().parents[1] / "migrations/20261008220000_app_notice_views.sql"
    ).read_text(encoding="utf-8")
    db.execute(migration)
    with as_role(db, "anon"):
        published = db.execute(
            "select id,url,body_text from app_notice_detail order by id",
        ).fetchall()
        assert published
    # Also supports a database on which the #58 view contract already exists.
    db.execute(migration)
    assert db.execute(
        "select id,title,content_revision from notices order by id",
    ).fetchall() == before
    with as_role(db, "anon"):
        assert db.execute(
            "select id,url,body_text from app_notice_detail order by id",
        ).fetchall() == published
