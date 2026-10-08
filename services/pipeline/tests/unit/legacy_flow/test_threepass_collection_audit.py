"""Fault injection for truthful progress and fail-closed collection boundaries."""

from unittest.mock import MagicMock, patch

import psycopg
import pytest
from support.threepass_collection_audit import EMPTY_ATTACHMENTS, _nowon

from pipeline.collect_nowon import collect_and_save_nowon
from pipeline.collect_wolgye1 import WolgyeListing, collect_and_save_wolgye1
from pipeline.config import DatabaseSettings, NowonSettings, WolgyeSettings
from pipeline.models import RawNotice
from pipeline.sources.nowon_api import NowonCollection, PageStatus
from pipeline.sources.nowon_page import NowonPageError
from pipeline.sources.wolgye1_board import BoardEntry, WolgyeSourceError


@pytest.mark.parametrize("failure", ["rate_limit", "listing_conflict", "db_unavailable"])
def test_nowon_does_not_report_skipped_notices_as_attempted(failure: str) -> None:
    notices = tuple(_nowon(str(index)) for index in range(1, 4))
    conflicts = ("1",) if failure == "listing_conflict" else ()
    listing = NowonCollection(notices, (PageStatus(1, 3, 1, True),), 3, True, (), conflicts)
    conn = MagicMock()
    conn.closed = failure == "db_unavailable"
    connects = [conn, psycopg.OperationalError("PRIVATE_DB_DETAIL")]
    with (
        patch("pipeline.collect_nowon.collect_all", return_value=listing),
        patch("pipeline.collect_nowon.psycopg.connect", side_effect=connects),
        patch("pipeline.collect_nowon.fetch_notice_page", side_effect=(
            NowonPageError("429", rate_limited=True) if failure == "rate_limit"
            else lambda notice, _settings: (notice.url, EMPTY_ATTACHMENTS)
        )) as page,
        patch("pipeline.collect_nowon.save_notice_with_files", return_value=17) as save,
    ):
        result = collect_and_save_nowon(
            NowonSettings("audit-only", 2.0, 7.0), DatabaseSettings("postgresql://unused/audit")
        )
    expected = 2 if failure == "listing_conflict" else 1
    assert result.attempted_count == page.call_count == expected
    assert result.complete is False
    assert result.saved_count == save.call_count == (2 if failure == "listing_conflict" else 0)
    assert "PRIVATE_DB_DETAIL" not in repr(result)
    if failure != "listing_conflict":
        assert all(item.reason_code.endswith("_not_attempted") for item in result.failures[1:])


@pytest.mark.parametrize("failure", ["rate_limit", "listing_conflict", "db_unavailable"])
def test_wolgye_does_not_report_skipped_notices_as_attempted(failure: str) -> None:
    entries = tuple(BoardEntry(str(index), "audit", "월계1동", "2026-10-07", False)
                    for index in range(1, 4))
    conflicts = ("1",) if failure == "listing_conflict" else ()
    listing = WolgyeListing(3, entries, (), 0, conflicts, False, True)
    conn = MagicMock()
    conn.closed = failure == "db_unavailable"

    def raw(entry: BoardEntry, _html: str) -> RawNotice:
        return RawNotice(
            source_board="1042", category="dong", dong_group="wolgye1", is_pinned=False,
            post_sn=entry.post_sn, title=entry.title, department="월계1동",
            registered_on="2026-10-07",
            url=entry.url, body_html="<p>Body</p>", license_type="KOGL-1",
        )

    with (
        patch("pipeline.collect_wolgye1.collect_wolgye_listing", return_value=listing),
        patch("pipeline.collect_wolgye1.psycopg.connect",
              side_effect=[conn, psycopg.OperationalError("PRIVATE_DB_DETAIL")]),
        patch("pipeline.collect_wolgye1._fetch_detail_with_retry", side_effect=(
            WolgyeSourceError("429", rate_limited=True) if failure == "rate_limit"
            else lambda *_args: "detail"
        )) as page,
        patch("pipeline.collect_wolgye1.parse_detail_page", side_effect=raw),
        patch("pipeline.collect_wolgye1.extract_dong_files", return_value=[]),
        patch("pipeline.collect_wolgye1.save_notice_with_files", return_value=17) as save,
    ):
        result = collect_and_save_wolgye1(
            WolgyeSettings(1, 2), DatabaseSettings("postgresql://unused/audit")
        )
    expected = 2 if failure == "listing_conflict" else 1
    assert result.attempted_count == page.call_count == expected
    assert result.complete is False
    assert result.saved_count == save.call_count == (2 if failure == "listing_conflict" else 0)
    assert "PRIVATE_DB_DETAIL" not in repr(result)
    if failure != "listing_conflict":
        assert all(item.reason_code.endswith("_not_attempted") for item in result.failures[1:])
