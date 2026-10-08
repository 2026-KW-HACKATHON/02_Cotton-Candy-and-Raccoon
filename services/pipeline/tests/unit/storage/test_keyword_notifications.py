"""Real DB ownership, new-notice matching and provider failure isolation."""

import json
from uuid import uuid4

import httpx
import psycopg
import pytest
from support.db import database_uri, owned_migrated_database

from pipeline.keyword_notifications import dispatch, send_result


@pytest.fixture(scope="module")
def database():
    with owned_migrated_database(
        env="PIPELINE_TEST_DATABASE_URL",
        required_prefix="pipeline_schema_test_",
        missing="skip",
        missing_message="Disposable database required",
        invalid_message="Use disposable local database",
    ) as info:
        yield info


@pytest.fixture
def conn(database):
    with psycopg.connect(**database, autocommit=True) as connection:
        yield connection
        connection.execute("reset role")
        connection.execute("delete from keyword_devices")
        connection.execute("delete from notices")


def save(conn, owner, words, enabled=True, token="ExpoPushToken[test]"):
    conn.execute(
        "select set_config('request.jwt.claims',%s,false)", (json.dumps({"sub": str(owner)}),)
    )
    conn.execute("set role authenticated")
    try:
        return conn.execute(
            "select save_keyword_preferences(%s,%s,%s)", (words, enabled, token)
        ).fetchone()[0]
    finally:
        conn.execute("reset role")


def notice(conn, title="새 공지", body="장학금 지원", old=False):
    return conn.execute(
        "insert into notices(category,source_board,post_sn,title,body_html,body_text,"
        "registered_on,url,is_visible) values('nowon','1001',%s,%s,%s,%s,"
        "(now() at time zone 'Asia/Seoul')::date - %s,"
        "'https://www.nowon.kr/test',true) returning id",
        (uuid4().hex, title, "<p>" + body + "</p>", body, 2 if old else 0),
    ).fetchone()[0]


def unthrottle(conn):
    conn.execute("update keyword_devices set updated_at=now()-interval '4 seconds'")


def test_title_or_body_matches_once_and_updates_do_not_notify(conn):
    owner = uuid4()
    prior = notice(conn)
    save(conn, owner, ["장학금", "지원"])
    conn.execute("update notices set title='장학금 지원 수정' where id=%s", (prior,))
    notice(conn, old=True)
    notice(conn, body="관련 없는 공지")
    new = notice(conn, title="장학금 안내")
    rows = conn.execute("select notice_id,matched_keywords from keyword_deliveries").fetchall()
    assert len(rows) == 1 and rows[0][0] == new
    assert set(rows[0][1]) == {"장학금", "지원"}


def test_subscriptions_are_owned_and_private(conn):
    a, b = uuid4(), uuid4()
    save(conn, a, ["장학금"])
    save(conn, b, ["주차"], token="ExpoPushToken[other]")
    conn.execute("set role authenticated")
    assert conn.execute("select get_keyword_preferences()").fetchone()[0]["keywords"] == ["주차"]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select * from keyword_devices")
    conn.execute("reset role")
    conn.execute("set role anon")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select get_keyword_preferences()")
    conn.execute("reset role")
    assert (
        conn.execute(
            "select keyword from keyword_subscriptions where owner_id=%s", (a,)
        ).fetchone()[0]
        == "장학금"
    )


def test_limits_and_subscription_removal(conn):
    owner = uuid4()
    result = save(conn, owner, ["  장학금  ", "장학금", "HEALTH"])
    assert set(result["keywords"]) == {"장학금", "health"}
    with pytest.raises(psycopg.errors.RaiseException, match="update_rate_limited"):
        save(conn, owner, ["주차"])
    notice(conn)
    unthrottle(conn)
    save(conn, owner, ["health"])
    assert conn.execute("select state from keyword_deliveries").fetchone()[0] == "cancelled"
    unthrottle(conn)
    with pytest.raises(psycopg.errors.RaiseException, match="invalid_keywords"):
        save(conn, owner, ["x"])


