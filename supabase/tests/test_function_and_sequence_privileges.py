"""Backend-only functions and sequences stay closed to the app under Supabase defaults.

Supabase grants EXECUTE on new functions and all privileges on new sequences to
anon and authenticated explicitly, so revoking from PUBLIC alone is not enough.
The conftest reproduces those default privileges.
"""

import psycopg
import pytest

APP_ROLES = ("anon", "authenticated")

BACKEND_ONLY_FUNCTIONS = (
    "public.invalidate_summary_on_source_change()",
    "public.invalidate_summary_on_source_file_change()",
    "public.summary_file_references(jsonb, jsonb)",
    "public.summary_preparation_omissions(jsonb, jsonb)",
    "public.summary_information_loss(jsonb, jsonb, date, date)",
)
SERVICE_FUNCTIONS = (
    "public.summary_file_references(jsonb, jsonb)",
    "public.summary_preparation_omissions(jsonb, jsonb)",
    "public.summary_information_loss(jsonb, jsonb, date, date)",
)
# The easy-text read policy calls these with the requesting role's privileges.
POLICY_FUNCTIONS = (
    "public.notice_easy_text_revision(text, text)",
    "public.notice_easy_text_preserves_title(text, text, text, jsonb)",
)
BACKEND_SEQUENCES = (
    "public.notices_id_seq",
    "public.notice_files_id_seq",
    "public.notice_summary_execution_token_seq",
)


def _can_execute(db: psycopg.Connection, role: str, function: str) -> bool:
    return db.execute(
        "select has_function_privilege(%s, %s, 'execute')", (role, function)
    ).fetchone()[0]


@pytest.mark.parametrize("role", APP_ROLES)
@pytest.mark.parametrize("function", BACKEND_ONLY_FUNCTIONS)
def test_app_roles_cannot_execute_backend_functions(
    db: psycopg.Connection, role: str, function: str
) -> None:
    assert _can_execute(db, role, function) is False


@pytest.mark.parametrize("function", SERVICE_FUNCTIONS)
def test_service_role_executes_summary_functions(db: psycopg.Connection, function: str) -> None:
    assert _can_execute(db, "service_role", function) is True


@pytest.mark.parametrize("role", APP_ROLES)
@pytest.mark.parametrize("function", POLICY_FUNCTIONS)
def test_app_roles_keep_easy_text_policy_functions(
    db: psycopg.Connection, role: str, function: str
) -> None:
    assert _can_execute(db, role, function) is True


@pytest.mark.parametrize("role", APP_ROLES)
def test_app_role_rpc_call_of_backend_function_is_denied(
    db: psycopg.Connection, role: str
) -> None:
    db.execute("set local role " + role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
        db.execute("select public.summary_file_references('{}'::jsonb, '{}'::jsonb)")


@pytest.mark.parametrize("role", APP_ROLES)
@pytest.mark.parametrize("sequence", BACKEND_SEQUENCES)
@pytest.mark.parametrize("privilege", ["usage", "select", "update"])
def test_app_roles_have_no_sequence_privileges(
    db: psycopg.Connection, role: str, sequence: str, privilege: str
) -> None:
    assert db.execute(
        "select has_sequence_privilege(%s, %s, %s)", (role, sequence, privilege)
    ).fetchone() == (False,)


@pytest.mark.parametrize("sequence", BACKEND_SEQUENCES)
def test_service_role_uses_backend_sequences(db: psycopg.Connection, sequence: str) -> None:
    assert db.execute(
        "select has_sequence_privilege('service_role', %s, 'usage')", (sequence,)
    ).fetchone() == (True,)
