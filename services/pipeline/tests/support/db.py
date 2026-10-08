"""Disposable PostgreSQL databases that one test creates, migrates, and drops."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from support.paths import REPO_ROOT

__all__ = [
    "TEST_ROLES",
    "owned_migrated_database",
]

TEST_ROLES = ("anon", "authenticated", "service_role")
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"


@contextmanager
def owned_migrated_database(
    *,
    env: str,
    required_prefix: str,
    missing: Literal["skip", "fail"],
    missing_message: str,
    invalid_message: str,
) -> Iterator[dict[str, str]]:
    """Create a database named ``<required_prefix>auto_<uuid>`` and apply all migrations.

    ``env`` names a loopback URL whose database name starts with ``required_prefix``.
    Only its server is used: the named database is never written to. Yields libpq
    connection parameters for the new database, then drops it with any test roles
    this call created. Committed fixtures therefore never reach a shared database.
    """
    database_url = os.getenv(env)
    if not database_url:
        if missing == "skip":
            pytest.skip(missing_message)
        pytest.fail(missing_message)
    info = conninfo_to_dict(database_url)
    if (
        info.get("host") not in {"localhost", "127.0.0.1", "::1"}
        or info.get("hostaddr") not in {None, "127.0.0.1", "::1"}
        or not info.get("dbname", "").startswith(required_prefix)
    ):
        raise ValueError(invalid_message)
    database_name = f"{required_prefix}auto_{uuid4().hex}"
    admin = psycopg.connect(**{**info, "dbname": "postgres"}, autocommit=True)
    created_database = False
    created_roles: list[str] = []
    try:
        admin.execute(sql.SQL("create database {}").format(sql.Identifier(database_name)))
        created_database = True
        for role in TEST_ROLES:
            flags = admin.execute(
                "select rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,rolreplication,rolcanlogin "
                "from pg_roles where rolname = %s", (role,),
            ).fetchone()
            if flags is None:
                admin.execute(sql.SQL("create role {} nologin").format(sql.Identifier(role)))
                created_roles.append(role)
            else:
                assert flags == (False,) * 6, "Existing test role has elevated flags"
                assert admin.execute(
                    "select count(*) from pg_auth_members "
                    "where member=(select oid from pg_roles where rolname=%s)", (role,),
                ).fetchone() == (0,), "Existing test role inherits other privileges"
        test_info = {**info, "dbname": database_name}
        with psycopg.connect(**test_info) as setup:
            setup.execute("grant usage on schema public to anon, authenticated, service_role")
            for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
                setup.execute(migration.read_text("utf-8"))
        yield test_info
    finally:
        try:
            if created_database:
                admin.execute(
                    sql.SQL("drop database {} with (force)").format(sql.Identifier(database_name))
                )
            for role in reversed(created_roles):
                admin.execute(sql.SQL("drop role {}").format(sql.Identifier(role)))
        finally:
            admin.close()
