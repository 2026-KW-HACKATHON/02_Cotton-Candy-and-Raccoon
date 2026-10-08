"""Private retry queue; short claims and fenced completion never alter public results."""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from pipeline.glossary.easy_language import DEFAULT_MODEL as EASY_TEXT_MODEL
from pipeline.glossary.easy_language import PROMPT_VERSION as EASY_TEXT_PROMPT_VERSION
from pipeline.transform.gemini_client import DEFAULT_MODEL as SUMMARY_MODEL
from pipeline.transform.gemini_prompt import SUMMARY_PROMPT_VERSION

type Feature = Literal["summary", "easy_text"]
type FinishState = Literal["retry_wait", "succeeded", "skipped", "blocked", "exhausted"]
FEATURES: tuple[Feature, ...] = ("summary", "easy_text")


class ProcessingClaimSuperseded(RuntimeError):
    """The source, lease, or current owner changed; this worker must not publish."""

    reason_code = "processing_claim_superseded"

    def __init__(self) -> None:
        super().__init__(self.reason_code)


def make_contract_key(model: str, prompt_version: str) -> str:
    """Version cache identity without exposing provider secrets or notice content."""
    if not all(isinstance(value, str) and value.strip() for value in (model, prompt_version)):
        raise ValueError("invalid_processing_contract")
    value = json.dumps(
        {"model": model, "prompt_version": prompt_version}, sort_keys=True, separators=(",", ":")
    )
    if len(value) > 1024:
        raise ValueError("invalid_processing_contract")
    return value


@dataclass(frozen=True, slots=True)
class Candidate:
    notice_id: int
    feature: Feature
    input_version: str
    contract_key: str

    def __post_init__(self) -> None:
        if type(self.notice_id) is not int or not 0 < self.notice_id <= 2**63 - 1:
            raise ValueError("invalid_notice_id")
        if self.feature not in FEATURES:
            raise ValueError("invalid_processing_feature")
        pattern = r"[1-9][0-9]{0,18}" if self.feature == "summary" else r"[0-9a-f]{64}"
        if not isinstance(self.input_version, str) or not re.fullmatch(pattern, self.input_version):
            raise ValueError("invalid_processing_version")
        try:
            contract = json.loads(self.contract_key)
            if self.contract_key != make_contract_key(**contract):
                raise ValueError("invalid_processing_contract")
        except (TypeError, ValueError):
            raise ValueError("invalid_processing_contract") from None


@dataclass(frozen=True, slots=True)
class Claim(Candidate):
    claim_token: UUID
    attempts: int
    lease_expires_at: datetime


def _now(value: datetime | None) -> datetime:
    value = datetime.now(UTC) if value is None else value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("processing_time_requires_timezone")
    return value


def _features(features: Sequence[Feature]) -> list[Feature]:
    values = list(dict.fromkeys(features))
    if not values or any(value not in FEATURES for value in values):
        raise ValueError("invalid_processing_feature")
    return values


_SOURCE_VERSION = """
case when %s = 'summary' then n.content_revision::text
     else public.notice_easy_text_revision(n.title, n.body_html) end
"""

# This is a result cache test, not a queue status test. Missing result rows and
# legacy/current-contract mismatches are filtered before applying a batch limit.
_SUMMARY_READY = """
s.status in ('summarized', 'needs_review') and s.result is not null
and s.file_manifest ->> 'source_revision' = n.content_revision::text
and s.model = %(summary_model)s and s.prompt_version = %(summary_prompt)s
"""
_EASY_READY = """
e.notice_revision = public.notice_easy_text_revision(n.title, n.body_html)
and e.model = %(easy_model)s and e.prompt_version = %(easy_prompt)s
and e.dictionary_candidates is not null
and e.body_text_present is true and e.attachment_content_included is false
and public.notice_easy_text_preserves_title(n.title, e.original_text, e.easy_text, e.changes)
and public.notice_dictionary_candidates_valid(
    e.original_text, e.dictionary_candidates, char_length(n.title) + 1)
"""


_INPUTS = f"""
with inputs as (
    select n.id as notice_id, 'summary'::text as feature,
           n.content_revision::text as input_version, %(summary_contract)s::text as contract_key,
           coalesce(({_SUMMARY_READY}), false) as ready
    from public.notices n left join public.notice_summaries s on s.notice_id = n.id
    where n.is_visible and (%(notice_id)s::bigint is null or n.id = %(notice_id)s)
      and (%(source)s::text is null or n.category = %(source)s)
    union all
    select n.id, 'easy_text', public.notice_easy_text_revision(n.title, n.body_html),
           %(easy_contract)s::text, coalesce(({_EASY_READY}), false)
    from public.notices n left join public.notice_easy_texts e on e.notice_id = n.id
    where n.is_visible and (%(notice_id)s::bigint is null or n.id = %(notice_id)s)
      and (%(source)s::text is null or n.category = %(source)s)
)
"""