def test_send_ticket_receipt_and_idempotency(conn, database):
    save(conn, uuid4(), ["장학금"])
    notice(conn)
    calls = []

    def transport(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/send"):
            return httpx.Response(200, json={"data": {"status": "ok", "id": "receipt-1"}})
        return httpx.Response(200, json={"data": {"receipt-1": {"status": "ok"}}})

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        assert dispatch(database_uri(database), access_token="fake", client=client)["ticket"] == 1
        dispatch(database_uri(database), access_token="fake", client=client)
        assert len(calls) == 1
        conn.execute("update keyword_deliveries set next_attempt_at=now()-interval '1 second'")
        assert (
            dispatch(database_uri(database), access_token="fake", client=client)["delivered"] == 1
        )
    assert len(calls) == 2


@pytest.mark.parametrize(
    "mode,expected", [("timeout", "unknown"), ("invalid", "failed"), ("rate", "queued")]
)
def test_failed_push_never_rolls_back_notice(conn, database, mode, expected):
    save(conn, uuid4(), ["장학금"])
    new = notice(conn)

    def transport(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("synthetic", request=request)
        if mode == "rate":
            return httpx.Response(429)
        return httpx.Response(
            200, json={"data": {"status": "error", "details": {"error": "DeviceNotRegistered"}}}
        )

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        dispatch(database_uri(database), access_token="fake", client=client)
    assert conn.execute("select state from keyword_deliveries").fetchone()[0] == expected
    assert conn.execute("select id from notices where id=%s", (new,)).fetchone()
    if mode == "invalid":
        assert not conn.execute("select enabled from keyword_devices").fetchone()[0]


def test_ambiguous_provider_responses_are_not_retried():
    for response in (httpx.Response(500), httpx.Response(200, json={"data": []})):
        assert send_result(response)[0] == "unknown"


@pytest.mark.parametrize("operation", ["disable", "delete", "hide"])
def test_pending_push_is_cancelled_before_delivery(conn, database, operation):
    owner = uuid4()
    save(conn, owner, ["장학금"])
    item = notice(conn)
    unthrottle(conn)
    if operation == "disable":
        save(conn, owner, ["장학금"], enabled=False)
    elif operation == "delete":
        save(conn, owner, [], enabled=False)
    else:
        conn.execute("update notices set is_visible=false where id=%s", (item,))
    def no_network(request):
        pytest.fail("Cancelled subscription must not reach Expo")
    with httpx.Client(transport=httpx.MockTransport(no_network)) as client:
        dispatch(database_uri(database), access_token="fake", client=client)
    assert conn.execute("select state from keyword_deliveries").fetchone()[0] == "cancelled"


def test_rate_limit_retries_are_bounded(conn, database):
    save(conn, uuid4(), ["장학금"])
    notice(conn)
    calls = []
    def rejected(request):
        calls.append(request)
        return httpx.Response(429)
    with httpx.Client(transport=httpx.MockTransport(rejected)) as client:
        for _ in range(4):
            dispatch(database_uri(database), access_token="fake", client=client)
            conn.execute("update keyword_deliveries set next_attempt_at=now()-interval '1 second'")
    assert len(calls) == 3
    assert conn.execute("select state,attempts from keyword_deliveries").fetchone() == ("failed", 3)


def test_token_refresh_and_late_invalid_receipt_do_not_disable_new_token(conn, database):
    owner = uuid4()
    save(conn, owner, ["장학금"])
    notice(conn)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, json={"data": {"status": "ok", "id": "ticket"}},
    ))) as client:
        dispatch(database_uri(database), access_token="fake", client=client)
    before = conn.execute("select created_at from keyword_subscriptions").fetchone()[0]
    unthrottle(conn)
    conn.execute("set role authenticated")
    conn.execute("select refresh_keyword_push_token('ExpoPushToken[new]')")
    conn.execute("reset role")
    conn.execute("update keyword_deliveries set next_attempt_at=now()-interval '1 second'")
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, json={"data": {"ticket": {"status": "error", "details": {
            "error": "DeviceNotRegistered"}}}},
    ))) as client:
        dispatch(database_uri(database), access_token="fake", client=client)
    assert conn.execute("select enabled,push_token from keyword_devices").fetchone() == (
        True, "ExpoPushToken[new]",
    )
    assert conn.execute("select created_at from keyword_subscriptions").fetchone()[0] == before
