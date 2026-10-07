"""Validate the consolidated baseline using an empty disposable loopback DB."""

import psycopg
import pytest


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
        db.execute("select file_key from notice_files where notice_id=%s", (key,)).fetchone()[0]
        == "id:aaaaaaaa-0000-0000-0000-000000000001"
    )
    assert db.execute("select count(*) from notices").fetchone()[0] == 5
    assert db.execute("select count(*) from notice_files").fetchone()[0] == 4
    columns = dict(
        db.execute(
            "select column_name,is_nullable from information_schema.columns "
            "where table_schema='public' and table_name='notice_files'"
        ).fetchall()
    )
    assert columns["file_sn"] == columns["file_id"] == "YES"
    assert columns["file_key"] == "NO"
    assert db.execute("select to_regclass('public.holidays')").fetchone()[0] == "holidays"


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
            (key, "https://news.seoul.go.kr/image.png", "https://news.seoul.go.kr/image.png"),
        )
    db.execute("set local role " + role)
    assert db.execute(
        "select id,source_board,post_sn from notices where category='seoul'"
    ).fetchall() == [(visible, "25", "00123")]
    assert db.execute(
        "select notice_id from notice_files where notice_id=any(%s)", ([visible, hidden],)
    ).fetchall() == [(visible,)]


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("table", ["notices", "notice_files"])
def test_app_table_privileges_are_read_only(db: psycopg.Connection, role: str, table: str) -> None:
    for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert (
            db.execute("select has_table_privilege(%s,%s,%s)", (role, table, privilege)).fetchone()[
                0
            ]
            is False
        )
