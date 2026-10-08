"""Validate summary/review constraints, incremental migrations, and app RLS."""

from copy import deepcopy
from datetime import UTC, date, datetime

import psycopg
import pytest
from psycopg.types.json import Jsonb

from pipeline.storage.summary_record import FAILURE_CODES
from pipeline.transform.summary_schema import NoticeSummary

PUBLIC_COLUMNS = (
    "notice_id",
    "status",
    "category",
    "category_code",
    "deadline_on",
    "result",
    "card_summaries",
    "attachment_status",
    "generated_at",
)
PRIVATE_COLUMNS = (
    "source_hash",
    "model",
    "prompt_version",
    "attempt_count",
    "last_error_code",
    "updated_at",
)
SUBJECT_CODES = (21, 22, 23, 24, 25, 26, 27, 30)
CARD_SLOTS = ("audience", "deadline", "action", "notes")


def insert_notice(
    db: psycopg.Connection, *, post: str = "summary-test", visible: bool = True
) -> int:
    return db.execute(
        "insert into notices(category,source_board,post_sn,title,registered_on,url,is_visible) "
        "values ('nowon','1001',%s,'test',current_date,'https://www.nowon.kr/test',%s) "
        "returning id",
        (post, visible),
    ).fetchone()[0]


def summary_json(db: psycopg.Connection, *, code: int = 27) -> dict:
    result = db.execute(
        "select result from notice_summaries where status='summarized' limit 1"
    ).fetchone()[0]
    result["category_code"] = code
    return result


def insert_summary(db: psycopg.Connection, notice_id: int, **overrides: object) -> None:
    values = {
        "notice_id": notice_id,
        "status": "summarized",
        "result": summary_json(db),
        "category": "living",
        "category_code": 27,
        "deadline_on": None,
        "attachment_status": "none",
        "source_hash": "c" * 64,
        "model": "gemini-2.5-flash",
        "prompt_version": "test-v2",
        "attempt_count": 1,
        "last_error_code": None,
        "generated_at": datetime(2026, 10, 6, tzinfo=UTC),
    }
    values.update(overrides)
    if values["result"] is not None:
        values["result"] = Jsonb(values["result"])
    columns = ",".join(values)
    db.execute(
        f"insert into notice_summaries ({columns}) values ({','.join(['%s'] * len(values))})",
        tuple(values.values()),
    )


@pytest.mark.parametrize("status", ["summarized", "needs_review"])
@pytest.mark.parametrize(
    "cards",
    [
        dict.fromkeys(CARD_SLOTS),
        {"audience": "주민", "deadline": "10월 31일까지", "action": "신청", "notes": "무료"},
        {"audience": " 주민 ", "deadline": None, "action": "방문 신청", "notes": None},
        {"audience": None, "deadline": None, "action": None, "notes": "안내 " * 80},
    ],
)
def test_card_summaries_generated_without_changing_original_result(
    db: psycopg.Connection,
    status: str,
    cards: dict,
) -> None:
    key = insert_notice(db)
    result = summary_json(db)
    result["card_summaries"] = cards
    insert_summary(db, key, status=status, result=result)
    assert db.execute(
        "select result,card_summaries from notice_summaries where notice_id=%s", (key,)
    ).fetchone() == (result, cards)


@pytest.mark.parametrize("status", ["summarized", "needs_review"])
@pytest.mark.parametrize("explicit_null", [False, True])
def test_legacy_missing_or_null_cards_remain_sql_null(
    db: psycopg.Connection,
    status: str,
    explicit_null: bool,
) -> None:
    key = insert_notice(db)
    result = summary_json(db)
    result.pop("card_summaries", None)
    if explicit_null:
        result["card_summaries"] = None
    insert_summary(db, key, status=status, result=result)
    assert db.execute(
        "select result,card_summaries from notice_summaries where notice_id=%s", (key,)
    ).fetchone() == (result, None)


