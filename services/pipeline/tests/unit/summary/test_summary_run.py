"""summarize-one against a real migrated PostgreSQL; only Gemini and HTTP are replaced."""

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from google import genai
from psycopg.rows import dict_row
from support.db import database_uri, owned_migrated_database
from support.prepared_summary_storage import _response
from support.summary_bundle import PNG, URL

from pipeline import cli, clock
from pipeline.config import DatabaseSettings
from pipeline.storage.summaries import begin_summary_execution
from pipeline.summary_run import summarize_one

BODY = "<p>행사 안내</p>"
FIXED_NOW = datetime(2026, 10, 8, 3, 0, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[dict[str, str]]:
    """A database owned by one test: summarize-one commits through its own connections."""
    with owned_migrated_database(
        env="PIPELINE_TEST_DATABASE_URL",
        required_prefix="pipeline_schema_test_",
        missing="skip",
        missing_message="PIPELINE_TEST_DATABASE_URL 미설정: 요약 실행 통합 테스트 생략",
        invalid_message="요약 실행 검증은 전용 로컬 테스트 DB에서만 가능합니다.",
    ) as info:
        yield info


@pytest.fixture
def settings(db) -> DatabaseSettings:
    return DatabaseSettings(database_uri(db))


class Gemini:
    """Replaces the SDK client; each reply is a payload, an exception, or a callable."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.replies: list[object] = []
        self.calls = 0
        monkeypatch.setattr(genai, "Client", self._client)

    def _client(self, **_: object) -> object:
        outer = self

        class Client:
            interactions = SimpleNamespace(create=outer._create)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return None

        return Client()

    def _create(self, **_: object) -> object:
        self.calls += 1
        if not self.replies:
            raise AssertionError("unexpected Gemini call")
        reply = self.replies.pop(0)
        if callable(reply):
            reply = reply()
        if isinstance(reply, BaseException):
            raise reply
        text = json.dumps(reply, ensure_ascii=False)
        return SimpleNamespace(status="completed", errors=None, output_text=text)


@pytest.fixture
def gemini(monkeypatch) -> Gemini:
    return Gemini(monkeypatch)


@pytest.fixture
def downloads(monkeypatch) -> dict[str, httpx.Response]:
    """URL -> response for attachment downloads; any other request fails the test."""
    responses: dict[str, httpx.Response] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url not in responses:
            raise AssertionError(f"unexpected request {url}")
        stored = responses[url]
        return httpx.Response(stored.status_code, headers=stored.headers, content=stored.content)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)
    return responses


def _notice(
    db: dict[str, str], *, body: str | None = BODY, visible: bool = True,
    files: tuple[tuple[str, str], ...] = (),
) -> int:
    with psycopg.connect(**db, autocommit=True) as conn:
        notice_id = conn.execute(
            "insert into notices(category,source_board,post_sn,title,registered_on,url,"
            "body_html,is_visible) values ('nowon','1001',%s,'행사 안내','2026-10-08',"
            "'https://www.nowon.kr/www/user/bbs/BD_selectBbs.do',%s,%s) returning id",
            (f"run-{datetime.now().timestamp()}", body, visible),
        ).fetchone()[0]
        for index, (name, url) in enumerate(files, 1):
            conn.execute(
                "insert into notice_files(notice_id,kind,file_key,file_id,file_name,url) "
                "values (%s,'attachment',%s,%s,%s,%s)",
                (notice_id, f"id:{index}", str(index), name, url),
            )
    return notice_id


def _row(db: dict[str, str], notice_id: int) -> dict | None:
    with psycopg.connect(**db, row_factory=dict_row) as conn:
        return conn.execute(
            "select * from notice_summaries where notice_id=%s", (notice_id,)
        ).fetchone()


def _anon_row(db: dict[str, str], notice_id: int) -> dict | None:
    with psycopg.connect(**db, row_factory=dict_row) as conn, conn.transaction():
        conn.execute("set local role anon")
        return conn.execute(
            "select notice_id, status, result from notice_summaries where notice_id=%s",
            (notice_id,),
        ).fetchone()


def _review_response() -> dict:
    data = _response("text")
    data["uncertainties"] = ["행사 일정은 원문 확인 필요"]
    return data


def test_first_summary_is_committed_and_readable_by_the_app(db, settings, gemini):
    notice_id = _notice(db)
    gemini.replies.append(_response("text"))
    result = summarize_one(settings, notice_id, api_key="test-key")
    report = result.report()
    assert {k: report[k] for k in report if k != "view"} == {
        "notice_id": notice_id,
        "execution_status": "summarized",
        "stored_status": "summarized",
        "public_result": True,
        "attachment_status": "none",
        "reason_code": None,
        "gemini_called": True,
        "gemini_requests": 1,
    }
    assert result.exit_code == 0
    assert report["view"]["status"] == "summarized"
    assert report["view"]["content"]["headline"]["text"] == "행사 안내"
    anon = _anon_row(db, notice_id)
    assert anon["status"] == "summarized" and anon["result"]["summary"] == "행사 안내"


def test_review_result_is_stored_and_reported_with_a_reason(db, settings, gemini):
    notice_id = _notice(db)
    gemini.replies.append(_review_response())
    result = summarize_one(settings, notice_id, api_key="test-key")
    assert result.execution_status == result.stored_status == "needs_review"
    assert result.reason_code == "summary_review_required"
    assert result.public_result is True and result.exit_code == 0


@pytest.mark.parametrize("hidden", [False, True], ids=["missing", "hidden"])
def test_missing_or_hidden_notice_is_not_found_without_any_write(db, settings, gemini, hidden):
    notice_id = _notice(db, visible=False) if hidden else 999_999
    result = summarize_one(settings, notice_id, api_key="test-key")
    assert (result.execution_status, result.reason_code, result.exit_code) == (
        "not_found", "notice_not_found_or_hidden", 3,
    )
    assert result.stored_status is None and result.public_result is False
    assert gemini.calls == 0
    assert _row(db, notice_id) is None


def test_notice_without_readable_content_fails_without_calling_gemini(db, settings, gemini):
    notice_id = _notice(db, body=None)
    result = summarize_one(settings, notice_id, api_key="test-key")
    # The preparer records a no_content failure; summary_job stores every preparation
    # failure as input_preparation_failed before any Gemini request.
    assert (result.execution_status, result.reason_code, result.exit_code) == (
        "failed", "input_preparation_failed", 1,
    )
    assert result.gemini_called is False and gemini.calls == 0
    assert result.stored_status == "failed" and result.public_result is False


def test_unread_attachment_is_summarized_from_the_body_and_reported(
    db, settings, gemini, downloads
):
    notice_id = _notice(db, files=(("poster.png", URL + "1"),))
    downloads[URL + "1"] = httpx.Response(503)
    gemini.replies.append(_response("text"))
    result = summarize_one(settings, notice_id, api_key="test-key")
    assert result.attachment_status == "unread"
    assert result.execution_status == "needs_review"
    assert result.reason_code == "attachments_unread"
    assert result.gemini_called is True
    row = _row(db, notice_id)
    assert row["attachment_status"] == "unread"
    assert row["preparation_omissions"]
    assert result.report()["view"]["preparation_omissions"]


def test_gemini_timeout_keeps_the_existing_public_summary(db, settings, gemini):
    notice_id = _notice(db)
    gemini.replies.append(_response("text"))
    assert summarize_one(settings, notice_id, api_key="test-key").exit_code == 0
    before = _row(db, notice_id)
    gemini.replies.append(httpx.ReadTimeout("timeout"))
    result = summarize_one(settings, notice_id, api_key="test-key")
    assert (result.execution_status, result.reason_code, result.exit_code) == (
        "failed", "api_timeout", 1,
    )
    assert result.stored_status == "summarized" and result.public_result is True
    assert result.report()["view"]["status"] == "summarized"
    after = _row(db, notice_id)
    assert after["result"] == before["result"] and after["status"] == "summarized"
    assert after["last_error_code"] == "api_timeout"


def test_temporary_attachment_failure_keeps_the_existing_public_result(
    db, settings, gemini, downloads
):
    notice_id = _notice(db, files=(("poster.png", URL + "1"),))
    downloads[URL + "1"] = httpx.Response(
        200, headers={"content-type": "image/png"}, content=PNG
    )
    gemini.replies.append(_response("image"))
    first = summarize_one(settings, notice_id, api_key="test-key")
    assert first.public_result is True
    before = _row(db, notice_id)
    downloads[URL + "1"] = httpx.Response(503)
    gemini.replies.append(_response("text"))
    second = summarize_one(settings, notice_id, api_key="test-key")
    assert second.attachment_status == "unread"
    assert second.public_result is True
    after = _row(db, notice_id)
    for key in ("status", "result", "file_manifest", "file_references", "attachment_status"):
        assert after[key] == before[key]


def _concurrent(db: dict[str, str], sql: Callable[[psycopg.Connection, int], None], notice_id):
    def change() -> dict:
        with psycopg.connect(**db, autocommit=True) as other:
            sql(other, notice_id)
        return _response("text")

    return change


@pytest.mark.parametrize(
    "interference",
    [
        lambda conn, nid: conn.execute(
            "update notices set body_html='<p>행사 안내 변경</p>' where id=%s", (nid,)
        ),
        lambda conn, nid: begin_summary_execution(conn, nid),
    ],
    ids=["source-changed", "newer-execution"],
)
def test_stale_execution_is_superseded_and_never_reported_as_success(
    db, settings, gemini, interference
):
    notice_id = _notice(db)
    gemini.replies.append(_concurrent(db, interference, notice_id))
    result = summarize_one(settings, notice_id, api_key="test-key")
    assert (result.execution_status, result.reason_code, result.exit_code) == (
        "superseded", "summary_execution_superseded", 4,
    )
    assert result.public_result is False
    row = _row(db, notice_id)
    assert row is None or row["result"] is None


def test_storage_failure_is_reported_and_nothing_is_committed(db, settings, gemini):
    notice_id = _notice(db)
    with psycopg.connect(**db, autocommit=True) as conn:
        conn.execute(
            "create function reject_summary() returns trigger language plpgsql as "
            "$$ begin raise exception 'storage unavailable'; end $$"
        )
        conn.execute(
            "create trigger reject_summary before insert or update on notice_summaries "
            "for each row execute function reject_summary()"
        )
    gemini.replies.append(_response("text"))
    result = summarize_one(settings, notice_id, api_key="test-key")
    assert result.execution_status == "storage_failed" and result.exit_code == 5
    assert result.public_result is False and result.stored_status is None
    assert "storage unavailable" not in json.dumps(result.report())
    assert _row(db, notice_id) is None


def test_unreachable_database_is_storage_failed_before_any_work(gemini):
    settings = DatabaseSettings("postgresql://postgres@127.0.0.1:1/pipeline_schema_test_none")
    result = summarize_one(settings, 1, api_key="test-key")
    assert (result.execution_status, result.reason_code) == ("storage_failed", "db_unavailable")
    assert gemini.calls == 0


def test_one_clock_sets_reference_time_and_generated_at(db, settings, gemini, monkeypatch):
    seen: list[object] = []
    original = gemini._create

    def create(**kwargs):
        seen.append(kwargs["input"])
        return original(**kwargs)

    gemini._create = create
    monkeypatch.setattr(clock, "now", lambda: FIXED_NOW)
    notice_id = _notice(db)
    gemini.replies.append(_response("text"))
    summarize_one(settings, notice_id, api_key="test-key")
    assert _row(db, notice_id)["generated_at"] == FIXED_NOW
    assert "2026-10-08T12:00:00+09:00" in json.dumps(seen, ensure_ascii=False)


def test_cli_prints_one_json_line_and_uses_the_execution_exit_code(
    db, settings, gemini, monkeypatch, capsys
):
    notice_id = _notice(db)
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    gemini.replies.append(httpx.ReadTimeout("timeout"))
    assert cli.main(["summarize-one", "--notice-id", str(notice_id)]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    report = json.loads(lines[0])
    assert report["execution_status"] == "failed" and report["reason_code"] == "api_timeout"
    assert settings.database_url not in lines[0] and "test-key" not in lines[0]


@pytest.mark.parametrize("missing", ["DATABASE_URL", "GEMINI_API_KEY"])
def test_cli_configuration_errors_exit_2_before_touching_the_database(
    db, settings, gemini, monkeypatch, tmp_path, capsys, missing
):
    notice_id = _notice(db)
    monkeypatch.setattr("pipeline.transform.gemini_prompt.DEFAULT_ENV_PATH", tmp_path / ".env")
    for name, value in (("DATABASE_URL", settings.database_url), ("GEMINI_API_KEY", "test-key")):
        if name == missing:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert cli.main(["summarize-one", "--notice-id", str(notice_id)]) == 2
    assert "설정 오류" in capsys.readouterr().err
    assert gemini.calls == 0 and _row(db, notice_id) is None


def test_no_transaction_or_lock_is_held_while_gemini_runs(db, settings, gemini):
    notice_id = _notice(db)
    observed: dict[str, int] = {}

    def inspect() -> dict:
        with psycopg.connect(**db, autocommit=True) as other:
            observed["open_transactions"] = other.execute(
                "select count(*) from pg_stat_activity where datname = current_database() "
                "and pid <> pg_backend_pid() and state like 'idle in transaction%%'"
            ).fetchone()[0]
            observed["source_locks"] = other.execute(
                "select count(*) from pg_locks l join pg_class c on c.oid = l.relation "
                "where c.relname in ('notices', 'notice_files', 'notice_summary_executions') "
                "and l.pid <> pg_backend_pid()"
            ).fetchone()[0]
        return _response("text")

    gemini.replies.append(inspect)
    assert summarize_one(settings, notice_id, api_key="test-key").exit_code == 0
    assert observed == {"open_transactions": 0, "source_locks": 0}
