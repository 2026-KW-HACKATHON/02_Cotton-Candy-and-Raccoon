"""Bind a durable processing claim to the existing result writers.

Standalone commands keep their existing storage contract. Batch workers must
hold a current claim inside the same short transaction as every result write;
checking only after a network request would allow an expired worker to publish.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from psycopg import Connection

if TYPE_CHECKING:
    from pipeline.storage.processing_jobs import Claim, Feature

_CLAIM: ContextVar["Claim | None"] = ContextVar("notice_processing_claim", default=None)


@contextmanager
def processing_claim(claim: "Claim") -> Iterator[None]:
    token = _CLAIM.set(claim)
    try:
        yield
    finally:
        _CLAIM.reset(token)


def has_processing_claim() -> bool:
    return _CLAIM.get() is not None


def guard_processing_write(conn: Connection, notice_id: int, feature: "Feature") -> None:
    """Fence the write while the caller holds its transaction open.

    The queue and source locks taken here must survive through the write. Easy
    text writers acquire their stronger parent UPDATE lock before calling this
    guard; otherwise a later SHARE-to-UPDATE upgrade can deadlock a claimant.
    """
    claim = _CLAIM.get()
    if claim is None:
        return
    # Lazy import avoids a cycle through the queue's validated cache reader.
    from pipeline.storage.processing_jobs import ProcessingClaimSuperseded, assert_active_claim

    if claim.notice_id != notice_id or claim.feature != feature:
        raise ProcessingClaimSuperseded()
    assert_active_claim(conn, claim)
