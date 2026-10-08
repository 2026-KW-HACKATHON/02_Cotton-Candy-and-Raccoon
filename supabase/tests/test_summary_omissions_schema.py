"""Only safe omission fields are projected; absent content never exposes links."""

import pytest
from psycopg.types.json import Jsonb


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_omission_column_is_public_read_only_and_manifest_stays_private(database, role):
    assert database.execute(
        "select has_column_privilege(%s,'notice_summaries','preparation_omissions','SELECT'), "
        "has_column_privilege(%s,'notice_summaries','preparation_omissions','UPDATE'), "
        "has_column_privilege(%s,'notice_summaries','file_manifest','SELECT')",
        (role, role, role),
    ).fetchone() == (True, False, False)
    assert database.execute(
        "select is_generated from information_schema.columns "
        "where table_schema='public' and table_name='notice_summaries' "
        "and column_name='preparation_omissions'",
    ).fetchone() == ("ALWAYS",)


def test_projection_whitelists_fields_and_handles_legacy_or_invalidated_content(database):
    public = {"notice_file_id": 1, "url": "https://www.nowon.kr/file", "reason_code": "timeout"}
    manifest = {"omissions": [public | {"file_key": "secret", "internal_error": "secret"}]}
    assert database.execute(
        "select public.summary_preparation_omissions(%s,%s)",
        (Jsonb({}), Jsonb(manifest)),
    ).fetchone()[0] == [public]
    assert database.execute(
        "select public.summary_preparation_omissions(null,%s)", (Jsonb(manifest),),
    ).fetchone()[0] is None
    assert database.execute(
        "select public.summary_preparation_omissions(%s,%s)", (Jsonb({}), Jsonb({})),
    ).fetchone()[0] == []
