"""Prepare separate disposable PostgreSQL databases for the CI test suites."""

import os
import sys
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.sql import SQL, Identifier

PIPELINE_DATABASE = "pipeline_schema_test_ci"
SCHEMA_DATABASE = "pipeline_schema_test_empty_ci"
TEST_ROLES = ("anon", "authenticated", "service_role")
MIGRATION_DIRECTORY = Path(__file__).resolve().parents[3] / "supabase" / "migrations"


class TestDatabasePreparationError(ValueError):
    """A fixed preparation code, without connection strings or bound data."""


def _connection_parameters(admin_url: str) -> dict[str, str]:
    if not admin_url:
        raise TestDatabasePreparationError("missing_ci_test_database_admin_url")
    try:
        parameters = conninfo_to_dict(admin_url)
    except psycopg.Error:
        raise TestDatabasePreparationError("invalid_test_database_admin_url") from None
    if (
        parameters.get("host") != "127.0.0.1"
        or parameters.get("hostaddr", "127.0.0.1") != "127.0.0.1"
        or parameters.get("dbname") != "postgres"
        or "service" in parameters
    ):
        raise TestDatabasePreparationError("disposable_loopback_admin_required")
    # Prevent inherited libpq environment settings from changing the target.
    return parameters | {"hostaddr": "127.0.0.1", "connect_timeout": "5"}


def _ensure_plain_roles(conn: psycopg.Connection) -> None:
    for role in TEST_ROLES:
        flags = conn.execute(
            "select rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,rolreplication,rolcanlogin "
            "from pg_roles where rolname=%s", (role,),
        ).fetchone()
        if flags is None:
            conn.execute(SQL(
                "create role {} nologin nosuperuser nocreatedb nocreaterole "
                "noreplication nobypassrls"
            ).format(Identifier(role)))
            continue
        membership = conn.execute(
            "select count(*) from pg_auth_members "
            "where member=(select oid from pg_roles where rolname=%s)", (role,),
        ).fetchone()
        if flags != (False,) * 6 or membership != (0,):
            raise TestDatabasePreparationError("existing_test_role_has_privileges")


def prepare_test_databases(
    admin_url: str, *, migration_directory: Path = MIGRATION_DIRECTORY,
) -> int:
    """Create fresh test databases, never reset an existing database.

    Pipeline integration tests need committed migrations. Schema tests instead
    apply migrations and seed in their own rollback-only empty database.
    """
    parameters = _connection_parameters(admin_url)
    migrations = sorted(migration_directory.glob("*.sql"))
    if not migrations:
        raise TestDatabasePreparationError("missing_test_migrations")
    with psycopg.connect(**parameters, autocommit=True) as conn:
        existing = conn.execute(
            "select datname from pg_database where datname in (%s,%s)",
            (PIPELINE_DATABASE, SCHEMA_DATABASE),
        ).fetchall()
        if existing:
            raise TestDatabasePreparationError("test_database_already_exists")
        _ensure_plain_roles(conn)
        for database in (PIPELINE_DATABASE, SCHEMA_DATABASE):
            conn.execute(SQL("create database {}").format(Identifier(database)))
    with psycopg.connect(**(parameters | {"dbname": PIPELINE_DATABASE})) as conn:
        conn.execute("grant usage on schema public to anon, authenticated, service_role")
        for migration in migrations:
            conn.execute(migration.read_text(encoding="utf-8"))
    return len(migrations)


def main() -> int:
    try:
        count = prepare_test_databases(os.environ.get("CI_TEST_DATABASE_ADMIN_URL", ""))
    except TestDatabasePreparationError as error:
        print(f"Test database preparation failed: {error}", file=sys.stderr)
        return 1
    except (psycopg.Error, OSError):
        print("Test database preparation failed: database_preparation_failed", file=sys.stderr)
        return 1
    print(
        f"Prepared pipeline database with {count} migrations and a separate empty schema database."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
