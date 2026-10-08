"""Read, normalize, and compare what a case left in the database and what anon sees."""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.sql import SQL, Identifier

SNAPSHOT_TABLES = (
    "notices",
    "notice_files",
    "notice_summaries",
    "notice_easy_texts",
    "notice_summary_executions",
)

# What the app can read. Kept explicit (no "select *"): check_anon_grants() fails when
# a migration changes anon's column grants so this list and the expectations are revisited.
ANON_COLUMNS: dict[str, tuple[str, ...]] = {
    "notices": (
        "id", "category", "source_board", "dong_group", "is_pinned", "post_sn", "title",
        "department", "registered_on", "url", "body_html", "license_type", "is_modified",
        "is_visible", "created_at", "updated_at", "content_updated_at", "content_revision",
    ),
    "notice_files": ("id", "notice_id", "kind", "url"),
    "notice_summaries": (
        "notice_id", "status", "category", "category_code", "deadline_on", "result",
        "attachment_status", "generated_at", "card_summaries", "file_references",
        "preparation_omissions",
    ),
    "notice_easy_texts": (
        "notice_id", "notice_revision", "source_hash", "original_text", "easy_text", "changes",
        "model", "prompt_version", "attempt_count", "generated_at", "body_text_present",
        "attachment_content_included",
    ),
}

LONG_TEXT = 300
SEQUENCE_COLUMNS = {"execution_token"}
TIME_TYPES = {"timestamp with time zone", "timestamp without time zone"}

Raw = dict[str, dict[str, dict[str, Any]]]


class SnapshotError(AssertionError):
    """The database does not match what the harness knows how to snapshot."""


def _column_types(conn: psycopg.Connection, tables: tuple[str, ...]) -> dict[str, dict[str, str]]:
    rows = conn.execute(
        "select table_name, column_name, data_type from information_schema.columns "
        "where table_schema = 'public' and table_name = any(%s) "
        "order by table_name, ordinal_position",
        (list(tables),),
    ).fetchall()
    types: dict[str, dict[str, str]] = {table: {} for table in tables}
    for table, column, data_type in rows:
        types[table][column] = data_type
    return types


def check_anon_grants(conn: psycopg.Connection) -> None:
    rows = conn.execute(
        "select table_name, column_name from information_schema.column_privileges "
        "where grantee = 'anon' and privilege_type = 'SELECT' and table_schema = 'public' "
        "and table_name = any(%s)",
        (list(ANON_COLUMNS),),
    ).fetchall()
    granted: dict[str, set[str]] = {table: set() for table in ANON_COLUMNS}
    for table, column in rows:
        granted[table].add(column)
    mismatched = {
        table: {"granted": sorted(granted[table]), "harness": sorted(columns)}
        for table, columns in ANON_COLUMNS.items()
        if granted[table] != set(columns)
    }
    if mismatched:
        raise SnapshotError(
            "anon SELECT grants differ from harness ANON_COLUMNS; update snapshot.py and "
            f"review expectations: {json.dumps(mismatched, ensure_ascii=False)}"
        )


def _key_rows(table: str, rows: list[dict[str, Any]], notice_keys: dict[int, str]) -> dict:
    keyed: dict[str, dict[str, Any]] = {}
    for row in rows:
        row = dict(row)
        if table == "notices":
            key = f"{row.pop('category')}/{row.pop('source_board')}/{row.pop('post_sn')}"
            row.pop("id", None)
        else:
            notice = notice_keys.get(row.pop("notice_id", None), "<unknown notice>")
            if table == "notice_files":
                row.pop("id", None)
                key = f"{notice}|{row.get('kind')}|{row.get('file_key', row.get('url'))}"
            else:
                key = notice
        if key in keyed:
            raise SnapshotError(f"duplicate snapshot key in {table}: {key}")
        keyed[key] = row
    return dict(sorted(keyed.items()))


