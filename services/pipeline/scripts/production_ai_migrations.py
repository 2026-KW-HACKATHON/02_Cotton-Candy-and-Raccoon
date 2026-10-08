"""Issue #76: bounded upgrade from the deployed app-view baseline, never reset a DB.

prepare creates an authenticated encrypted pg_dump and a matching data fingerprint.
apply requires that backup, rejects drift, and commits SQL/history only after validation.
Credentials travel through environment variables and never appear in command arguments.
"""

import argparse
import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg.conninfo import conninfo_to_dict
from psycopg.sql import SQL, Identifier

MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
BASE = (
    "20260922053900", "20260922053901", "20260922053902", "20260922053903",
    "20261008150000",
)
UPGRADES = (
    "20261008160000", "20261008170000", "20261008190000",
    "20261008210000", "20261008220000",
)
PRIVATE = ("standard_dictionary_cache", "notice_dictionary_links", "notice_processing_jobs")
VIEWS = ("app_notice_list", "app_notice_detail")
MAGIC = b"issue76-backup-v1\n"


class UpgradeError(RuntimeError):
    """Fixed diagnostic codes only; do not print arbitrary DB/subprocess errors."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def migration_files():
    paths = sorted(MIGRATIONS.glob("*.sql"))
    versions = [p.name.split("_", 1)[0] for p in paths]
    if tuple(versions) != BASE + UPGRADES:
        raise UpgradeError("unexpected_local_migration_set")
    return dict(zip(versions, paths, strict=True))


def inspect(conn):
    versions = tuple(r[0] for r in conn.execute(
        "select version from supabase_migrations.schema_migrations order by version"
    ))
    if versions not in (BASE, BASE + UPGRADES):
        raise UpgradeError("unexpected_history_manual_review_required")
    present = {name: conn.execute("select to_regclass(%s) is not null", (
        "public." + name,
    )).fetchone()[0] for name in (*PRIVATE, *VIEWS)}
    candidates = conn.execute(
        "select exists(select 1 from information_schema.columns where table_schema='public' "
        "and table_name='notice_easy_texts' and column_name='dictionary_candidates')"
    ).fetchone()[0]
    if not all(present[v] for v in VIEWS):
        raise UpgradeError("baseline_app_views_missing")
    upgraded = versions == BASE + UPGRADES
    if any(present[t] != upgraded for t in PRIVATE) or candidates != upgraded:
        raise UpgradeError("history_schema_mismatch_manual_review_required")
    return versions


def layout(conn):
    result = {}
    for table, column in conn.execute(
        "select c.table_name,c.column_name from information_schema.columns c "
        "join information_schema.tables t using(table_catalog,table_schema,table_name) "
        "where c.table_schema='public' and t.table_type='BASE TABLE' "
        "order by c.table_name,c.ordinal_position"
    ):
        result.setdefault(table, []).append(column)
    return result


def lock_tables(conn, tables):
    # Compatible with pg_dump's ACCESS SHARE lock, but exclude concurrent writers.
    for table in sorted(tables):
        conn.execute(SQL("lock table public.{} in share row exclusive mode").format(
            Identifier(table)
        ))
    conn.execute("lock table supabase_migrations.schema_migrations in exclusive mode")


def snapshot(conn, columns):
    result = {}
    for table, names in columns.items():
        rows = conn.execute(SQL("select {} from public.{}").format(
            SQL(",").join(map(Identifier, names)), Identifier(table)
        )).fetchall()
        result[table] = {"count": len(rows), "sha256": digest(sorted(map(digest, rows)))}
    return result


def public_snapshot(conn):
    result = {}
    for role in ("anon", "authenticated"):
        conn.execute(SQL("set local role {}").format(Identifier(role)))
        try:
            result[role] = {}
            for view in VIEWS:
                rows = conn.execute(SQL("select * from public.{}").format(
                    Identifier(view)
                )).fetchall()
                result[role][view] = digest(sorted(map(digest, rows)))
        finally:
            conn.execute("reset role")
    return result


def verify_permissions(conn):
    for table in PRIVATE:
        rls = conn.execute("select relrowsecurity from pg_class where oid=%s::regclass", (
            "public." + table,
        )).fetchone()[0]
        if not rls:
            raise UpgradeError("private_table_rls_missing")
        for role in ("anon", "authenticated"):
            privilege = conn.execute(
                "select has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE'), "
                "has_any_column_privilege(%s,%s,'SELECT,INSERT,UPDATE')",
                (role, "public." + table, role, "public." + table),
            ).fetchone()
            if any(privilege):
                raise UpgradeError("private_table_exposed")
    for role in ("anon", "authenticated"):
        if conn.execute(
            "select has_column_privilege(%s,'public.notice_easy_texts',"
            "'dictionary_candidates','SELECT')", (role,),
        ).fetchone()[0]:
            raise UpgradeError("private_candidates_exposed")
        if not conn.execute(
            "select has_function_privilege(%s,'public.get_notice_dictionary(bigint)','EXECUTE')",
            (role,),
        ).fetchone()[0]:
            raise UpgradeError("dictionary_rpc_not_readable")
    for table in PRIVATE:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            if not conn.execute("select has_table_privilege('service_role',%s,%s)", (
                "public." + table, privilege,
            )).fetchone()[0]:
                raise UpgradeError("service_role_privilege_missing")


def pg_environment(dsn):
    params = conninfo_to_dict(dsn)
    env = os.environ.copy()
    for key in ("host", "port", "user", "password", "dbname", "sslmode"):
        if key in params:
            env["PG" + ("DATABASE" if key == "dbname" else key.upper())] = params[key]
    return env


def backup(conn, dsn, target):
    versions = inspect(conn)
    columns = layout(conn)
    lock_tables(conn, columns)
    before = snapshot(conn, columns)
    public = public_snapshot(conn)
    proc = subprocess.run(
        [os.environ.get("PG_DUMP", "pg_dump"), "--format=custom", "--no-owner",
         "--schema=public", "--schema=supabase_migrations", "--lock-wait-timeout=10s"],
        env=pg_environment(dsn), capture_output=True, timeout=120, check=False,
    )
    if proc.returncode or not proc.stdout.startswith(b"PGDMP"):
        raise UpgradeError("backup_failed")
    payload = {
        "versions": versions, "columns": columns, "rows": before, "public": public,
        "migration_hashes": {v: digest(p.read_text("utf-8"))
                             for v, p in migration_files().items()},
        "dump": base64.b64encode(proc.stdout).decode(),
    }
    key = bytes.fromhex(os.environ["MIGRATION_BACKUP_KEY"])
    nonce = os.urandom(12)
    target.write_bytes(MAGIC + nonce + AESGCM(key).encrypt(
        nonce, json.dumps(payload).encode(), MAGIC
    ))
    return {"versions": versions, "row_counts": {t: r["count"] for t, r in before.items()},
            "backup_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


def read_backup(target):
    data = target.read_bytes()
    if not data.startswith(MAGIC):
        raise UpgradeError("invalid_backup")
    body = data[len(MAGIC):]
    return json.loads(AESGCM(bytes.fromhex(os.environ["MIGRATION_BACKUP_KEY"])).decrypt(
        body[:12], body[12:], MAGIC
    ))


def apply(conn, saved):
    paths = migration_files()
    versions = inspect(conn)
    columns = layout(conn)
    lock_tables(conn, columns)
    if (list(versions) != list(saved["versions"]) or columns != saved["columns"]
            or snapshot(conn, columns) != saved["rows"]
            or public_snapshot(conn) != saved["public"]):
        raise UpgradeError("database_changed_since_backup")
    if {v: digest(p.read_text("utf-8")) for v, p in paths.items()} != saved["migration_hashes"]:
        raise UpgradeError("migration_changed_since_backup")
    applied = []
    for version in UPGRADES:
        if version in versions:
            continue
        path = paths[version]
        sql = path.read_text("utf-8")
        conn.execute(sql)
        conn.execute(
            "insert into supabase_migrations.schema_migrations(version,name,statements) "
            "values (%s,%s,%s)", (version, path.stem.split("_", 1)[1], [sql]),
        )
        applied.append(version)
    if snapshot(conn, columns) != saved["rows"] or public_snapshot(conn) != saved["public"]:
        raise UpgradeError("existing_data_or_public_results_changed")
    if inspect(conn) != BASE + UPGRADES:
        raise UpgradeError("post_upgrade_history_mismatch")
    verify_permissions(conn)
    return {"applied": applied, "data_preserved": True, "public_reads_preserved": True,
            "private_permissions_verified": True}


def restore_check(saved):
    """Restore backup contents only into an explicitly disposable local database."""
    dsn = os.environ["RESTORE_DATABASE_URL"]
    params = conninfo_to_dict(dsn)
    if (params.get("host") != "127.0.0.1"
            or not params.get("dbname", "").startswith("pipeline_schema_test_")):
        raise UpgradeError("restore_requires_disposable_local_database")
    with psycopg.connect(dsn, autocommit=True) as conn:
        if conn.execute("select to_regclass('public.notices')").fetchone()[0] is not None:
            raise UpgradeError("restore_database_not_empty")
        for role in ("anon", "authenticated", "service_role"):
            if not conn.execute("select 1 from pg_roles where rolname=%s", (role,)).fetchone():
                conn.execute(SQL("create role {} nologin").format(Identifier(role)))
    result = subprocess.run(
        [os.environ.get("PG_RESTORE", "pg_restore"), "--no-owner", "--no-privileges",
         "--exit-on-error", "--clean", "--if-exists", "--dbname", params["dbname"]],
        input=base64.b64decode(saved["dump"], validate=True),
        env=pg_environment(dsn), capture_output=True, timeout=120, check=False,
    )
    if result.returncode:
        raise UpgradeError("backup_restore_failed")
    with psycopg.connect(dsn) as conn:
        if snapshot(conn, saved["columns"]) != saved["rows"]:
            raise UpgradeError("restored_backup_data_mismatch")
    return {"backup_restore_verified": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "restore-check", "apply"))
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode == "restore-check":
            print(json.dumps(restore_check(read_backup(args.backup))))
            return 0
        dsn = os.environ["DATABASE_URL"]
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            # Explicit transaction settings also work through the Supabase pooler.
            conn.execute("set local lock_timeout='10s'")
            conn.execute("set local statement_timeout='180s'")
            conn.execute("select pg_advisory_xact_lock(760076)")
            result = (backup(conn, dsn, args.backup) if args.mode == "prepare"
                      else apply(conn, read_backup(args.backup)))
        print(json.dumps(result))  # Only report success after COMMIT.
    except Exception as error:
        code = str(error) if isinstance(error, UpgradeError) else type(error).__name__
        print(json.dumps({"complete": False, "reason": code}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
