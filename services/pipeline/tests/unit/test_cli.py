import json
import os
from datetime import date
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg
import pytest

from pipeline.cli import main
from pipeline.collect_nowon import CollectNowonResult, NoticeFailure
from pipeline.collection_processing import CollectionPostprocessing
from pipeline.models import NoticeRecord, RawNotice
from pipeline.sources.nowon_page import NowonPageError
from pipeline.transform.nowon import TransformError


@pytest.fixture
def collect_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOWON_NOTICE_API_KEY", "private-api-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://pipeline:private-password@localhost/test")


@pytest.fixture
def raw_notice() -> RawNotice:
    return RawNotice(
        source_board="1001",
        category="nowon", dong_group=None, is_pinned=False, post_sn="00123",
        title="Notice", department=None, registered_on="2026-09-26",
        url=("https://www.nowon.kr/www/user/bbs/BD_selectBbs.do"
             "?q_bbsCode=1001&q_bbscttSn=00123"),
        body_html="<p>Content</p>", license_type="KOGL-4",
    )


def test_collect_one_saves_after_complete_collection(
    collect_env: None, raw_notice: RawNotice, capsys: pytest.CaptureFixture[str],
) -> None:
    conn = MagicMock()
    conn.__enter__.return_value = conn
    with (
        patch("pipeline.cli.collect_one", return_value=raw_notice),
        patch("pipeline.cli.fetch_notice_page", return_value=(raw_notice.url, "<html></html>")),
        patch("pipeline.cli.extract_files", return_value=[]),
        patch("pipeline.cli.extract_page_files", return_value=[]),
        patch("pipeline.cli.psycopg.connect", return_value=conn) as connect,
        patch("pipeline.cli.save_notice_with_files", return_value=42) as save,
    ):
        assert main(["collect-one", "--source", "nowon"]) == 0
    saved_notice, saved_files = save.call_args.args[1:]
    assert saved_notice == NoticeRecord(
        source_board="1001",
        category="nowon", dong_group=None, is_pinned=False, post_sn="00123",
        title="Notice", department=None, registered_on=date(2026, 9, 26),
        url=raw_notice.url, body_html="<p>Content</p>", license_type="KOGL-4",
    )
    assert saved_files == []
    connect.assert_called_once()
    assert json.loads(capsys.readouterr().out)["notice_id"] == 42


def test_collect_many_reports_partial_failure_without_secrets(
    collect_env: None, capsys: pytest.CaptureFixture[str],
) -> None:
    result = CollectNowonResult(
        total_count=3, listed_count=3, attempted_count=3, saved_count=2,
        listing_complete=True, limited=False, failed_pages=(),
        failures=(NoticeFailure("002", "page", "page_unavailable"),),
    )
    with patch("pipeline.cli.collect_and_save_nowon", return_value=result) as collect:
        assert main(["collect", "--source", "nowon"]) == 1
    collect.assert_called_once()
    output = capsys.readouterr()
    summary = json.loads(output.out)
    assert summary["complete"] is False
    assert summary["saved_count"] == 2
    assert summary["failures"] == [
        {"post_sn": "002", "stage": "page", "reason_code": "page_unavailable"},
    ]
    assert "private-api-key" not in output.out
    assert "private-password" not in output.out
    assert output.err == ""


def test_collect_many_complete_returns_success(
    collect_env: None, capsys: pytest.CaptureFixture[str],
) -> None:
    result = CollectNowonResult(
        total_count=2, listed_count=2, attempted_count=2, saved_count=2,
        listing_complete=True, limited=False, failed_pages=(), failures=(),
    )
    with patch("pipeline.cli.collect_and_save_nowon", return_value=result):
        assert main(["collect", "--source", "nowon"]) == 0
    assert json.loads(capsys.readouterr().out)["complete"] is True