@pytest.mark.parametrize("status", ["pending", "needs_review", "failed"])
def test_rows_without_result_have_no_generated_cards(db: psycopg.Connection, status: str) -> None:
    key = insert_notice(db)
    insert_summary(
        db,
        key,
        status=status,
        result=None,
        category=None,
        category_code=None,
        generated_at=datetime(2026, 10, 6, tzinfo=UTC) if status == "needs_review" else None,
        last_error_code="api_timeout" if status == "failed" else None,
    )
    assert db.execute(
        "select result,card_summaries from notice_summaries where notice_id=%s", (key,)
    ).fetchone() == (None, None)


def test_card_column_is_stored_generated_and_cannot_be_written(db: psycopg.Connection) -> None:
    generated, expression, data_type = db.execute(
        "select is_generated,generation_expression,data_type from information_schema.columns "
        "where table_schema='public' and table_name='notice_summaries' "
        "and column_name='card_summaries'"
    ).fetchone()
    assert generated == "ALWAYS"
    assert "NULLIF" in expression.upper()
    assert "result" in expression and "card_summaries" in expression
    assert data_type == "jsonb"
    assert db.execute(
        "select attgenerated from pg_attribute "
        "where attrelid='notice_summaries'::regclass and attname='card_summaries'"
    ).fetchone() == ("s",)
    key = insert_notice(db)
    insert_summary(db, key)
    with pytest.raises(psycopg.errors.GeneratedAlways):
        db.execute(
            "update notice_summaries set card_summaries=%s where notice_id=%s",
            (Jsonb(dict.fromkeys(CARD_SLOTS)), key),
        )


def test_generated_cards_follow_result_updates_and_changed_source_invalidation(
    db: psycopg.Connection,
) -> None:
    key = insert_notice(db)
    result = summary_json(db)
    result["card_summaries"] = dict.fromkeys(CARD_SLOTS)
    insert_summary(db, key, result=result)
    cards = {"audience": "주민", "deadline": None, "action": "신청", "notes": "신분증 지참"}
    result["card_summaries"] = cards
    db.execute("update notice_summaries set result=%s where notice_id=%s", (Jsonb(result), key))
    assert db.execute(
        "select result,card_summaries from notice_summaries where notice_id=%s", (key,)
    ).fetchone() == (result, cards)
    db.execute(
        "update notice_summaries set status='needs_review',result=null,category=null,"
        "category_code=null,deadline_on=null,last_error_code='api_timeout' where notice_id=%s",
        (key,),
    )
    assert db.execute(
        "select result,card_summaries from notice_summaries where notice_id=%s", (key,)
    ).fetchone() == (None, None)


@pytest.mark.parametrize("status", ["summarized", "needs_review"])
@pytest.mark.parametrize("cards", [[], "", "text", 27, 27.5, True])
def test_non_object_card_summaries_rejected(
    db: psycopg.Connection,
    status: str,
    cards: object,
) -> None:
    key = insert_notice(db)
    result = summary_json(db)
    result["card_summaries"] = cards
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, status=status, result=result)


@pytest.mark.parametrize("status", ["summarized", "needs_review"])
@pytest.mark.parametrize("slot", CARD_SLOTS)
def test_missing_card_slot_rejected_even_when_other_slots_are_null(
    db: psycopg.Connection,
    status: str,
    slot: str,
) -> None:
    key = insert_notice(db)
    cards = dict.fromkeys(CARD_SLOTS)
    del cards[slot]
    result = summary_json(db)
    result["card_summaries"] = cards
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, status=status, result=result)


@pytest.mark.parametrize("status", ["summarized", "needs_review"])
@pytest.mark.parametrize("cards", [{}, {**dict.fromkeys(CARD_SLOTS), "extra": None}])
def test_empty_or_extra_card_keys_rejected(
    db: psycopg.Connection,
    status: str,
    cards: dict,
) -> None:
    key = insert_notice(db)
    result = summary_json(db)
    result["card_summaries"] = cards
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, status=status, result=result)