def select_candidates(
    conn: Connection,
    *,
    features: Sequence[Feature] = FEATURES,
    notice_id: int | None = None,
    source: str | None = None,
    limit: int | None = 100,
    summary_model: str = SUMMARY_MODEL,
    summary_prompt_version: str = SUMMARY_PROMPT_VERSION,
    easy_text_model: str = EASY_TEXT_MODEL,
    easy_text_prompt_version: str = EASY_TEXT_PROMPT_VERSION,
    now: datetime | None = None,
) -> list[Candidate]:
    """Read missing/currently due work across all notices, without any mutations.

    A skipped job is complete for that exact input/contract (e.g. no body).
    A succeeded job whose result has disappeared is rediscovered. Blocked and
    exhausted work stays stopped until its input/contract changes or manual retry.
    """
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 10000):
        raise ValueError("invalid_processing_limit")
    params = {
        "features": _features(features), "notice_id": notice_id, "limit": limit,
        "source": source,
        "summary_model": summary_model, "summary_prompt": summary_prompt_version,
        "easy_model": easy_text_model, "easy_prompt": easy_text_prompt_version,
        "summary_contract": make_contract_key(summary_model, summary_prompt_version),
        "easy_contract": make_contract_key(easy_text_model, easy_text_prompt_version),
        "now": _now(now),
    }
    query = f"""
{_INPUTS}
select i.notice_id, i.feature, i.input_version, i.contract_key
from inputs i left join public.notice_processing_jobs j
    on j.notice_id = i.notice_id and j.feature = i.feature
where i.feature = any(%(features)s::text[]) and not i.ready
  and (j.notice_id is null or j.input_version <> i.input_version
       or j.contract_key <> i.contract_key or j.state in ('pending', 'succeeded')
       or (j.state = 'retry_wait' and j.next_attempt_at <= %(now)s)
       or (j.state = 'running' and j.lease_expires_at <= %(now)s))
order by coalesce(j.next_attempt_at, j.updated_at, '-infinity'::timestamptz),
         i.notice_id, i.feature
limit %(limit)s
"""
    with conn.cursor(row_factory=dict_row) as cursor:
        return [Candidate(**row) for row in cursor.execute(query, params)]


def readiness_counts(conn: Connection, *, feature: Feature, source: str) -> dict[str, int]:
    """Distinguish result readiness from this invocation's attempted jobs.

    Include deferred/stopped work and jobs beyond the batch limit. Reuse the
    selector's exact cache/version contract so an old success cannot hide a gap.
    """
    params = {
        "notice_id": None, "source": source,
        "summary_model": SUMMARY_MODEL, "summary_prompt": SUMMARY_PROMPT_VERSION,
        "easy_model": EASY_TEXT_MODEL, "easy_prompt": EASY_TEXT_PROMPT_VERSION,
        "summary_contract": make_contract_key(SUMMARY_MODEL, SUMMARY_PROMPT_VERSION),
        "easy_contract": make_contract_key(EASY_TEXT_MODEL, EASY_TEXT_PROMPT_VERSION),
        "feature": feature,
    }
    rows = conn.execute(f"""
{_INPUTS}
select case when i.ready then 'ready'
            when j.input_version = i.input_version and j.contract_key = i.contract_key
                 and j.state <> 'succeeded' then j.state
            else 'pending' end as state, count(*)
from inputs i left join public.notice_processing_jobs j
    on j.notice_id = i.notice_id and j.feature = i.feature
where i.feature = %(feature)s
group by 1
""", params).fetchall()
    counts = dict.fromkeys(
        ("ready", "skipped", "pending", "running", "retry_wait", "blocked", "exhausted"), 0,
    )
    counts.update(rows)
    return counts


def _lock_source(conn: Connection, candidate: Candidate, *, skip_locked: bool = False) -> bool:
    suffix = " skip locked" if skip_locked else ""
    row = conn.execute(
        f"select {_SOURCE_VERSION} from public.notices n "
        f"where n.id = %s and n.is_visible for share{suffix}",
        (candidate.feature, candidate.notice_id),
    ).fetchone()
    return row is not None and row[0] == candidate.input_version