def read_database(conn: psycopg.Connection) -> tuple[Raw, dict[str, int], dict[str, dict]]:
    """Raw rows of SNAPSHOT_TABLES keyed by notice, plus row counts of every public table."""
    types = _column_types(conn, SNAPSHOT_TABLES)
    with conn.cursor(row_factory=dict_row) as cursor:
        notice_rows = cursor.execute("select * from public.notices").fetchall()
        notice_keys = {
            row["id"]: f"{row['category']}/{row['source_board']}/{row['post_sn']}"
            for row in notice_rows
        }
        raw: Raw = {}
        for table in SNAPSHOT_TABLES:
            rows = (
                notice_rows
                if table == "notices"
                else cursor.execute(SQL("select * from public.{}").format(Identifier(table)))
                .fetchall()
            )
            raw[table] = _key_rows(table, rows, notice_keys)
    tables = [
        name for (name,) in conn.execute(
            "select table_name from information_schema.tables "
            "where table_schema = 'public' and table_type = 'BASE TABLE' order by table_name"
        ).fetchall()
    ]
    counts = {
        name: conn.execute(SQL("select count(*) from public.{}").format(Identifier(name)))
        .fetchone()[0]
        for name in tables
    }
    return raw, counts, types


def read_anon(conn: psycopg.Connection) -> tuple[Raw, dict[str, str]]:
    """Rows visible to the app role, using its explicit column list per table."""
    check_anon_grants(conn)
    raw: Raw = {}
    denied: dict[str, str] = {}
    with conn.transaction():
        conn.execute("set local role anon")
        notice_keys: dict[int, str] = {}
        for table, columns in ANON_COLUMNS.items():
            query = SQL("select {} from public.{}").format(
                SQL(", ").join(Identifier(c) for c in columns), Identifier(table)
            )
            try:
                with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
                    rows = cursor.execute(query).fetchall()
            except psycopg.errors.InsufficientPrivilege:
                denied[table] = "permission denied"
                raw[table] = {}
                continue
            if table == "notices":
                notice_keys = {
                    row["id"]: f"{row['category']}/{row['source_board']}/{row['post_sn']}"
                    for row in rows
                }
            raw[table] = _key_rows(table, rows, notice_keys)
    return raw, denied


def _text(value: str) -> str:
    if len(value) <= LONG_TEXT:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"<text len={len(value)} sha256={digest}>"


def _value(value: Any, data_type: str, column: str, previous: Any, existed: bool) -> Any:
    if data_type in TIME_TYPES:
        if value is None:
            return None
        if not existed:
            return "<set>"
        return "<unchanged>" if value == previous else "<changed>"
    if value is None:
        return None
    if column in SEQUENCE_COLUMNS or isinstance(value, UUID):
        return "<set>"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        data = bytes(value)
        return f"<bytes len={len(data)} sha256={hashlib.sha256(data).hexdigest()[:16]}>"
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, (dict, list)):
        return json.loads(json.dumps(value, sort_keys=True, default=str))
    return value


def normalize(raw: Raw, types: dict[str, dict[str, str]], previous: Raw | None) -> dict:
    previous = previous or {}
    result: dict[str, dict] = {}
    for table, rows in raw.items():
        before = previous.get(table, {})
        result[table] = {
            key: {
                column: _value(
                    value,
                    types.get(table, {}).get(column, ""),
                    column,
                    before.get(key, {}).get(column),
                    key in before,
                )
                for column, value in row.items()
            }
            for key, row in rows.items()
        }
    return result


def diff(expected: Any, actual: Any, path: str = "") -> list[str]:
    """Readable differences, one line per table/row/column that does not match."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        lines: list[str] = []
        for key in list(expected) + [k for k in actual if k not in expected]:
            where = f"{path}[{json.dumps(key, ensure_ascii=False)}]" if path else str(key)
            if key not in actual:
                lines.append(f"{where}: missing (expected {_short(expected[key])})")
            elif key not in expected:
                lines.append(f"{where}: unexpected {_short(actual[key])}")
            else:
                lines.extend(diff(expected[key], actual[key], where))
        return lines
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [
            line
            for index, (left, right) in enumerate(zip(expected, actual, strict=True))
            for line in diff(left, right, f"{path}[{index}]")
        ]
    if expected != actual:
        return [f"{path}: expected {_short(expected)}, got {_short(actual)}"]
    return []


def _short(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return text if len(text) <= 200 else text[:197] + "..."