@pytest.mark.parametrize("status", ["summarized", "needs_review"])
@pytest.mark.parametrize("slot", CARD_SLOTS)
@pytest.mark.parametrize(
    "value",
    [
        "", " ", "\t", "\v", "\f", "\n", "\r", "\r\n",
        "\x1c\x1d\x1e\x1f", "\u0085", "\u00a0", "\u1680", "\u2000\u2001\u2002\u2003",
        "\u2004\u2005\u2006\u2007\u2008\u2009\u200a", "\u2028\u2029", "\u202f", "\u205f",
        "\u3000", " \t\u00a0\u2003\u3000 ", "주민\n신청", "주민\r신청", "신청\r\n",
        27, 27.5, True, [], {},
    ],
)
def test_non_string_blank_or_multiline_card_values_rejected(
    db: psycopg.Connection,
    status: str,
    slot: str,
    value: object,
) -> None:
    key = insert_notice(db)
    cards = dict.fromkeys(CARD_SLOTS)
    cards[slot] = value
    result = summary_json(db)
    result["card_summaries"] = cards
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, status=status, result=result)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_app_can_read_generated_cards_only_for_visible_notices(
    db: psycopg.Connection,
    role: str,
) -> None:
    visible_key = insert_notice(db, post="card-visible")
    hidden_key = insert_notice(db, post="card-hidden", visible=False)
    result = summary_json(db)
    cards = {"audience": "주민", "deadline": None, "action": "신청", "notes": None}
    result["card_summaries"] = cards
    insert_summary(db, visible_key, status="needs_review", result=result)
    insert_summary(db, hidden_key, result=result)
    db.execute("set local role " + role)
    assert db.execute(
        "select notice_id,status,result,card_summaries from notice_summaries "
        "where notice_id in (%s,%s)",
        (visible_key, hidden_key),
    ).fetchall() == [(visible_key, "needs_review", result, cards)]


def test_seed_four_states_and_full_valid_summary(db: psycopg.Connection) -> None:
    assert set(db.execute("select distinct status from notice_summaries").fetchall()) == {
        ("pending",),
        ("summarized",),
        ("needs_review",),
        ("failed",),
    }
    assert db.execute(
        "select attachment_status,result,category,category_code,deadline_on "
        "from notice_summaries where status='needs_review'"
    ).fetchone() == ("partial", None, None, None, None)
    for (result,) in db.execute("select result from notice_summaries where result is not null"):
        assert NoticeSummary.model_validate(result).category_code == 27
    assert (
        db.execute(
            "select count(*) from notice_summaries s join notices n on n.id=s.notice_id "
            "where not n.is_visible"
        ).fetchone()[0]
        == 1
    )


def test_new_notice_content_timestamp_default_and_not_null(db: psycopg.Connection) -> None:
    key = insert_notice(db)
    assert db.execute(
        "select content_updated_at is not null,content_updated_at=created_at "
        "from notices where id=%s",
        (key,),
    ).fetchone() == (True, True)
    with pytest.raises(psycopg.errors.NotNullViolation):
        db.execute("update notices set content_updated_at=null where id=%s", (key,))


@pytest.mark.parametrize("code", SUBJECT_CODES)
def test_eight_subject_codes_stored_as_json_integers(db: psycopg.Connection, code: int) -> None:
    key = insert_notice(db)
    insert_summary(db, key, result=summary_json(db, code=code), category_code=code)
    assert db.execute(
        "select category_code,jsonb_typeof(result->'category_code'),result->>'category_code' "
        "from notice_summaries where notice_id=%s",
        (key,),
    ).fetchone() == (code, "number", str(code))


@pytest.mark.parametrize("result_code", ["27", "복지", 99, 27.0, 27.5, None, True])
def test_invalid_json_subject_code_rejected(
    db: psycopg.Connection,
    result_code: object,
) -> None:
    key = insert_notice(db)
    result = summary_json(db)
    result["category_code"] = result_code
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, result=result)