def has_current_result(conn: Connection, candidate: Candidate) -> bool:
    """Check the published cache contract; callers fencing completion must also hold the claim."""
    contract = json.loads(candidate.contract_key)
    params = {
        "id": candidate.notice_id,
        "summary_model": contract["model"], "summary_prompt": contract["prompt_version"],
        "easy_model": contract["model"], "easy_prompt": contract["prompt_version"],
    }
    condition = _SUMMARY_READY if candidate.feature == "summary" else _EASY_READY
    table, alias = (
        ("notice_summaries", "s") if candidate.feature == "summary" else ("notice_easy_texts", "e")
    )
    return conn.execute(
        f"select coalesce(({condition}), false) from public.notices n "
        f"left join public.{table} {alias} on {alias}.notice_id = n.id where n.id = %(id)s",
        params,
    ).fetchone()[0]


def enqueue_candidates(
    conn: Connection, candidates: Sequence[Candidate], *, now: datetime | None = None
) -> int:
    """Synchronize current inputs without resetting attempts/backoff on repeated scans."""
    timestamp = _now(now)
    count = 0
    # Parent -> job is the same lock order used by claim/finalize/public save.
    for candidate in sorted(candidates, key=lambda item: (item.notice_id, item.feature)):
        with conn.transaction():
            if not _lock_source(conn, candidate) or has_current_result(conn, candidate):
                continue
            row = conn.execute(
                "insert into public.notice_processing_jobs "
                "(notice_id, feature, input_version, contract_key, created_at, updated_at) "
                "values (%s,%s,%s,%s,%s,%s) on conflict (notice_id, feature) do update set "
                "input_version=excluded.input_version, contract_key=excluded.contract_key, "
                "state='pending', attempts=0, next_attempt_at=null, last_error_code=null, "
                "claim_token=null, lease_expires_at=null, updated_at=excluded.updated_at "
                "where notice_processing_jobs.input_version <> excluded.input_version "
                "or notice_processing_jobs.contract_key <> excluded.contract_key "
                "or notice_processing_jobs.state = 'succeeded' returning notice_id",
                (candidate.notice_id, candidate.feature, candidate.input_version,
                 candidate.contract_key, timestamp, timestamp),
            ).fetchone()
            count += row is not None
    return count


def claim_next(
    conn: Connection,
    *,
    features: Sequence[Feature] = FEATURES,
    contract_keys: Mapping[Feature, str] | None = None,
    transitions: list[dict[str, object]] | None = None,
    notice_id: int | None = None,
    source: str | None = None,
    max_attempts: int = 3,
    lease_seconds: int = 300,
    now: datetime | None = None,
) -> Claim | None:
    """Atomically claim due work; stale ownership consumes the previous attempt.

    Call using an idle connection. Transactions end before this function returns;
    callers must never hold a transaction while contacting an external service.
    """
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("invalid_processing_attempt_limit")
    if type(lease_seconds) is not int or lease_seconds < 1:
        raise ValueError("invalid_processing_lease")
    if contract_keys is not None:
        _features(tuple(contract_keys))
    contracts = Jsonb(dict(contract_keys)) if contract_keys is not None else None
    timestamp = _now(now)
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "select notice_id,feature,input_version,contract_key "
            "from public.notice_processing_jobs "
            "where feature = any(%s::text[]) and (%s::bigint is null or notice_id=%s) "
            "and (%s::text is null or notice_id in "
            "(select id from public.notices where category=%s)) "
            "and (%s::jsonb is null or contract_key = (%s::jsonb ->> feature)) "
            "and (state='pending' or (state='retry_wait' and next_attempt_at <= %s) "
            "or (state='running' and lease_expires_at <= %s)) "
            "order by coalesce(next_attempt_at, updated_at), notice_id, feature",
            (_features(features), notice_id, notice_id, source, source,
             contracts, contracts, timestamp, timestamp),
        ).fetchall()
    for row in rows:
        candidate = Candidate(**row)
        recovered = None
        acquired = None
        with conn.transaction():
            if not _lock_source(conn, candidate, skip_locked=True):
                continue
            with conn.cursor(row_factory=dict_row) as cursor:
                job = cursor.execute(
                    "select * from public.notice_processing_jobs where notice_id=%s and feature=%s "
                    "and input_version=%s and contract_key=%s and (state='pending' "
                    "or (state='retry_wait' and next_attempt_at <= %s) "
                    "or (state='running' and lease_expires_at <= %s)) for update skip locked",
                    (candidate.notice_id, candidate.feature, candidate.input_version,
                     candidate.contract_key, timestamp, timestamp),
                ).fetchone()
            if job is None:
                continue
            # A blocked cache/parent/job read may outlive the original scan time.
            timestamp = _now(now)
            if has_current_result(conn, candidate):
                _settle(conn, candidate, "succeeded", None, None, timestamp)
                recovered = {"state": "succeeded", "reason_code": None}
            elif job["attempts"] >= max_attempts:
                reason = "processing_lease_expired" if job["state"] == "running" else (
                    job["last_error_code"] or "processing_attempts_exhausted"
                )
                _settle(conn, candidate, "exhausted", reason, None, timestamp)
                recovered = {"state": "exhausted", "reason_code": reason}
            else:
                token = uuid4()
                expiry = timestamp + timedelta(seconds=lease_seconds)
                conn.execute(
                    "update public.notice_processing_jobs "
                    "set state='running', attempts=attempts+1, "
                    "claim_token=%s, lease_expires_at=%s, next_attempt_at=null, updated_at=%s "
                    "where notice_id=%s and feature=%s",
                    (token, expiry, timestamp, candidate.notice_id, candidate.feature),
                )
                acquired = Claim(**row, claim_token=token, attempts=job["attempts"] + 1,
                                 lease_expires_at=expiry)
        # Report only committed transitions, including attempts exhausted without
        # starting another worker. A failed commit must never produce success.
        if recovered is not None and transitions is not None:
            transitions.append({
                "notice_id": candidate.notice_id, "feature": candidate.feature,
                **recovered, "attempts": job["attempts"], "next_attempt_at": None,
                "recovered": True,
            })
        if acquired is not None:
            return acquired
    return None


