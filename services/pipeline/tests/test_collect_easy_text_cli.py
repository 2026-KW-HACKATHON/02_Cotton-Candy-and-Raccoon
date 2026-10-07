"""Explicit collection opt-in reports easy-text work independently of raw saves."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from pipeline.after_collect import AfterCollectEasyText
from pipeline.cli import main
from pipeline.models import RawNotice
from pipeline.sources.wolgye1_board import BoardEntry
from pipeline.transform.dong import transform_dong_notice


@pytest.fixture
def collect_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOWON_NOTICE_API_KEY", "private-api-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:private-password@localhost/test")


def _report(*, failed: bool) -> dict[str, object]:
    return {
        "status": "failed" if failed else "complete",
        "complete": not failed,
        "attempted_count": 1,
        "successful_count": int(not failed),
        "skipped_count": 0,
        "failed_count": int(failed),
        "skipped": [],
        "failures": [{"notice_id": 42, "reason_code": "api_failed"}] if failed else [],
    }


def _processor(*, failed: bool) -> MagicMock:
    processor = MagicMock(spec=AfterCollectEasyText)
    processor.complete = not failed
    processor.report.return_value = _report(failed=failed)
    return processor


@pytest.mark.parametrize("source", ["nowon", "wolgye1"])
@pytest.mark.parametrize("mode", [None, "new", "refresh"])
@pytest.mark.parametrize("failed", [False, True])
def test_collect_opt_in_passes_callback_and_reports_separate_outcome(
    collect_env: None,
    capsys: pytest.CaptureFixture[str],
    source: str,
    mode: str | None,
    failed: bool,
) -> None:
    processor = _processor(failed=failed)
    result = SimpleNamespace(
        mode=mode,
        total_count=1,
        selected_count=1,
        saved_count=1,
        pages_read=1,
        initial_baseline=False,
        listing_complete=True,
        failed_ranges=(),
        failed_pages=(),
        complete=True,
        failures=(),
        listed_count=1,
        attempted_count=1,
        limited=False,
        duplicate_count=0,
    )

    def collect(*_args: object, **kwargs: object) -> SimpleNamespace:
        kwargs["after_save"](42)
        return result

    collector = f"pipeline.cli.collect_and_save_{source}"
    if mode:
        collector += "_scheduled"
    command = ["collect", "--source", source, "--easy-text"]
    if mode:
        command += ["--mode", mode]
    with (
        patch("pipeline.cli.AfterCollectEasyText", return_value=processor),
        patch(collector, side_effect=collect),
    ):
        assert main(command) == int(failed)
    processor.assert_called_once_with(42)
    output = capsys.readouterr()
    result_json = json.loads(output.out)
    assert result_json["complete"] is True
    assert result_json["saved_count"] == 1
    assert result_json["failures"] == []
    assert result_json["easy_text"] == _report(failed=failed)
    assert "private" not in output.out
    assert output.err == ""


def test_collection_without_flag_creates_no_processor(
    collect_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = SimpleNamespace(
        total_count=1,
        listed_count=1,
        attempted_count=1,
        saved_count=1,
        listing_complete=True,
        limited=False,
        complete=True,
        failed_pages=(),
        failures=(),
    )
    with (
        patch("pipeline.cli.AfterCollectEasyText") as processor,
        patch("pipeline.cli.collect_and_save_nowon", return_value=result) as collect,
    ):
        assert main(["collect", "--source", "nowon"]) == 0
    processor.assert_not_called()
    assert "after_save" not in collect.call_args.kwargs
    assert "easy_text" not in json.loads(capsys.readouterr().out)


def _raw(source: str) -> RawNotice:
    return RawNotice(
        category="nowon" if source == "nowon" else "dong",
        dong_group=None if source == "nowon" else "wolgye1",
        is_pinned=False,
        post_sn="123",
        title="제목",
        department="월계1동",
        registered_on="2026-09-28",
        body_html="<p>본문</p>",
        license_type="KOGL-4",
        url=(
            "https://www.nowon.kr/www/user/bbs/BD_selectBbs.do?q_bbsCode=1001&q_bbscttSn=123"
            if source == "nowon"
            else BoardEntry("123", "제목", "월계1동", "2026-09-28", False).url
        ),
    )


@pytest.mark.parametrize("source", ["nowon", "wolgye1"])
@pytest.mark.parametrize("failed", [False, True])
def test_collect_one_calls_processor_after_raw_connection_exit(
    collect_env: None,
    capsys: pytest.CaptureFixture[str],
    source: str,
    failed: bool,
) -> None:
    events: list[str] = []
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.side_effect = lambda *_args: events.append("raw-committed")
    processor = _processor(failed=failed)
    processor.side_effect = lambda _notice_id: events.append("easy-text")
    source_notice = _raw(source)
    with (
        patch("pipeline.cli.psycopg.connect", return_value=conn),
        patch("pipeline.cli.save_notice_with_files", return_value=42),
        patch("pipeline.cli.AfterCollectEasyText", return_value=processor),
        patch("pipeline.cli.collect_one", return_value=source_notice),
        patch("pipeline.cli.fetch_notice_page", return_value=(source_notice.url, "page")),
        patch("pipeline.cli.extract_files", return_value=[]),
        patch("pipeline.cli.extract_page_files", return_value=[]),
        patch(
            "pipeline.cli.collect_one_wolgye1",
            return_value=(transform_dong_notice(_raw("wolgye1")), []),
        ),
    ):
        assert main(["collect-one", "--source", source, "--easy-text"]) == int(failed)
    assert events == ["raw-committed", "easy-text"]
    processor.assert_called_once_with(42)
    output = json.loads(capsys.readouterr().out)
    assert output["notice_id"] == 42
    assert output["title"] == "제목"
    assert output["easy_text"] == _report(failed=failed)


@pytest.mark.parametrize("source", ["nowon", "wolgye1"])
def test_collect_one_storage_failure_never_calls_processor(
    collect_env: None,
    capsys: pytest.CaptureFixture[str],
    source: str,
) -> None:
    source_notice = _raw(source)
    conn = MagicMock()
    conn.__enter__.return_value = conn
    with (
        patch("pipeline.cli.psycopg.connect", return_value=conn),
        patch("pipeline.cli.save_notice_with_files", side_effect=psycopg.OperationalError),
        patch("pipeline.cli.AfterCollectEasyText") as processor,
        patch("pipeline.cli.collect_one", return_value=source_notice),
        patch("pipeline.cli.fetch_notice_page", return_value=(source_notice.url, "page")),
        patch("pipeline.cli.extract_files", return_value=[]),
        patch("pipeline.cli.extract_page_files", return_value=[]),
        patch(
            "pipeline.cli.collect_one_wolgye1",
            return_value=(transform_dong_notice(_raw("wolgye1")), []),
        ),
    ):
        assert main(["collect-one", "--source", source, "--easy-text"]) == 1
    processor.assert_not_called()
    assert capsys.readouterr().out == ""