@pytest.mark.parametrize("json_category", [None, 27, True, "event", "unknown"])
def test_invalid_or_mismatched_json_category_rejected(
    db: psycopg.Connection,
    json_category: object,
) -> None:
    key = insert_notice(db)
    result = summary_json(db)
    result["category"] = json_category
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, result=result)


@pytest.mark.parametrize("value", [None, [], "text", 27, {}])
def test_non_object_or_missing_summary_fields_rejected(
    db: psycopg.Connection,
    value: object,
) -> None:
    key = insert_notice(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, result=value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"category_code": None},
        {"category_code": 99},
        {"category_code": 21},
        {"category": None},
        {"category": "event"},
        {"category": "unknown"},
        {"status": "bad"},
        {"attachment_status": "bad"},
        {"generated_at": None},
        {"source_hash": "A" * 64},
        {"source_hash": "a" * 63},
        {"source_hash": None},
        {"model": " model"},
        {"model": ""},
        {"model": None},
        {"prompt_version": "\nversion"},
        {"prompt_version": ""},
        {"attempt_count": -1},
        {"last_error_code": "raw exception / secret"},
    ],
)
def test_invalid_storage_contract_rejected(db: psycopg.Connection, overrides: dict) -> None:
    key = insert_notice(db)
    with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation)):
        insert_summary(db, key, **overrides)


@pytest.mark.parametrize("status", ["pending", "failed"])
@pytest.mark.parametrize("leaked_column", ["result", "category", "category_code", "deadline_on"])
def test_unpublished_states_cannot_leak_summary_fields(
    db: psycopg.Connection,
    status: str,
    leaked_column: str,
) -> None:
    key = insert_notice(db)
    values = {
        "status": status,
        "result": None,
        "category": None,
        "category_code": None,
        "deadline_on": None,
        "generated_at": None,
        "last_error_code": "api_timeout" if status == "failed" else None,
    }
    values[leaked_column] = {
        "result": summary_json(db),
        "category": "living",
        "category_code": 27,
        "deadline_on": "2026-10-31",
    }[leaked_column]
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, **values)


@pytest.mark.parametrize("category", ["living", "unknown"])
@pytest.mark.parametrize("code", [*SUBJECT_CODES, None])
def test_review_preserves_generated_result_and_available_classification(
    db: psycopg.Connection,
    category: str,
    code: int | None,
) -> None:
    key = insert_notice(db)
    result = summary_json(db)
    result.update(category=category, category_code=code)
    stored_category = None if category == "unknown" else category
    insert_summary(
        db,
        key,
        status="needs_review",
        result=result,
        category=stored_category,
        category_code=code,
        attachment_status="partial",
    )
    assert db.execute(
        "select result,category,category_code,deadline_on from notice_summaries where notice_id=%s",
        (key,),
    ).fetchone() == (result, stored_category, code, None)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_app_can_read_generated_review_content(db: psycopg.Connection, role: str) -> None:
    key = insert_notice(db)
    result = summary_json(db)
    insert_summary(db, key, status="needs_review", result=result, attachment_status="unread")
    db.execute("set local role " + role)
    assert db.execute(
        "select status,result,category,category_code,deadline_on "
        "from notice_summaries where notice_id=%s",
        (key,),
    ).fetchone() == ("needs_review", result, "living", 27, None)


@pytest.mark.parametrize("error_code", [None, "api_timeout"])
def test_review_without_result_keeps_all_public_content_null(
    db: psycopg.Connection,
    error_code: str | None,
) -> None:
    key = insert_notice(db)
    insert_summary(
        db,
        key,
        status="needs_review",
        result=None,
        category=None,
        category_code=None,
        last_error_code=error_code,
    )
    assert db.execute(
        "select result,category,category_code,deadline_on from notice_summaries where notice_id=%s",
        (key,),
    ).fetchone() == (None, None, None, None)


