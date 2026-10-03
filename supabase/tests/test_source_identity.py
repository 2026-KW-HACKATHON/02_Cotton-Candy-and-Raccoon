"""Use only an empty disposable loopback PostgreSQL database."""

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
    if conn.info.host != "127.0.0.1" or not conn.info.dbname.startswith("pipeline_schema_test_"):
        conn.close()
        pytest.fail("Use a disposable loopback pipeline_schema_test_* database")
    try:
        assert conn.execute("select to_regclass('public.notices')").fetchone()[0] is None
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
        assert len(files) == 5
        for path in files[:4]:
            conn.execute(path.read_text(encoding="utf-8"))
        conn.execute("""insert into notices(category,post_sn,title,registered_on,url)
                     values ('nowon','000001','legacy',current_date,'https://www.nowon.kr/old')""")
        key = conn.execute("select id from notices where post_sn='000001'").fetchone()[0]
        conn.execute(
            """insert into notice_files(notice_id,kind,file_sn,file_id,url)
                      values (%s,'attachment','group','old-uuid','https://www.nowon.kr/old.pdf')""",
            (key,),
        )
        conn.execute(files[4].read_text(encoding="utf-8"))
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


def test_backfill_and_seed(db: psycopg.Connection) -> None:
    key, board, post = db.execute(
        "select id,source_board,post_sn from notices where title='legacy'"
    ).fetchone()
    assert (board, post) == ("1001", "000001")
    assert (
        db.execute("select file_key from notice_files where notice_id=%s", (key,)).fetchone()[0]
        == "id:old-uuid"
    )
    assert db.execute("select count(*) from notices").fetchone()[0] == 6
    assert db.execute("select count(*) from notice_files").fetchone()[0] == 4


@pytest.mark.parametrize("board", ["21", "22", "23", "24", "25", "26", "27", "30"])
def test_seoul_boards_and_leading_zero(db: psycopg.Connection, board: str) -> None:
    key = insert_notice(db, board=board)
    assert db.execute("select post_sn from notices where id=%s", (key,)).fetchone()[0] == "00123"


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
    with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation)):
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
        db.execute("select count(*) from notice_files where notice_id=%s", (key,)).fetchone()[0]
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
    with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation)):
        db.execute(
            """insert into notice_files(notice_id,kind,file_id,file_sn,file_key,url)
                    values (%s,'inline_image',%s,%s,%s,'https://news.seoul.go.kr/image.png')""",
            (key, file_id, file_sn, file_key),
        )


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_visible_rows_allowed_file_key_forbidden(db: psycopg.Connection, role: str) -> None:
    db.execute("set local role " + role)
    assert db.execute("select count(*) from notices where not is_visible").fetchone()[0] == 0
    assert len(db.execute("select id,notice_id,kind,url from notice_files").fetchall()) == 3
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("select file_key from notice_files")


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("table,column", [("notice_files", "file_name"), ("holidays", "locdate")])
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
