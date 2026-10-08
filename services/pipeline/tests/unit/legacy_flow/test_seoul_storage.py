"""Seoul single-notice storage; use disposable PIPELINE_TEST_DATABASE_URL only."""

import json
import os
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import patch

import httpx
import psycopg
import pytest
from support.paths import FIXTURES_DIR

from pipeline.cli import main
from pipeline.collect_seoul import SeoulStorageError, collect_and_save_one, prepare_one
from pipeline.config import DatabaseSettings, SeoulNewsSettings
from pipeline.sources.seoul_api import SeoulSourceError, collect_one
from pipeline.storage.notice_bundle import save_notice_with_files
from pipeline.transform.seoul import SeoulTransformError

FIXTURES = FIXTURES_DIR
SETTINGS = SeoulNewsSettings("sample", 5, 20)
XML = (FIXTURES / "seoul_one.xml").read_bytes()


def api_transport() -> httpx.MockTransport:
    return httpx.MockTransport(lambda r: httpx.Response(200, content=XML))


def test_filtered_one_uses_exact_single_index_and_board() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sample/xml/SeoulNewsList/2/2/25/"
        return httpx.Response(200, content=XML)

    notice = collect_one(
        SETTINGS, index=2, source_board="25", transport=httpx.MockTransport(respond)
    )
    assert notice.post_sn == "000123" and notice.source_board == "25"


@pytest.mark.parametrize(
    "index,board", [(0, None), (-1, None), (True, None), ("2", None), (6, None), (1, "99"), (1, 25)]
)
def test_bad_selection_fails_before_request(index: object, board: object) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid selection must not make requests")

    with pytest.raises(SeoulSourceError):
        collect_one(SETTINGS, index=index, source_board=board, transport=httpx.MockTransport(fail))


def test_ignored_api_board_filter_is_error() -> None:
    with pytest.raises(SeoulSourceError, match="게시판"):
        collect_one(SETTINGS, source_board="24", transport=api_transport())


def test_failure_before_storage_never_connects() -> None:
    with (
        patch("pipeline.collect_seoul.prepare_one", side_effect=SeoulTransformError("wrong page")),
        patch(
            "pipeline.collect_seoul.psycopg.connect",
            side_effect=AssertionError("must not connect"),
        ),
    ):
        with pytest.raises(SeoulTransformError):
            collect_and_save_one(SETTINGS, DatabaseSettings("postgresql://user@localhost/test"))


def test_db_error_hides_database_uri_and_chained_details() -> None:
    api = api_transport()
    with patch(
        "pipeline.collect_seoul.psycopg.connect",
        side_effect=psycopg.OperationalError(
            "postgresql://user:secret@127.0.0.1/db",
        ),
    ):
        with pytest.raises(SeoulStorageError) as caught:
            collect_and_save_one(
                SETTINGS,
                DatabaseSettings("postgresql://user@localhost/test"),
                api_transport=api,
            )
    assert "secret" not in str(caught.value) and caught.value.__suppress_context__


def test_cli_storage_summary_and_option_validation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SEOUL_NEWS_API_KEY", "sample")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@localhost/test")
    api = api_transport()
    record, files = prepare_one(SETTINGS, api_transport=api)
    with patch("pipeline.cli.collect_and_save_one", return_value=(42, record, files)) as save:
        assert (
            main(["collect-one", "--source", "seoul", "--source-board", "25", "--index", "2"]) == 0
        )
    assert save.call_args.kwargs == {"source_board": "25", "index": 2}
    summary = json.loads(capsys.readouterr().out)
    assert summary["stored"] is True and summary["notice_id"] == 42
    assert summary["attachment_count"] == 1 and summary["inline_image_count"] == 1
    for argv in (
        ["collect-one", "--source", "seoul", "--index", "0"],
        ["collect-one", "--source", "seoul", "--post-sn", "123"],
        ["collect-one", "--source", "nowon", "--source-board", "25"],
        ["inspect-prepared", "--source", "seoul", "--index", "0"],
    ):
        with patch("pipeline.cli.collect_and_save_one") as save:
            assert main(argv) == 2
            save.assert_not_called()


def test_cli_storage_failure_and_missing_config(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["collect-one", "--source", "seoul"]) == 2
    capsys.readouterr()
    monkeypatch.setenv("SEOUL_NEWS_API_KEY", "sample")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@localhost/test")
    with patch("pipeline.cli.collect_and_save_one", side_effect=SeoulStorageError("저장 실패")):
        assert main(["collect-one", "--source", "seoul"]) == 1
    result = capsys.readouterr()
    assert result.out == "" and "저장 실패" in result.err


