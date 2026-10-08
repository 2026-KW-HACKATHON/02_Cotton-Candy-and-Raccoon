"""Candidate JSON integrity, provenance invalidation, and body-only app reads."""

from datetime import UTC, datetime

import psycopg
import pytest
from psycopg.types.json import Jsonb
from support.paths import REPO_ROOT

CANDIDATE_MIGRATION = "20261008170000_notice_dictionary_candidates.sql"

TITLE = "😀 안내"
BODY = "구비서류를 지참하세요."
ORIGINAL = TITLE + "\n" + BODY
CANDIDATE = {
    "original": "구비서류를",
    "query_word": "구비서류",
    "context": BODY,
    "start": len(TITLE) + 1,
    "end": len(TITLE) + 1 + len("구비서류를"),
}


@pytest.fixture
def candidate_row(db):
    notice_id = db.execute(
        "insert into notices(category,source_board,post_sn,title,body_html,registered_on,url) "
        "values ('nowon','1001','dictionary-candidates',%s,%s,current_date,"
        "'https://www.nowon.kr/test/dictionary-candidates') returning id",
        (TITLE, BODY),
    ).fetchone()[0]
    db.execute(
        "insert into notice_easy_texts "
        "(notice_id,notice_revision,source_hash,original_text,easy_text,changes,model,"
        "prompt_version,attempt_count,generated_at) "
        "values (%s,notice_easy_text_revision(%s,%s),encode(sha256(convert_to(%s,'UTF8')),"
        "'hex'),%s,%s,'[]','test','easy-language-v8',1,'2026-10-08T00:00:00Z')",
        (notice_id, TITLE, BODY, ORIGINAL, ORIGINAL, ORIGINAL),
    )
    return notice_id


@pytest.mark.parametrize("database", [CANDIDATE_MIGRATION], indirect=True)
def test_candidate_migration_preserves_existing_result_as_unprocessed(db, candidate_row):
    db.execute(
        "update notice_easy_texts set prompt_version='easy-language-v7' where notice_id=%s",
        (candidate_row,),
    )
    before = db.execute(
        "select to_jsonb(result) from notice_easy_texts result where notice_id=%s",
        (candidate_row,),
    ).fetchone()[0]
    assert "dictionary_candidates" not in before

    db.execute((REPO_ROOT / "supabase/migrations" / CANDIDATE_MIGRATION).read_text("utf-8"))

    after = db.execute(
        "select to_jsonb(result) from notice_easy_texts result where notice_id=%s",
        (candidate_row,),
    ).fetchone()[0]
    assert after == {**before, "dictionary_candidates": None}
    with db.transaction():
        db.execute("set local role anon")
        assert db.execute(
            "select original_text,easy_text,dictionary_candidates "
            "from notice_easy_texts where notice_id=%s",
            (candidate_row,),
        ).fetchone() == (ORIGINAL, ORIGINAL, None)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_dictionary_candidates_are_public_read_only(db, role):
    assert db.execute(
        "select has_column_privilege(%s,'notice_easy_texts','dictionary_candidates','SELECT'), "
        "has_column_privilege(%s,'notice_easy_texts','dictionary_candidates','UPDATE')",
        (role, role),
    ).fetchone() == (True, False)


@pytest.mark.parametrize("candidates", [None, [], [CANDIDATE]])
def test_candidates_distinguish_unprocessed_empty_and_populated(db, candidate_row, candidates):
    if candidates is not None:
        db.execute(
            "update notice_easy_texts set dictionary_candidates=%s where notice_id=%s",
            (Jsonb(candidates), candidate_row),
        )
    assert db.execute(
        "select dictionary_candidates from notice_easy_texts where notice_id=%s",
        (candidate_row,),
    ).fetchone() == (candidates,)
    with db.transaction():
        db.execute("set local role anon")
        assert db.execute(
            "select dictionary_candidates from notice_easy_texts where notice_id=%s",
            (candidate_row,),
        ).fetchone() == (candidates,)


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        {},
        [CANDIDATE | {"definition": "unexpected lookup result"}],
        [CANDIDATE | {"query_word": ""}],
        [CANDIDATE | {"query_word": "가" * 101}],
        [CANDIDATE | {"start": True}],
        [CANDIDATE | {"start": 1.5}],
        [CANDIDATE | {"start": -1}],
        [CANDIDATE | {"end": 10**100}],
        [CANDIDATE | {"original": "다른 말"}],
        [CANDIDATE | {"context": "구비서류를 새로 제출하세요."}],
        [CANDIDATE, CANDIDATE],
    ],
    ids=[
        "json-null", "object", "extra-key", "empty-query", "long-query", "boolean-offset",
        "fractional-offset", "negative-offset", "huge-offset", "wrong-span", "wrong-context",
        "overlapping-candidates",
    ],
)
def test_candidate_constraint_rejects_malformed_or_unattested_payload(db, candidate_row, invalid):
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute(
            "update notice_easy_texts set dictionary_candidates=%s where notice_id=%s",
            (Jsonb(invalid), candidate_row),
        )


@pytest.mark.parametrize("candidates", [[], [CANDIDATE]], ids=["empty", "populated"])
@pytest.mark.parametrize(
    "assignment,parameters",
    [
        ("notice_revision=repeat('a',64)", ()),
        ("model='old-worker-model'", ()),
        ("prompt_version='easy-language-v7'", ()),
        ("attempt_count=2", ()),
        ("generated_at=%s", (datetime(2026, 10, 9, tzinfo=UTC),)),
        ("easy_text=easy_text || ' '", ()),
        ("changes=%s", (Jsonb([{"start": len(TITLE) + 1}]),)),
        (
            "original_text=%s,easy_text=%s,source_hash="
            "encode(sha256(convert_to(%s,'UTF8')),'hex')",
            (TITLE + "\n새 원문",) * 3,
        ),
    ],
    ids=["revision", "model", "prompt", "attempt", "time", "easy-text", "changes", "source"],
)
def test_old_worker_cannot_inherit_candidates_for_a_changed_result(
    db, candidate_row, candidates, assignment, parameters
):
    db.execute(
        "update notice_easy_texts set dictionary_candidates=%s where notice_id=%s",
        (Jsonb(candidates), candidate_row),
    )

    db.execute(
        f"update notice_easy_texts set {assignment} where notice_id=%s",
        (*parameters, candidate_row),
    )

    assert db.execute(
        "select dictionary_candidates from notice_easy_texts where notice_id=%s",
        (candidate_row,),
    ).fetchone() == (None,)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_app_read_policy_excludes_candidates_from_title(db, candidate_row, role):
    candidate = {
        "original": "안내", "query_word": "안내", "context": TITLE,
        "start": 2, "end": len(TITLE),
    }
    db.execute(
        "update notice_easy_texts set dictionary_candidates=%s where notice_id=%s",
        (Jsonb([candidate]), candidate_row),
    )
    with db.transaction():
        db.execute(f"set local role {role}")
        assert db.execute(
            "select count(*) from notice_easy_texts where notice_id=%s", (candidate_row,),
        ).fetchone() == (0,)