def test_collect_many_rejects_bad_limit_before_network(
    collect_env: None, capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("pipeline.cli.collect_and_save_nowon") as collect:
        assert main(["collect", "--source", "nowon", "--limit", "0"]) == 2
    collect.assert_not_called()
    assert "--limit" in capsys.readouterr().err


@pytest.mark.parametrize("failed_step,error", [
    ("fetch_notice_page", NowonPageError("page unavailable")),
    ("transform_nowon_notice", TransformError("bad date")),
])
def test_collect_one_does_not_write_after_collection_failure(
    collect_env: None, raw_notice: RawNotice, capsys: pytest.CaptureFixture[str],
    failed_step: str, error: Exception,
) -> None:
    with (
        patch("pipeline.cli.collect_one", return_value=raw_notice),
        patch("pipeline.cli.fetch_notice_page", return_value=(raw_notice.url, "<html></html>")),
        patch("pipeline.cli.extract_files", return_value=[]),
        patch("pipeline.cli.extract_page_files", return_value=[]),
        patch(f"pipeline.cli.{failed_step}", side_effect=error),
        patch("pipeline.cli.psycopg.connect") as connect,
    ):
        assert main(["collect-one", "--source", "nowon"]) == 1
    connect.assert_not_called()
    assert capsys.readouterr().out == ""


def test_collect_one_db_failure_has_no_secret_in_output(
    collect_env: None, raw_notice: RawNotice, capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch("pipeline.cli.collect_one", return_value=raw_notice),
        patch("pipeline.cli.fetch_notice_page", return_value=(raw_notice.url, "<html></html>")),
        patch("pipeline.cli.extract_files", return_value=[]),
        patch("pipeline.cli.extract_page_files", return_value=[]),
        patch("pipeline.cli.psycopg.connect", side_effect=psycopg.OperationalError(
            "private-password private-api-key",
        )),
    ):
        assert main(["collect-one", "--source", "nowon"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "DB 저장 실패" in output.err
    assert "private-password" not in output.err
    assert "private-api-key" not in output.err


def test_collect_one_requires_database_url_before_network_request(
    collect_env: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DATABASE_URL")
    with patch("pipeline.cli.collect_one") as collect:
        assert main(["collect-one", "--source", "nowon"]) == 2
    collect.assert_not_called()
    assert "DATABASE_URL" in capsys.readouterr().err


def test_collect_one_twice_persists_one_notice_with_both_file_kinds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    database_url = os.getenv("PIPELINE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PIPELINE_TEST_DATABASE_URL is required for CLI DB integration")
    post_sn = "00" + uuid4().hex
    url = ("https://www.nowon.kr/www/user/bbs/BD_selectBbs.do"
           f"?q_bbsCode=1001&q_bbscttSn={post_sn}")
    source = RawNotice(
        source_board="1001",
        category="nowon", dong_group=None, is_pinned=False, post_sn=post_sn,
        title="Synthetic notice", department=None, registered_on="2026-09-26", url=url,
        body_html='<img src="/file?q_fileSn=1&amp;q_fileId=image-a">',
        license_type="KOGL-4",
    )
    page_html = ('<table><tr><th>첨부파일</th><td><ul class="file-list">'
                 '<li><a href="/file?q_fileSn=2&amp;q_fileId=file-b">attachment.pdf</a>'
                 '</li></ul></td></tr></table>')
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("NOWON_NOTICE_API_KEY", "sample")
    try:
        with (
            patch("pipeline.cli.collect_one", return_value=source),
            patch("pipeline.cli.fetch_notice_page", return_value=(url, page_html)),
        ):
            assert main(["collect-one", "--source", "nowon"]) == 0
            first = json.loads(capsys.readouterr().out)
            assert main(["collect-one", "--source", "nowon"]) == 0
            second = json.loads(capsys.readouterr().out)
        assert first["notice_id"] == second["notice_id"]
        assert first["attachment_count"] == first["inline_image_count"] == 1
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "select id, body_html, is_modified from notices "
                    "where category = 'nowon' and post_sn = %s", (post_sn,),
                )
                assert cursor.fetchall() == [
                    (first["notice_id"], source.body_html, False),
                ]
                cursor.execute(
                    "select kind, file_id from notice_files where notice_id = %s "
                    "order by kind", (first["notice_id"],),
                )
                assert cursor.fetchall() == [
                    ("attachment", "file-b"), ("inline_image", "image-a"),
                ]
    finally:
        with psycopg.connect(database_url) as conn:
            conn.execute(
                "delete from notices where category = 'nowon' and post_sn = %s", (post_sn,),
            )


def test_check_config_does_not_print_secrets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_url = "postgresql://pipeline:secret-password@localhost/postgres"
    api_key = "secret-api-key"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("SEOUL_API_KEY", api_key)

    exit_code = main(["check-config"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert database_url not in output
    assert api_key not in output


def test_check_config_missing_values_returns_failure(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check-config"]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "DATABASE_URL" in output.err
    assert "SEOUL_API_KEY" in output.err


@pytest.mark.parametrize("name,value", [
    ("DATABASE_URL", "https://user:secret-password@localhost/db"),
    ("HTTP_READ_TIMEOUT_SECONDS", "private-invalid-timeout"),
])
def test_check_config_failure_does_not_print_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    name: str, value: str,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/db")
    monkeypatch.setenv("SEOUL_API_KEY", "secret-api-key")
    monkeypatch.setenv(name, value)
    assert main(["check-config"]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert name in output.err
    assert value not in output.err
    assert "secret-api-key" not in output.err


@pytest.mark.parametrize("source", ["nowon", "wolgye1", "seoul"])
@pytest.mark.parametrize("command", ["collect", "collect-one"])
def test_combined_ai_requires_runner_before_any_collection(
    source: str, command: str, collect_env: None,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SEOUL_NEWS_API_KEY", "private-api-key")
    args = [command, "--source", source, "--process-ai"]
    if command == "collect":
        args += ["--mode", "new"]
    with (
        patch("httpx.Client") as http,
        patch("psycopg.connect") as connect,
        patch("pipeline.cli.create_easy_text_processing") as legacy,
    ):
        assert main(args) == 2
    http.assert_not_called()
    connect.assert_not_called()
    legacy.assert_not_called()
    output = capsys.readouterr()
    assert "#61" in output.err and "#60" in output.err
    assert output.out == ""
    assert "private" not in output.err


@pytest.mark.parametrize("command", ["collect", "collect-one"])
def test_two_processing_modes_cannot_run_together(command: str) -> None:
    with pytest.raises(SystemExit) as stopped:
        main([command, "--source", "nowon", "--easy-text", "--process-ai"])
    assert stopped.value.code == 2


def test_raw_collection_finishes_before_easy_text_requests(
    collect_env: None, capsys: pytest.CaptureFixture[str],
) -> None:
    events = []
    legacy = MagicMock(side_effect=lambda notice_id: events.append(("ai", notice_id)))
    legacy.report.return_value = {"complete": True, "successful_count": 2}

    def collect(*args, after_save, **kwargs):
        for notice_id in (42, 43):
            events.append(("committed", notice_id))
            after_save(notice_id)
        events.append(("collector_closed", None))
        return CollectNowonResult(
            total_count=2, listed_count=2, attempted_count=2, saved_count=2,
            listing_complete=True, limited=False, failed_pages=(), failures=(),
        )

    with (
        patch("pipeline.cli.collect_and_save_nowon", side_effect=collect),
        patch("pipeline.collection_processing.AfterCollectEasyText", return_value=legacy),
    ):
        assert main(["collect", "--source", "nowon", "--easy-text"]) == 0
    assert events == [
        ("committed", 42), ("committed", 43), ("collector_closed", None), ("ai", 42), ("ai", 43),
    ]
    assert json.loads(capsys.readouterr().out)["easy_text"]["successful_count"] == 2


@pytest.mark.parametrize("failed_feature", ["summary", "easy_text"])
def test_common_runner_reports_both_features_without_changing_collection_success(
    collect_env: None, capsys: pytest.CaptureFixture[str], failed_feature: str,
) -> None:
    reports = {
        feature: {"complete": feature != failed_feature}
        for feature in ("summary", "easy_text")
    }
    runner = MagicMock(return_value=reports)
    processor = CollectionPostprocessing(("summary", "easy_text"), runner)

    def collect(*args, after_save, **kwargs):
        after_save(42)
        runner.assert_not_called()
        return CollectNowonResult(
            total_count=1, listed_count=1, attempted_count=1, saved_count=1,
            listing_complete=True, limited=False, failed_pages=(), failures=(),
        )

    with (
        patch("pipeline.cli.create_ai_processing", return_value=processor),
        patch("pipeline.cli.create_easy_text_processing") as legacy,
        patch("pipeline.cli.collect_and_save_nowon", side_effect=collect),
    ):
        assert main(["collect", "--source", "nowon", "--process-ai"]) == 1
    legacy.assert_not_called()
    runner.assert_called_once_with((42,))
    report = json.loads(capsys.readouterr().out)
    assert report["complete"] is True and report["saved_count"] == 1
    assert {feature: report[feature] for feature in reports} == reports