@pytest.mark.parametrize("result_code", ["27", "복지", 99, 27.0, 27.5, None, True])
def test_review_invalid_or_mismatched_json_subject_code_rejected(
    db: psycopg.Connection,
    result_code: object,
) -> None:
    key = insert_notice(db)
    result = summary_json(db)
    result["category_code"] = result_code
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, status="needs_review", result=result)


@pytest.mark.parametrize("json_category", [None, 27, True, "event", "unknown"])
def test_review_invalid_or_mismatched_json_category_rejected(
    db: psycopg.Connection,
    json_category: object,
) -> None:
    key = insert_notice(db)
    result = summary_json(db)
    result["category"] = json_category
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, status="needs_review", result=result)


@pytest.mark.parametrize("field", ["category", "category_code"])
@pytest.mark.parametrize("unclassified", [False, True])
def test_review_requires_explicit_json_classification_keys(
    db: psycopg.Connection,
    field: str,
    unclassified: bool,
) -> None:
    key = insert_notice(db)
    result = summary_json(db)
    if unclassified:
        result.update(category="unknown", category_code=None)
    del result[field]
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(
            db,
            key,
            status="needs_review",
            result=result,
            category=None if unclassified else "living",
            category_code=None if unclassified else 27,
        )


@pytest.mark.parametrize("value", [[], "text", 27, {}])
def test_review_non_object_or_missing_summary_fields_rejected(
    db: psycopg.Connection,
    value: object,
) -> None:
    key = insert_notice(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, status="needs_review", result=value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"category": None},
        {"category_code": None},
        {"category_code": 21},
        {"deadline_on": "2026-10-31"},
        {"result": None},
        {"category": "unknown"},
        {"generated_at": None},
    ],
)
def test_review_inconsistent_columns_or_deadline_rejected(
    db: psycopg.Connection,
    overrides: dict,
) -> None:
    key = insert_notice(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, status="needs_review", **overrides)


@pytest.mark.parametrize("result_code", ["27", 27, 27.0, True])
def test_review_null_subject_column_requires_explicit_json_null(
    db: psycopg.Connection,
    result_code: object,
) -> None:
    key = insert_notice(db)
    result = summary_json(db)
    result["category_code"] = result_code
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, status="needs_review", result=result, category_code=None)


@pytest.mark.parametrize("json_category", [None, 27, True, "living"])
def test_review_null_type_column_requires_json_unknown(
    db: psycopg.Connection,
    json_category: object,
) -> None:
    key = insert_notice(db)
    result = summary_json(db)
    result["category"] = json_category
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(db, key, status="needs_review", result=result, category=None)