def assert_active_claim(
    conn: Connection, claim: Claim, *, now: datetime | None = None
) -> None:
    """Fence public writes inside their transaction, locking parent before job.

    The caller must already be inside a transaction and keep it through its
    result write. Calling this only before the network request is insufficient.
    """
    if conn.info.transaction_status == TransactionStatus.IDLE:
        raise ValueError("processing_fence_requires_transaction")
    if not _lock_source(conn, claim):
        raise ProcessingClaimSuperseded()
    row = conn.execute(
        "select lease_expires_at from public.notice_processing_jobs "
        "where notice_id=%s and feature=%s "
        "and state='running' and claim_token=%s and input_version=%s and contract_key=%s "
        "for update",
        (claim.notice_id, claim.feature, claim.claim_token, claim.input_version,
         claim.contract_key),
    ).fetchone()
    # Read the clock *after* acquiring both locks. A waiter must not authorize a
    # write using the timestamp from before its lease expired during lock waits.
    if row is None or row[0] <= _now(now):
        raise ProcessingClaimSuperseded()


def _settle(
    conn: Connection, candidate: Candidate, state: FinishState,
    reason: str | None, next_attempt_at: datetime | None, now: datetime,
) -> None:
    conn.execute(
        "update public.notice_processing_jobs set state=%s, last_error_code=%s, "
        "next_attempt_at=%s, claim_token=null, lease_expires_at=null, updated_at=%s "
        "where notice_id=%s and feature=%s",
        (state, reason, next_attempt_at, now, candidate.notice_id, candidate.feature),
    )


def finish_claim(
    conn: Connection, claim: Claim, *, state: FinishState,
    last_error_code: str | None = None, next_attempt_at: datetime | None = None,
    now: datetime | None = None,
) -> bool:
    """Complete only this still-live claim; obsolete completions are harmless no-ops."""
    if state not in {"retry_wait", "succeeded", "skipped", "blocked", "exhausted"}:
        raise ValueError("invalid_processing_finish_state")
    timestamp = _now(now)
    if state == "retry_wait":
        if next_attempt_at is None or _now(next_attempt_at) <= timestamp:
            raise ValueError("processing_retry_requires_future_time")
    elif next_attempt_at is not None:
        raise ValueError("processing_terminal_has_retry_time")
    if last_error_code is not None and not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", last_error_code):
        raise ValueError("invalid_processing_error_code")
    try:
        with conn.transaction():
            assert_active_claim(conn, claim, now=now)
            _settle(conn, claim, state, last_error_code, next_attempt_at, _now(now))
        return True
    except ProcessingClaimSuperseded:
        return False


def retry_job(
    conn: Connection, notice_id: int, feature: Feature, *, now: datetime | None = None
) -> bool:
    """Explicitly release stopped work; never touch or force-replace a public result."""
    _features([feature])
    with conn.transaction():
        parent = conn.execute(
            "select id from public.notices where id=%s and is_visible for share", (notice_id,),
        ).fetchone()
        if parent is None:
            return False
        return conn.execute(
            "update public.notice_processing_jobs set state='pending', attempts=0, "
            "last_error_code=null, next_attempt_at=null, updated_at=%s "
            "where notice_id=%s and feature=%s and state in ('blocked','exhausted') "
            "returning notice_id", (_now(now), notice_id, feature),
        ).fetchone() is not None
