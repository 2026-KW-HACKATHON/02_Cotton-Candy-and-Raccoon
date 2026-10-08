"""Bounded Expo outbox sender. Ambiguous sends never get an automatic duplicate retry."""

import json
import os
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row

SEND_URL = "https://exp.host/--/api/v2/push/send"
RECEIPT_URL = "https://exp.host/--/api/v2/push/getReceipts"


def send_result(response: httpx.Response) -> tuple[str, str | None]:
    # 429 explicitly rejects this request; 5xx/timeouts can have accepted it already.
    if response.status_code == 429:
        return "retry", "rate_limited"
    if response.status_code >= 500:
        return "unknown", "provider_unavailable"
    if response.status_code != 200:
        return "failed", "provider_rejected"
    try:
        data = response.json()["data"]
        if isinstance(data, list):
            (data,) = data
        if data.get("status") == "ok" and isinstance(data.get("id"), str):
            return "ticket", data["id"]
        code = data.get("details", {}).get("error")
        if code == "DeviceNotRegistered":
            return "invalid", "device_not_registered"
        if code == "MessageRateExceeded":
            return "retry", "message_rate_exceeded"
        return "failed", "provider_rejected"
    except (ValueError, KeyError, TypeError, AttributeError):
        return "unknown", "invalid_provider_response"


def dispatch(
    database_url: str, *, access_token: str, limit: int = 20, client: httpx.Client | None = None
) -> dict[str, int]:
    if not access_token or not 1 <= limit <= 20:
        raise ValueError("invalid_push_configuration")
    if client is None:
        with httpx.Client(timeout=15, follow_redirects=False) as owned:
            return dispatch(database_url, access_token=access_token, limit=limit, client=owned)
    headers = {"Authorization": f"Bearer {access_token}"}
    counts = {"ticket": 0, "delivered": 0, "failed": 0, "unknown": 0, "retry": 0}
    with psycopg.connect(
        database_url,
        autocommit=True,
        row_factory=dict_row,
        connect_timeout=5,
        options="-c statement_timeout=10000 -c lock_timeout=5000",
    ) as conn:
        # A process could have stopped after sending but before persisting the ticket.
        conn.execute(
            "update keyword_deliveries set state='unknown',error_code='send_interrupted' "
            "where state='sending' and updated_at<now()-interval '5 minutes'"
        )
        # Poll previously accepted tickets, never send them again.
        rows = conn.execute(
            "select * from keyword_deliveries where state='ticket' "
            "and next_attempt_at<=now() order by id limit %s",
            (limit,),
        ).fetchall()
        for row in rows:
            try:
                response = client.post(
                    RECEIPT_URL, headers=headers, json={"ids": [row["ticket_id"]]}
                )
                response.raise_for_status()
                receipt = response.json()["data"].get(row["ticket_id"])
                if not receipt:
                    conn.execute(
                        "update keyword_deliveries set "
                        "next_attempt_at=now()+interval '15 minutes', "
                        "state=case when updated_at<now()-interval '24 hours' then 'unknown' "
                        "else state end where id=%s and state='ticket'",
                        (row["id"],),
                    )
                    continue
                state = "delivered" if receipt.get("status") == "ok" else "failed"
                invalid = receipt.get("details", {}).get("error") == "DeviceNotRegistered"
                conn.execute(
                    "update keyword_deliveries set state=%s,error_code=%s "
                    "where id=%s and state='ticket'",
                    (state, None if state == "delivered" else "receipt_failed", row["id"]),
                )
                if invalid:
                    conn.execute(
                        "update keyword_devices set enabled=false where owner_id=%s "
                        "and push_token=%s",
                        (row["owner_id"], row["sent_token"]),
                    )
                counts[state] += 1
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                conn.execute(
                    "update keyword_deliveries set "
                        "next_attempt_at=now()+interval '15 minutes', "
                    "state=case when updated_at<now()-interval '24 hours' then 'unknown' "
                    "else state end where id=%s and state='ticket'",
                    (row["id"],),
                )
        for _ in range(limit):
            claim = uuid4()
            with conn.transaction():
                row = conn.execute(
                    "select j.*,d.push_token,d.enabled,n.is_visible "
                    "from keyword_deliveries j join keyword_devices d on d.owner_id=j.owner_id "
                    "join notices n on n.id=j.notice_id "
                    "where j.state='queued' and j.next_attempt_at<=now() "
                    "order by j.id for update of j skip locked limit 1"
                ).fetchone()
                if row is None:
                    break
                valid = conn.execute(
                    "select 1 from keyword_subscriptions where owner_id=%s "
                    "and keyword=any(%s) and created_at<=%s limit 1",
                    (row["owner_id"], row["matched_keywords"], row["created_at"]),
                ).fetchone()
                if not row["enabled"] or not row["is_visible"] or not valid:
                    conn.execute(
                        "update keyword_deliveries set state='cancelled' where id=%s", (row["id"],)
                    )
                    continue
                conn.execute(
                    "update keyword_deliveries set state='sending',claim_token=%s, "
                    "attempts=attempts+1,updated_at=now(),sent_token=%s where id=%s",
                    (claim, row["push_token"], row["id"]),
                )
            payload = {
                "to": row["push_token"],
                "title": "관심 키워드의 새 공문이 왔어요",
                "body": row["title"][:160],
                "sound": "default",
                "channelId": "notice-alerts",
                "data": {
                    "type": "keyword_notice",
                    "noticeId": str(row["notice_id"]),
                    "deliveryId": str(row["id"]),
                },
            }
            try:
                state, detail = send_result(client.post(SEND_URL, headers=headers, json=payload))
            except httpx.HTTPError:
                state, detail = "unknown", "send_outcome_unknown"
            if state == "invalid":
                conn.execute(
                    "update keyword_devices set enabled=false where owner_id=%s and push_token=%s",
                    (row["owner_id"], row["push_token"]),
                )
                state = "failed"
            if state == "retry" and row["attempts"] + 1 >= 3:
                state, detail = "failed", "retry_exhausted"
            counts[state] += 1
            conn.execute(
                "update keyword_deliveries set state=%s,ticket_id=%s,error_code=%s, "
                "next_attempt_at=now()+interval '15 minutes',updated_at=now() "
                "where id=%s and claim_token=%s and state='sending'",
                (
                    "queued" if state == "retry" else state,
                    detail if state == "ticket" else None,
                    None if state == "ticket" else detail,
                    row["id"],
                    claim,
                ),
            )
    return counts


def main() -> None:
    try:
        result = dispatch(os.environ["DATABASE_URL"], access_token=os.environ["EXPO_ACCESS_TOKEN"])
    except (KeyError, ValueError, psycopg.Error, httpx.HTTPError):
        raise SystemExit(
            "Keyword push processing failed; check server configuration and DB."
        ) from None
    print(json.dumps(result))
    if result["failed"] or result["unknown"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