def test_failed_state_requires_safe_error_and_no_generation(db: psycopg.Connection) -> None:
    key = insert_notice(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(
            db,
            key,
            status="failed",
            result=None,
            category=None,
            category_code=None,
            generated_at=None,
            last_error_code=None,
        )


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_waiting_or_failed_row_cannot_claim_generation(
    db: psycopg.Connection,
    status: str,
) -> None:
    key = insert_notice(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_summary(
            db,
            key,
            status=status,
            result=None,
            category=None,
            category_code=None,
            last_error_code="api_timeout" if status == "failed" else None,
        )


@pytest.mark.parametrize("code", sorted(FAILURE_CODES))
def test_every_python_failure_code_allowed_on_preserved_summary(
    db: psycopg.Connection,
    code: str,
) -> None:
    key = insert_notice(db)
    insert_summary(db, key, last_error_code=code)
    assert db.execute(
        "select status,last_error_code from notice_summaries where notice_id=%s", (key,)
    ).fetchone() == ("summarized", code)


def test_changed_source_review_can_retain_generation_metadata(db: psycopg.Connection) -> None:
    key = insert_notice(db)
    insert_summary(db, key)
    before = db.execute(
        "select source_hash,model,prompt_version,generated_at "
        "from notice_summaries where notice_id=%s",
        (key,),
    ).fetchone()
    db.execute(
        "update notice_summaries set status='needs_review',result=null,category=null,"
        "category_code=null,deadline_on=null,last_error_code='api_timeout' where notice_id=%s",
        (key,),
    )
    assert (
        db.execute(
            "select source_hash,model,prompt_version,generated_at "
            "from notice_summaries where notice_id=%s",
            (key,),
        ).fetchone()
        == before
    )
    assert key in {
        row[0]
        for row in db.execute(
            "select notice_id from notice_summaries "
            "where status in ('pending','failed') or last_error_code is not null"
        )
    }
    predicate = db.execute(
        "select pg_get_expr(indpred,indrelid) from pg_index "
        "where indexrelid='notice_summaries_retry_idx'::regclass"
    ).fetchone()[0]
    assert (
        "pending" in predicate
        and "failed" in predicate
        and "last_error_code IS NOT NULL" in predicate
    )


def test_one_summary_per_notice_and_delete_cascades(db: psycopg.Connection) -> None:
    key = insert_notice(db)
    insert_summary(db, key)
    db.execute("delete from notices where id=%s", (key,))
    assert (
        db.execute("select count(*) from notice_summaries where notice_id=%s", (key,)).fetchone()[0]
        == 0
    )


def test_orphan_summary_rejected(db: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        insert_summary(db, 2**63 - 1)


def test_duplicate_notice_summary_rejected(db: psycopg.Connection) -> None:
    key = insert_notice(db)
    insert_summary(db, key)
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_summary(db, key)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_app_reads_exact_public_columns_and_visible_rows(db: psycopg.Connection, role: str) -> None:
    db.execute("set local role " + role)
    rows = db.execute(f"select {','.join(PUBLIC_COLUMNS)} from notice_summaries").fetchall()
    assert len(rows) == 4
    assert {row[1] for row in rows} == {"pending", "summarized", "needs_review", "failed"}
    assert (
        db.execute("select content_updated_at from notices order by id limit 1").fetchone()[0]
        is not None
    )


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("column", [*PRIVATE_COLUMNS, "*"])
def test_app_private_metadata_and_select_star_forbidden(
    db: psycopg.Connection,
    role: str,
    column: str,
) -> None:
    db.execute("set local role " + role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute(f"select {column} from notice_summaries")


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize(
    "statement",
    [
        "insert into notice_summaries(notice_id) values (1)",
        "update notice_summaries set status='pending'",
        "update notice_summaries set card_summaries=default",
        "insert into notice_summaries(notice_id,card_summaries) values (1,default)",
        "delete from notice_summaries",
        "truncate notice_summaries",
    ],
)
def test_app_writes_forbidden(db: psycopg.Connection, role: str, statement: str) -> None:
    db.execute("set local role " + role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute(statement)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_default_table_privileges_revoked(db: psycopg.Connection, role: str) -> None:
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert (
            db.execute(
                "select has_table_privilege(%s,'notice_summaries',%s)", (role, privilege)
            ).fetchone()[0]
            is False
        )
    for column in PUBLIC_COLUMNS:
        assert (
            db.execute(
                "select has_column_privilege(%s,'notice_summaries',%s,'SELECT')", (role, column)
            ).fetchone()[0]
            is True
        )
    for column in PRIVATE_COLUMNS:
        assert (
            db.execute(
                "select has_column_privilege(%s,'notice_summaries',%s,'SELECT')", (role, column)
            ).fetchone()[0]
            is False
        )


def test_service_role_reads_hidden_metadata_and_writes(db: psycopg.Connection) -> None:
    key = insert_notice(db, visible=False)
    result = summary_json(db)
    db.execute("set local role service_role")
    assert db.execute("select count(*) from notice_summaries").fetchone()[0] == 5
    insert_summary(db, key, result=result)
    db.execute(
        "update notice_summaries set last_error_code='api_timeout' where notice_id=%s", (key,)
    )
    assert db.execute(
        "select source_hash,last_error_code from notice_summaries where notice_id=%s", (key,)
    ).fetchone() == ("c" * 64, "api_timeout")
    db.execute("delete from notice_summaries where notice_id=%s", (key,))


def _coverage_summary() -> dict:
    return {
        "summary": "지원 사업 신청", "audience": "월계1동 주민",
        "action": "방문 신청", "location": "주민센터", "publisher": "노원구청",
        "applicable_area": "월계1동", "category": "application", "category_code": 27,
        "audience_scope": "specific", "action_requirement": "optional",
        "card_summaries": {
            "audience": "주민이 대상이에요.", "deadline": "10월 20일까지예요.",
            "action": "방문해서 신청해요.", "notes": "신분증을 가져가요.",
        },
        "dates": [
            {
                "kind": "application", "start_date": "2026-10-03",
                "end_date": "2026-10-20", "start_time": "09:00", "end_time": "18:00",
            },
            {"kind": "event", "start_date": "2026-11-01", "end_date": "2026-11-02"},
        ],
        "notes": ["신분증 지참", "방문 접수"],
        "topics": [{"title": "지원 사업"}, {"title": "접수 안내"}],
        "evidence": [
            {"field": "summary", "excerpt": "지원 사업 신청"},
            {"field": "audience", "excerpt": "월계1동 주민"},
            {"field": "dates", "excerpt": "10월 20일까지"},
        ],
    }


def _information_loss(
    db: psycopg.Connection, previous: dict | None, candidate: dict | None,
    *, previous_deadline: date | None = None, candidate_deadline: date | None = None,
) -> bool:
    return db.execute(
        "select public.summary_information_loss(%s,%s,%s,%s)",
        (Jsonb(previous), Jsonb(candidate), previous_deadline, candidate_deadline),
    ).fetchone()[0]


@pytest.mark.parametrize("field", [
    "summary", "audience", "action", "location", "publisher", "applicable_area",
    "category", "category_code", "audience_scope", "action_requirement",
])
def test_information_loss_detects_removed_or_unknown_scalar(
    db: psycopg.Connection, field: str,
) -> None:
    previous = _coverage_summary()
    for empty in (None, "", "unknown"):
        assert _information_loss(db, previous, previous | {field: empty}) is True
    candidate = deepcopy(previous)
    del candidate[field]
    assert _information_loss(db, previous, candidate) is True


@pytest.mark.parametrize("slot", CARD_SLOTS)
def test_information_loss_detects_card_text_loss(
    db: psycopg.Connection, slot: str,
) -> None:
    previous = _coverage_summary()
    candidate = deepcopy(previous)
    for empty in (None, "", "   "):
        candidate["card_summaries"][slot] = empty
        assert _information_loss(db, previous, candidate) is True
    del candidate["card_summaries"][slot]
    assert _information_loss(db, previous, candidate) is True


@pytest.mark.parametrize("part", ["start_date", "end_date", "start_time", "end_time"])
def test_information_loss_preserves_each_date_endpoint_and_time(
    db: psycopg.Connection, part: str,
) -> None:
    previous = _coverage_summary()
    candidate = deepcopy(previous)
    candidate["dates"][0][part] = None
    assert _information_loss(db, previous, candidate) is True
    # Adding the same kind of value under a different date kind is not recovery.
    candidate["dates"][1][part] = previous["dates"][0][part]
    assert _information_loss(db, previous, candidate) is True


@pytest.mark.parametrize("change", ["removed_entry", "changed_kind", "duplicate_kind"])
def test_information_loss_counts_dates_within_each_kind(
    db: psycopg.Connection, change: str,
) -> None:
    previous = _coverage_summary()
    if change == "duplicate_kind":
        previous["dates"].append(deepcopy(previous["dates"][0]))
    candidate = deepcopy(previous)
    if change == "changed_kind":
        candidate["dates"][0]["kind"] = "other"
    else:
        candidate["dates"].pop()
    assert _information_loss(db, previous, candidate) is True


@pytest.mark.parametrize("field", ["dates", "notes", "topics", "evidence"])
def test_information_loss_handles_missing_and_json_null_collections(
    db: psycopg.Connection, field: str,
) -> None:
    previous = _coverage_summary()
    for empty in (None, []):
        assert _information_loss(db, previous, previous | {field: empty}) is True
    candidate = deepcopy(previous)
    del candidate[field]
    assert _information_loss(db, previous, candidate) is True
    assert _information_loss(db, {field: None}, {}) is False
    assert _information_loss(db, {}, {field: None}) is False


@pytest.mark.parametrize("field", ["notes", "topics"])
def test_information_loss_detects_partial_list_removal(
    db: psycopg.Connection, field: str,
) -> None:
    previous = _coverage_summary()
    candidate = deepcopy(previous)
    candidate[field].pop()
    assert _information_loss(db, previous, candidate) is True


def test_information_loss_compares_evidence_fields_instead_of_excerpts_or_count(
    db: psycopg.Connection,
) -> None:
    previous = _coverage_summary()
    candidate = deepcopy(previous)
    candidate["evidence"][1] = {"field": "summary", "excerpt": "중복된 요약 근거"}
    assert len(candidate["evidence"]) == len(previous["evidence"])
    assert _information_loss(db, previous, candidate) is True
    candidate = deepcopy(previous)
    candidate["evidence"][1]["excerpt"] = "변경된 대상 근거"
    candidate["evidence"].reverse()
    assert _information_loss(db, previous, candidate) is False
    previous["uncertainties"] = ["원문 확인 필요"]
    previous["evidence"].append({"field": "uncertainties", "excerpt": "확인 필요"})
    candidate["uncertainties"] = []
    assert _information_loss(db, previous, candidate) is False


def test_information_loss_allows_complete_corrections_and_first_partial_result(
    db: psycopg.Connection,
) -> None:
    previous = _coverage_summary()
    candidate = deepcopy(previous)
    candidate.update(audience="노원구 주민", audience_scope="general", category_code=30)
    candidate["dates"][0]["end_date"] = "2026-10-31"
    candidate["card_summaries"]["deadline"] = "10월 31일까지예요."
    assert _information_loss(
        db, previous, candidate, previous_deadline=date(2026, 10, 20),
        candidate_deadline=date(2026, 10, 31),
    ) is False
    assert _information_loss(db, None, {"audience": "노원구 주민"}) is False
    assert db.execute(
        "select public.summary_information_loss(null,'{}'::jsonb,null,null)"
    ).fetchone() == (False,)
    assert _information_loss(db, previous, None) is True


def test_information_loss_detects_only_sorting_deadline_disappearance(
    db: psycopg.Connection,
) -> None:
    previous = _coverage_summary()
    assert _information_loss(
        db, previous, previous, previous_deadline=date(2026, 10, 20),
    ) is True
    assert _information_loss(
        db, previous, previous, candidate_deadline=date(2026, 10, 20),
    ) is False


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_information_loss_helper_is_not_callable_by_app_roles(
    db: psycopg.Connection, role: str,
) -> None:
    db.execute("set local role " + role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _information_loss(db, {}, {})


def test_information_loss_helper_is_immutable_invoker_and_backend_only(
    db: psycopg.Connection,
) -> None:
    oid = "public.summary_information_loss(jsonb,jsonb,date,date)"
    assert db.execute(
        "select provolatile,prosecdef,proconfig from pg_proc where oid=%s::regprocedure",
        (oid,),
    ).fetchone() == ("i", False, ["search_path=pg_catalog"])
    assert db.execute(
        "select count(*) from pg_proc p, lateral "
        "aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) acl "
        "where p.oid=%s::regprocedure and acl.grantee=0 and privilege_type='EXECUTE'",
        (oid,),
    ).fetchone() == (0,)
    db.execute("set local role service_role")
    assert _information_loss(db, {"audience": "주민"}, {"audience": None}) is True
