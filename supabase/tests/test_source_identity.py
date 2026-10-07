"""Validate the consolidated baseline using an empty disposable loopback DB."""

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def database() -> Iterator[psycopg.Connection]:
    dsn = os.environ.get("SCHEMA_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("SCHEMA_TEST_DATABASE_URL is required")
    conn = psycopg.connect(dsn)
    if conn.info.host != "127.0.0.1" or not conn.info.dbname.startswith(
        "pipeline_schema_test_"
    ):
        conn.close()
        pytest.fail("Use a disposable loopback pipeline_schema_test_* database")
    try:
        assert (
            conn.execute("select to_regclass('public.notices')").fetchone()[0] is None
        )
        # Emulate Supabase default API role grants, not the actual Data API.
        conn.execute("create role anon; create role authenticated")
        conn.execute("grant usage on schema public to anon, authenticated")
        conn.execute(
            "alter default privileges in schema public grant all on tables to anon, authenticated"
        )
        conn.execute(
            "alter default privileges in schema public "
            "grant all on sequences to anon, authenticated"
        )
        files = sorted((ROOT / "supabase/migrations").glob("*.sql"))
        assert [path.name for path in files] == [
            "20260922053900_init.sql",
            "20260922053901_rls.sql",
            "20260923044500_holidays.sql",
            "20261005000000_glossary.sql",
            "20261006000000_notice_glossary.sql",
            "20261007000000_notice_easy_text.sql",
            "20261007000001_notice_glossary_current_source.sql",
            "20261007000003_standard_dictionary.sql",
            "20261007000004_notice_easy_text_scope.sql",
            "20261007000005_notice_easy_text_body_only.sql",
        ]
        for path in files:
            conn.execute(path.read_text(encoding="utf-8"))
        conn.execute((ROOT / "supabase/seed.sql").read_text(encoding="utf-8"))
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def db(database: psycopg.Connection) -> Iterator[psycopg.Connection]:
    database.execute("savepoint source_test")
    try:
        yield database
    finally:
        database.execute("rollback to savepoint source_test")
        database.execute("release savepoint source_test")


def insert_notice(
    db: psycopg.Connection,
    *,
    board: str | None = "25",
    category: str = "seoul",
    dong: str | None = None,
    pinned: bool = False,
) -> int:
    return db.execute(
        """insert into notices(category,source_board,dong_group,is_pinned,
                     post_sn,title,registered_on,url)
                     values (%s,%s,%s,%s,'00123','test',current_date,
                     'https://news.seoul.go.kr/test') returning id""",
        (category, board, dong, pinned),
    ).fetchone()[0]


def test_consolidated_init_and_seed(db: psycopg.Connection) -> None:
    key, board, post = db.execute(
        "select id,source_board,post_sn from notices where category='nowon'"
    ).fetchone()
    assert (board, post) == ("1001", "20260901000000001")
    assert (
        db.execute(
            "select file_key from notice_files where notice_id=%s", (key,)
        ).fetchone()[0]
        == "id:aaaaaaaa-0000-0000-0000-000000000001"
    )
    assert db.execute("select count(*) from notices").fetchone()[0] == 5
    assert db.execute("select count(*) from notice_files").fetchone()[0] == 3
    columns = dict(
        db.execute(
            "select column_name,is_nullable from information_schema.columns "
            "where table_schema='public' and table_name='notice_files'"
        ).fetchall()
    )
    assert columns["file_sn"] == columns["file_id"] == "YES"
    assert columns["file_key"] == "NO"
    assert (
        db.execute("select to_regclass('public.holidays')").fetchone()[0] == "holidays"
    )


@pytest.mark.parametrize("board", ["21", "22", "23", "24", "25", "26", "27", "30"])
def test_seoul_boards_and_leading_zero(db: psycopg.Connection, board: str) -> None:
    key = insert_notice(db, board=board)
    assert (
        db.execute("select post_sn from notices where id=%s", (key,)).fetchone()[0]
        == "00123"
    )


def test_same_id_different_board_allowed_same_board_duplicate_rejected(
    db: psycopg.Connection,
) -> None:
    assert insert_notice(db, board="25") != insert_notice(db, board="30")
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_notice(db, board="25")


@pytest.mark.parametrize(
    "values",
    [
        {"board": ""},
        {"board": "99"},
        {"board": None},
        {"dong": "wolgye1"},
        {"pinned": True},
        {"category": "nowon", "board": "25"},
        {"category": "dong", "board": "1042", "dong": None},
    ],
)
def test_invalid_source_shape_rejected(db: psycopg.Connection, values: dict) -> None:
    with pytest.raises(
        (psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation)
    ):
        insert_notice(db, **values)


def test_other_dong_fixed_notice_uses_same_board(db: psycopg.Connection) -> None:
    key = insert_notice(db, category="dong", board="1042", dong="other", pinned=True)
    assert db.execute(
        "select source_board,dong_group from notices where id=%s", (key,)
    ).fetchone() == ("1042", "other")


def test_idless_file_duplicate_and_two_roles(db: psycopg.Connection) -> None:
    key = insert_notice(db)
    url = "https://news.seoul.go.kr/env/files/test.png"
    sql = """insert into notice_files(notice_id,kind,url,file_key) values (%s,%s,%s,
             'url:'||encode(sha256(convert_to(%s,'UTF8')),'hex'))"""
    db.execute(sql, (key, "inline_image", url, url))
    db.execute(sql, (key, "attachment", url, url))
    assert (
        db.execute(
            "select count(*) from notice_files where notice_id=%s "
            "and file_id is null and file_sn is null",
            (key,),
        ).fetchone()[0]
        == 2
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(sql, (key, "inline_image", url, url))


def test_same_file_sn_different_ids_allowed(db: psycopg.Connection) -> None:
    key = insert_notice(db)
    for file_id in ("a", "b"):
        db.execute(
            """insert into notice_files(notice_id,kind,file_sn,file_id,file_key,url)
                    values (%s,'attachment','group',%s,%s,'https://news.seoul.go.kr/file')""",
            (key, file_id, "id:" + file_id),
        )
    assert (
        db.execute(
            "select count(*) from notice_files where notice_id=%s", (key,)
        ).fetchone()[0]
        == 2
    )


@pytest.mark.parametrize(
    "file_id,file_sn,file_key",
    [
        (None, None, "wrong"),
        ("a", None, "id:b"),
        (" ", None, "id: "),
        ("a", " ", "id:a"),
        (None, None, None),
    ],
)
def test_invalid_file_key_rejected(
    db: psycopg.Connection,
    file_id: str | None,
    file_sn: str | None,
    file_key: str | None,
) -> None:
    key = insert_notice(db)
    with pytest.raises(
        (psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation)
    ):
        db.execute(
            """insert into notice_files(notice_id,kind,file_id,file_sn,file_key,url)
                    values (%s,'inline_image',%s,%s,%s,'https://news.seoul.go.kr/image.png')""",
            (key, file_id, file_sn, file_key),
        )


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_visible_rows_allowed_file_key_forbidden(
    db: psycopg.Connection, role: str
) -> None:
    db.execute("set local role " + role)
    assert (
        db.execute("select count(*) from notices where not is_visible").fetchone()[0]
        == 0
    )
    assert (
        len(db.execute("select id,notice_id,kind,url from notice_files").fetchall())
        == 2
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("select file_key from notice_files")


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize(
    "table,column", [("notice_files", "file_name"), ("holidays", "locdate")]
)
def test_private_columns_and_holidays_forbidden(
    db: psycopg.Connection,
    role: str,
    table: str,
    column: str,
) -> None:
    db.execute("set local role " + role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute(f"select {column} from {table}")


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_app_write_forbidden(db: psycopg.Connection, role: str) -> None:
    db.execute("set local role " + role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        insert_notice(db)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_seoul_visible_notice_and_files_allowed_hidden_ones_filtered(
    db: psycopg.Connection, role: str
) -> None:
    visible = insert_notice(db, board="25")
    hidden = insert_notice(db, board="30")
    db.execute("update notices set is_visible=false where id=%s", (hidden,))
    for key in (visible, hidden):
        db.execute(
            "insert into notice_files(notice_id,kind,file_key,url) "
            "values (%s,'inline_image','url:'||encode(sha256(convert_to(%s,'UTF8')),'hex'),%s)",
            (
                key,
                "https://news.seoul.go.kr/image.png",
                "https://news.seoul.go.kr/image.png",
            ),
        )
    db.execute("set local role " + role)
    assert db.execute(
        "select id,source_board,post_sn from notices where category='seoul'"
    ).fetchall() == [(visible, "25", "00123")]
    assert db.execute(
        "select notice_id from notice_files where notice_id=any(%s)",
        ([visible, hidden],),
    ).fetchall() == [(visible,)]


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize(
    "table",
    ["notices", "notice_files", "notice_glossary_results", "notice_easy_texts"],
)
def test_app_table_privileges_are_read_only(
    db: psycopg.Connection, role: str, table: str
) -> None:
    for privilege in (
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    ):
        assert (
            db.execute(
                "select has_table_privilege(%s,%s,%s)", (role, table, privilege)
            ).fetchone()[0]
            is False
        )


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_notice_glossary_originals_follow_notice_visibility(
    db: psycopg.Connection, role: str
) -> None:
    visible = insert_notice(db, board="25")
    hidden = insert_notice(db, board="30")
    db.execute("update notices set is_visible=false where id=%s", (hidden,))
    db.execute(
        """with source as (
               select id, public.notice_easy_text_revision(title, body_html) as notice_revision,
                      'policy test dummy'::text as original_text,
                      '2026-10-06T00:00:00+00:00'::timestamptz as generated_at
               from notices where id=any(%s)
           ), content as (
               select *, encode(sha256(convert_to(original_text,'UTF8')),'hex') as source_hash
               from source
           )
           insert into notice_glossary_results
               (notice_id,source_hash,rules_version,generated_at,status,result)
           select id,source_hash,'policy-test',generated_at,'completed',jsonb_build_object(
               'notice_id',id,'notice_revision',notice_revision,
               'source_hash',source_hash,'rules_version','policy-test',
               'generated_at',generated_at,'status','completed',
               'original_text',original_text,'easy_text',original_text)
           from content""",
        ([visible, hidden],),
    )
    for permitted, denied in ((visible, hidden), (hidden, visible)):
        db.execute(
            "update notices set is_visible=(id=%s) where id=any(%s)",
            (permitted, [visible, hidden]),
        )
        db.execute("set local role " + role)
        assert db.execute(
            "select notice_id,result->>'original_text' from notice_glossary_results "
            "where notice_id=any(%s)",
            ([visible, hidden],),
        ).fetchall() == [(permitted, "policy test dummy")]
        assert (
            db.execute(
                "select result->>'original_text' from notice_glossary_results where notice_id=%s",
                (denied,),
            ).fetchone()
            is None
        )
        db.execute("reset role")