@pytest.fixture
def db_conn() -> Iterator[psycopg.Connection]:
    dsn = os.getenv("PIPELINE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("PIPELINE_TEST_DATABASE_URL is required for Seoul storage integration")
    conn = psycopg.connect(dsn)
    if conn.info.host != "127.0.0.1" or not conn.info.dbname.startswith("pipeline_schema_test_"):
        conn.close()
        pytest.fail("Use a disposable loopback pipeline_schema_test_* DB")
    conn.execute("select 1")
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_actual_sql_new_repeat_and_order_keep_rows(db_conn: psycopg.Connection) -> None:
    api = api_transport()
    with patch("pipeline.collect_seoul.psycopg.connect", return_value=nullcontext(db_conn)):
        first_id, record, files = collect_and_save_one(
            SETTINGS,
            DatabaseSettings("postgresql://not-used/test"),
            api_transport=api,
        )
        before = db_conn.execute(
            "select id,file_key,kind,file_id,file_sn from notice_files "
            "where notice_id=%s order by id",
            (first_id,),
        ).fetchall()
        assert len(before) == 2 and all(r[3:] == (None, None) for r in before)
        api = api_transport()
        second_id, _, _ = collect_and_save_one(
            SETTINGS,
            DatabaseSettings("postgresql://not-used/test"),
            api_transport=api,
        )
    assert first_id == second_id
    assert save_notice_with_files(db_conn, record, tuple(reversed(files))) == first_id
    assert (
        db_conn.execute(
            "select id,file_key,kind,file_id,file_sn from notice_files "
            "where notice_id=%s order by id",
            (first_id,),
        ).fetchall()
        == before
    )
    assert db_conn.execute(
        "select category,source_board,post_sn,is_modified,body_html from notices where id=%s",
        (first_id,),
    ).fetchone() == ("seoul", "25", "000123", False, record.body_html)


def test_actual_sql_file_insert_failure_rolls_back_notice(db_conn: psycopg.Connection) -> None:
    api = api_transport()
    record, files = prepare_one(SETTINGS, api_transport=api)
    notice_id = save_notice_with_files(db_conn, record, files)
    before = db_conn.execute(
        "select id,kind,url from notice_files where notice_id=%s order by id",
        (notice_id,),
    ).fetchall()

    def reject(conn: psycopg.Connection, parent: int, file: object) -> None:
        # Real PostgreSQL CHECK failure, not a mocked exception.
        conn.execute(
            "insert into notice_files(notice_id,kind,file_key,url) "
            "values (%s,'attachment','bad',' ')",
            (parent,),
        )

    changed = replace(record, title="수정 제목")
    with patch("pipeline.storage.notice_bundle._insert_file", side_effect=reject):
        with pytest.raises(psycopg.errors.CheckViolation):
            save_notice_with_files(db_conn, changed, files[:1])
    assert db_conn.execute(
        "select title,is_modified from notices where id=%s",
        (notice_id,),
    ).fetchone() == (record.title, False)
    assert (
        db_conn.execute(
            "select id,kind,url from notice_files where notice_id=%s order by id",
            (notice_id,),
        ).fetchall()
        == before
    )


def test_actual_sql_new_notice_rolls_back_on_file_failure(db_conn: psycopg.Connection) -> None:
    api = api_transport()
    record, files = prepare_one(SETTINGS, api_transport=api)

    def reject(conn: psycopg.Connection, parent: int, file: object) -> None:
        conn.execute(
            "insert into notice_files(notice_id,kind,file_key,url) "
            "values (%s,'attachment','bad',' ')",
            (parent,),
        )

    with patch("pipeline.storage.notice_bundle._insert_file", side_effect=reject):
        with pytest.raises(psycopg.errors.CheckViolation):
            save_notice_with_files(db_conn, record, files)
    assert (
        db_conn.execute(
            "select count(*) from notices where category='seoul' "
            "and source_board='25' and post_sn='000123'",
        ).fetchone()[0]
        == 0
    )


def test_actual_sql_refresh_removes_known_decoration_but_preserves_body(
    db_conn: psycopg.Connection,
) -> None:
    from pipeline.collect_seoul import prepare_notice

    url = "https://culture.seoul.go.kr/_ui/images/main/cnl-common/nLc-logo-culture.png"
    raw = collect_one(SETTINGS, transport=api_transport())
    body = raw.body_html + f'<img src="{url}">'
    record, filtered = prepare_notice(replace(raw, body_html=body))
    decoration = replace(filtered[0], kind="inline_image", file_name="logo.png", url=url)
    notice_id = save_notice_with_files(db_conn, record, (*filtered, decoration))
    assert (
        db_conn.execute(
            "select count(*) from notice_files where notice_id=%s",
            (notice_id,),
        ).fetchone()[0]
        == 3
    )
    assert save_notice_with_files(db_conn, record, filtered) == notice_id
    assert db_conn.execute(
        "select body_html,is_modified from notices where id=%s",
        (notice_id,),
    ).fetchone() == (body, True)
    assert (
        db_conn.execute(
            "select count(*) from notice_files where notice_id=%s",
            (notice_id,),
        ).fetchone()[0]
        == 2
    )
    assert save_notice_with_files(db_conn, record, filtered) == notice_id
    assert (
        db_conn.execute(
            "select count(*) from notice_files where notice_id=%s and url=%s",
            (notice_id, url),
        ).fetchone()[0]
        == 0
    )
