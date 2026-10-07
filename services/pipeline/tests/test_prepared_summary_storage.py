"""Connect actual summary transforms to mock storage without live API or DB access."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
from test_gemini_multimodal import PreparationIssue, _media, _notice, _prepared

from pipeline import summary_job
from pipeline.storage.summaries import (
    SummaryStorageError,
    record_summary_failure,
    save_prepared_summary,
)
from pipeline.storage.summary_record import (
    SummaryMetadata,
    SummaryRecordError,
    build_summary_record,
)
from pipeline.transform import summarize as summarize_module
from pipeline.transform.gemini_client import DEFAULT_MODEL, GeminiRequestError
from pipeline.transform.gemini_prompt import GeminiConfigurationError
from pipeline.transform.grounding import REVIEW_NOTE, unknown_summary
from pipeline.transform.prepared_summary import PreparedSummaryResult, SummaryPreparationError
from pipeline.transform.summary_schema import NoticeSummary, SummaryValidationError

PRIVATE_MARKER = "MOCK_PRIVATE_PREPARATION_WARNING_123"
GENERATED_AT = datetime(2026, 10, 3, 7, tzinfo=UTC)
DEADLINE = date(2026, 10, 20)


def _metadata(attachment_status: str = "all_read") -> SummaryMetadata:
    return SummaryMetadata(
        source_hash="cd" * 32,
        model=DEFAULT_MODEL,
        prompt_version="summary-v3",
        attachment_status=attachment_status,
    )


def _response(kind: str) -> dict[str, Any]:
    data = unknown_summary(_notice("행사 안내")).model_dump(mode="json")
    data.update(category="event", summary="행사 안내", uncertainties=[])
    evidence: dict[str, Any] = {"field": "summary", "excerpt": "행사 안내"}
    if kind in {"pdf", "image"}:
        evidence.update(
            source_type="document" if kind == "pdf" else "image",
            source_id="media_1",
            page=1 if kind == "pdf" else None,
        )
    data["evidence"] = [evidence]
    if kind == "mixed":
        data["dates"] = [
            {
                "kind": "event",
                "label": "행사",
                "text": "2026-10-03~2026-10-20",
                "start_date": "2026-10-03",
                "end_date": "2026-10-20",
                "start_time": None,
                "end_time": None,
            }
        ]
        data["notes"] = ["참가비 무료"]
        data["evidence"].extend(
            [
                {
                    "field": "dates",
                    "excerpt": "행사: 2026-10-03~2026-10-20",
                    "source_type": "document",
                    "source_id": "media_2",
                    "page": 2,
                },
                {
                    "field": "notes",
                    "excerpt": "참가비 무료",
                    "source_type": "image",
                    "source_id": "media_1",
                    "page": None,
                },
            ]
        )
    return data


def _prepared_source(kind: str) -> Any:
    if kind == "text":
        return _prepared("행사 안내")
    if kind == "hwp":
        return _prepared("", attachment_text=True)
    if kind == "pdf":
        return _prepared("", _media("document"))
    if kind == "image":
        return _prepared("", _media("image"))
    return _prepared("행사 안내", _media("image"), _media("document"), attachment_text=True)


def _summarize(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> tuple[Any, PreparedSummaryResult, list[dict[str, Any]]]:
    prepared = _prepared_source(kind)
    calls: list[dict[str, Any]] = []

    def generate(**kwargs: Any) -> str:
        calls.append(deepcopy(kwargs))
        return json.dumps(_response(kind), ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    result = summarize_module.summarize_prepared_notice(prepared, api_key="test-key")
    return prepared, result, calls


def _connection(row: Any = (17,)) -> tuple[MagicMock, MagicMock]:
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = row
    return conn, cursor


def _assert_caller_keeps_transaction(conn: MagicMock) -> None:
    conn.commit.assert_not_called()
    conn.rollback.assert_not_called()
    conn.close.assert_not_called()


@pytest.mark.parametrize("kind", ["text", "hwp", "pdf", "image", "mixed"])
def test_actual_prepared_summary_then_mock_storage_preserves_all_sources_and_result(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    prepared, result, calls = _summarize(monkeypatch, kind)
    assert len(calls) == 1
    assert result.summary.summary == "행사 안내"
    assert result.summary.uncertainties == []
    sent = calls[0]["notice_text"]
    assert [block for block in sent if block["type"] != "text"] == [
        block for block in prepared.blocks if block["type"] != "text"
    ]
    if kind in {"hwp", "mixed"}:
        assert "안내.hwp" in sent[0]["text"]
    conn, cursor = _connection()
    stored = save_prepared_summary(
        conn, result, _metadata(), deadline_on=DEADLINE, generated_at=GENERATED_AT
    )
    values = cursor.execute.call_args.args[1]
    needs_review = kind in {"pdf", "image", "mixed"}
    assert stored.notice_id == 17
    assert stored.status == ("needs_review" if needs_review else "summarized")
    assert stored.deadline_on == (None if needs_review else DEADLINE)
    assert stored.generated_at == GENERATED_AT
    assert stored.result.summary.model_dump(mode="json") == result.summary.model_dump(mode="json")
    assert stored.result.media_sources == result.media_sources
    if needs_review:
        assert values[2:5] == (None, None, None)
    else:
        assert values[2].obj == result.summary.model_dump(mode="json")
        assert values[3:5] == ("event", DEADLINE)
    assert values[6:10] == ("cd" * 32, DEFAULT_MODEL, "summary-v3", 1)
    if kind in {"pdf", "image"}:
        assert stored.result.summary.evidence[0].verification == "file_reference_only"
    if kind == "mixed":
        assert {item.verification for item in stored.result.summary.evidence} == {
            "text_matched",
            "file_reference_only",
        }
        assert stored.result.summary.evidence[1].page == 2
    _assert_caller_keeps_transaction(conn)


def test_warnings_are_preserved_but_do_not_alone_change_status_or_emit_messages(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    recwarn: pytest.WarningsRecorder,
) -> None:
    prepared = _prepared_source("hwp")
    warnings = (PreparationIssue("hwp", 8, PRIVATE_MARKER),)
    prepared.warnings = warnings
    monkeypatch.setattr(
        summarize_module,
        "generate_summary_json",
        lambda **_kwargs: json.dumps(_response("hwp"), ensure_ascii=False),
    )
    result = summarize_module.summarize_prepared_notice(prepared, api_key="test-key")
    conn, cursor = _connection()
    stored = save_prepared_summary(
        conn, result, _metadata(), deadline_on=DEADLINE, generated_at=GENERATED_AT
    )
    assert stored.status == "summarized"
    assert stored.result.warnings == warnings
    assert result.warnings == warnings
    assert prepared.warnings == warnings
    assert "warnings" not in cursor.execute.call_args.args[1][2].obj
    captured = capsys.readouterr()
    assert PRIVATE_MARKER not in caplog.text + captured.out + captured.err
    assert caplog.records == []
    assert captured.out == ""
    assert captured.err == ""
    assert not recwarn.list
    _assert_caller_keeps_transaction(conn)


@pytest.mark.parametrize("review_reason", ["unknown", "uncertainties", "partial", "unread"])
def test_review_conditions_keep_a_private_snapshot_without_publishing_summary_or_deadline(
    monkeypatch: pytest.MonkeyPatch, review_reason: str
) -> None:
    _, result, _ = _summarize(monkeypatch, "text")
    metadata = _metadata(review_reason if review_reason in {"partial", "unread"} else "all_read")
    if review_reason == "unknown":
        result = replace(result, summary=unknown_summary(_notice("행사 안내")))
    elif review_reason == "uncertainties":
        result = replace(
            result,
            summary=result.summary.model_copy(update={"uncertainties": [REVIEW_NOTE]}),
        )
    record = build_summary_record(result, metadata, deadline_on=DEADLINE, generated_at=GENERATED_AT)
    assert record.status == "needs_review"
    assert record.result is None
    assert record.deadline_on is None
    conn, cursor = _connection()
    stored = save_prepared_summary(
        conn, result, metadata, deadline_on=DEADLINE, generated_at=GENERATED_AT
    )
    assert stored.status == "needs_review"
    assert stored.deadline_on is None
    assert stored.result.summary.model_dump(mode="json") == result.summary.model_dump(mode="json")
    assert stored.result.media_sources == result.media_sources
    assert cursor.execute.call_args.args[1][2:5] == (None, None, None)
    _assert_caller_keeps_transaction(conn)


@pytest.mark.parametrize("increment", [0, 1])
def test_storage_connector_forwards_execution_increment_without_counting_http_retries(
    monkeypatch: pytest.MonkeyPatch, increment: int
) -> None:
    prepared = _prepared_source("image")
    responses = iter(("{}", json.dumps(_response("image"))))
    calls: list[str] = []

    def generate(**_kwargs: Any) -> str:
        calls.append("API attempt")
        return next(responses)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    result = summarize_module.summarize_prepared_notice(prepared, api_key="test-key")
    assert len(calls) == 2
    conn, cursor = _connection()
    save_prepared_summary(
        conn,
        result,
        _metadata(),
        deadline_on=None,
        generated_at=GENERATED_AT,
        attempt_increment=increment,
    )
    assert cursor.execute.call_args.args[1][9] == increment
    cursor.execute.assert_called_once()
    _assert_caller_keeps_transaction(conn)


@pytest.mark.parametrize("failure_kind", ["api_timeout", "input_too_large", "json", "preparation"])
def test_summary_failure_is_written_as_failed_without_any_success_json(
    monkeypatch: pytest.MonkeyPatch, failure_kind: str
) -> None:
    prepared = _prepared_source("pdf")
    calls: list[str] = []

    def generate(**_kwargs: Any) -> str:
        calls.append("API attempt")
        if failure_kind == "json":
            return "invalid JSON " + PRIVATE_MARKER
        raise GeminiRequestError(PRIVATE_MARKER, reason_code=failure_kind)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    if failure_kind == "preparation":
        prepared.failures = (PreparationIssue("file", 12, "timeout"),)
    with pytest.raises(
        (GeminiRequestError, SummaryValidationError, SummaryPreparationError)
    ) as error:
        summarize_module.summarize_prepared_notice(prepared, api_key="test-key")
    if failure_kind == "preparation":
        assert calls == []
    elif failure_kind == "json":
        assert len(calls) == 2
    else:
        assert len(calls) == 1
    conn, cursor = _connection()
    assert record_summary_failure(conn, 17, _metadata(), reason_code=error.value.reason_code) == 17
    values = cursor.execute.call_args.args[1]
    assert values[1] == "failed"
    assert values[2:5] == (None, None, None)
    assert values[10] == error.value.reason_code
    assert values[11] is None
    assert PRIVATE_MARKER not in str(values[10])
    _assert_caller_keeps_transaction(conn)


@pytest.mark.parametrize("reason_code", [PRIVATE_MARKER, "error: private payload", True, None])
def test_failure_connector_rejects_unsafe_codes_before_any_database_write(reason_code: Any) -> None:
    conn, _ = _connection()
    with pytest.raises(SummaryRecordError, match="invalid_error_code"):
        record_summary_failure(conn, 17, _metadata(), reason_code=reason_code)
    conn.cursor.assert_not_called()
    _assert_caller_keeps_transaction(conn)


@pytest.mark.parametrize("missing_id", [False, True])
def test_save_connector_cannot_return_success_after_a_storage_failure(
    monkeypatch: pytest.MonkeyPatch, missing_id: bool
) -> None:
    _, result, _ = _summarize(monkeypatch, "mixed")
    conn, cursor = _connection(None if missing_id else (17,))
    if not missing_id:
        cursor.execute.side_effect = psycopg.OperationalError(PRIVATE_MARKER)
    with pytest.raises(SummaryStorageError) as error:
        save_prepared_summary(
            conn, result, _metadata(), deadline_on=DEADLINE, generated_at=GENERATED_AT
        )
    assert PRIVATE_MARKER not in str(error.value)
    _assert_caller_keeps_transaction(conn)


def test_failure_connector_storage_error_does_not_turn_into_a_saved_failure_success() -> None:
    conn, cursor = _connection()
    cursor.execute.side_effect = psycopg.IntegrityError(PRIVATE_MARKER)
    with pytest.raises(SummaryStorageError):
        record_summary_failure(conn, 17, _metadata(), reason_code="api_error")
    _assert_caller_keeps_transaction(conn)


@pytest.mark.parametrize("kind", ["text", "pdf"])
def test_saved_result_is_a_snapshot_and_only_summarized_results_publish_json(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    _, result, _ = _summarize(monkeypatch, kind)
    conn, cursor = _connection()
    stored = save_prepared_summary(
        conn, result, _metadata(), deadline_on=DEADLINE, generated_at=GENERATED_AT
    )
    result.summary.summary = "나중에 변경한 요약"
    result.summary.evidence[0].page = 9
    assert stored.result.summary.summary == "행사 안내"
    assert stored.result.summary.evidence[0].page == (1 if kind == "pdf" else None)
    if kind == "pdf":
        assert cursor.execute.call_args.args[1][2:5] == (None, None, None)
    else:
        assert cursor.execute.call_args.args[1][2].obj == (
            stored.result.summary.model_dump(mode="json")
        )


@pytest.mark.parametrize("kind", ["text", "hwp", "pdf", "image", "mixed"])
def test_integrated_job_summarizes_each_input_then_saves_with_model_and_utc_time(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    prepared = _prepared_source(kind)
    conn, cursor = _connection()
    metadata = replace(_metadata(), model="gemini-test-model-selection")
    requests: list[dict[str, Any]] = []

    def generate(**kwargs: Any) -> str:
        conn.cursor.assert_not_called()
        requests.append(deepcopy(kwargs))
        return json.dumps(_response(kind), ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    deadline_resolver = MagicMock(return_value=DEADLINE)
    before = datetime.now(UTC)
    stored = summary_job.summarize_and_save_prepared_notice(
        conn, prepared, metadata, deadline_resolver=deadline_resolver, api_key="test-key"
    )
    after = datetime.now(UTC)
    needs_review = kind in {"pdf", "image", "mixed"}
    assert stored.status == ("needs_review" if needs_review else "summarized")
    assert stored.notice_id == prepared.notice_id
    assert stored.result.summary.summary == "행사 안내"
    assert len(requests) == 1
    assert requests[0]["model"] == metadata.model
    assert [block for block in requests[0]["notice_text"] if block["type"] != "text"] == [
        block for block in prepared.blocks if block["type"] != "text"
    ]
    if needs_review:
        deadline_resolver.assert_not_called()
    else:
        deadline_resolver.assert_called_once()
        assert deadline_resolver.call_args.args[0].model_dump(mode="json") == (
            stored.result.summary.model_dump(mode="json")
        )
    values = cursor.execute.call_args.args[1]
    assert values[:2] == (17, stored.status)
    if needs_review:
        assert values[2:5] == (None, None, None)
    else:
        assert values[2].obj == stored.result.summary.model_dump(mode="json")
        assert values[4] == DEADLINE
    assert values[6:10] == (metadata.source_hash, metadata.model, metadata.prompt_version, 1)
    assert before <= values[11] <= after
    assert values[11].utcoffset().total_seconds() == 0
    assert stored.generated_at == values[11]
    cursor.execute.assert_called_once()
    _assert_caller_keeps_transaction(conn)


@pytest.mark.parametrize("review_reason", ["unknown", "uncertainties", "partial", "unread"])
def test_integrated_review_outcome_never_calls_the_deadline_resolver(
    monkeypatch: pytest.MonkeyPatch, review_reason: str
) -> None:
    prepared = _prepared_source("text")
    data = _response("text")
    if review_reason == "unknown":
        data = unknown_summary(_notice("행사 안내")).model_dump(mode="json")
    elif review_reason == "uncertainties":
        data["uncertainties"] = [REVIEW_NOTE]
    monkeypatch.setattr(
        summarize_module, "generate_summary_json", lambda **_kwargs: json.dumps(data)
    )
    metadata = _metadata(review_reason if review_reason in {"partial", "unread"} else "all_read")
    conn, cursor = _connection()
    deadline_resolver = MagicMock(side_effect=AssertionError("review must not publish a deadline"))
    stored = summary_job.summarize_and_save_prepared_notice(
        conn, prepared, metadata, deadline_resolver=deadline_resolver, api_key="test-key"
    )
    assert stored.status == "needs_review"
    assert stored.deadline_on is None
    assert cursor.execute.call_args.args[1][2:5] == (None, None, None)
    deadline_resolver.assert_not_called()
    _assert_caller_keeps_transaction(conn)


def test_integrated_shape_retry_retains_media_and_counts_as_one_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared_source("mixed")
    raw = _response("mixed")
    invalid = raw | {"summary": "가" * 41}
    responses = iter((json.dumps(invalid), json.dumps(raw)))
    calls: list[dict[str, Any]] = []

    def generate(**kwargs: Any) -> str:
        calls.append(deepcopy(kwargs))
        return next(responses)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    conn, cursor = _connection()
    stored = summary_job.summarize_and_save_prepared_notice(
        conn, prepared, _metadata(), deadline_resolver=lambda _summary: None, api_key="test-key"
    )
    assert stored.status == "needs_review"
    assert cursor.execute.call_args.args[1][2:5] == (None, None, None)
    assert len(calls) == 2
    assert calls[1]["notice_text"][: len(calls[0]["notice_text"])] == calls[0]["notice_text"]
    assert cursor.execute.call_args.args[1][9] == 1
    cursor.execute.assert_called_once()
    _assert_caller_keeps_transaction(conn)


@pytest.mark.parametrize(
    ("failure_kind", "expected_code", "api_attempts"),
    [
        ("preparation", "input_preparation_failed", 0),
        ("invalid_input", "invalid_media_data", 0),
        ("configuration", "configuration_error", 0),
        ("api_timeout", "api_timeout", 1),
        ("input_too_large", "input_too_large", 1),
        ("empty_response", "empty_response", 1),
        ("unknown_provider_code", "summary_processing_failed", 1),
        ("json", "response_validation_failed", 2),
    ],
)
def test_integrated_known_failure_returns_saved_failure_without_raising_or_success_json(
    monkeypatch: pytest.MonkeyPatch, failure_kind: str, expected_code: str, api_attempts: int
) -> None:
    prepared = _prepared_source("pdf")
    warning = PreparationIssue("hwp", 5, "table_layout_not_preserved")
    prepared.warnings = (warning,)
    requests: list[dict[str, Any]] = []

    def generate(**kwargs: Any) -> str:
        requests.append(deepcopy(kwargs))
        if failure_kind == "json":
            return "invalid JSON " + PRIVATE_MARKER
        reason_code = PRIVATE_MARKER if failure_kind == "unknown_provider_code" else failure_kind
        raise GeminiRequestError(PRIVATE_MARKER, reason_code=reason_code)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    if failure_kind == "preparation":
        prepared.failures = (PreparationIssue("file", 4, "timeout"),)
    elif failure_kind == "invalid_input":
        prepared.blocks[1]["data"] = "%invalid-base64%"
    elif failure_kind == "configuration":

        def invalid_configuration() -> str:
            raise GeminiConfigurationError(PRIVATE_MARKER)

        monkeypatch.setattr(summarize_module, "load_summary_prompt", invalid_configuration)
    conn, cursor = _connection()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    deadline_resolver = MagicMock()
    with conn as active_connection:
        stored = summary_job.summarize_and_save_prepared_notice(
            active_connection,
            prepared,
            _metadata(),
            deadline_resolver=deadline_resolver,
            api_key="test-key",
        )
    assert isinstance(stored, summary_job.StoredSummaryFailure)
    assert stored.status == "failed"
    assert stored.result is None
    assert stored.reason_code == expected_code
    assert stored.notice_id == 17
    assert stored.warnings == (warning,)
    assert len(requests) == api_attempts
    values = cursor.execute.call_args.args[1]
    assert values[1] == "failed"
    assert values[2:5] == (None, None, None)
    assert values[9] == 1
    assert values[10] == expected_code
    assert values[11] is None
    assert PRIVATE_MARKER not in values[10]
    # Returning a failure outcome leaves the caller's connection context without an exception.
    conn.__exit__.assert_called_once_with(None, None, None)
    deadline_resolver.assert_not_called()
    cursor.execute.assert_called_once()
    _assert_caller_keeps_transaction(conn)


@pytest.mark.parametrize("outcome", ["success", "known_failure"])
def test_integrated_storage_error_propagates_for_success_and_failure_writes(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    def generate(**_kwargs: Any) -> str:
        if outcome == "known_failure":
            raise GeminiRequestError(PRIVATE_MARKER, reason_code="api_error")
        return json.dumps(_response("text"))

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    conn, cursor = _connection()
    cursor.execute.side_effect = psycopg.OperationalError(PRIVATE_MARKER)
    with pytest.raises(SummaryStorageError):
        summary_job.summarize_and_save_prepared_notice(
            conn,
            _prepared_source("text"),
            _metadata(),
            deadline_resolver=lambda _summary: None,
            api_key="test-key",
        )
    cursor.execute.assert_called_once()
    _assert_caller_keeps_transaction(conn)


@pytest.mark.parametrize("stage", ["generate", "deadline"])
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_integrated_programming_errors_propagate_without_being_recorded_as_model_failures(
    monkeypatch: pytest.MonkeyPatch, stage: str, error_type: type[Exception]
) -> None:
    error = error_type("a programming failure")

    def generate(**_kwargs: Any) -> str:
        if stage == "generate":
            raise error
        return json.dumps(_response("text"))

    def deadline(_summary: Any) -> date | None:
        raise error

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    conn, _ = _connection()
    with pytest.raises(error_type) as raised:
        summary_job.summarize_and_save_prepared_notice(
            conn,
            _prepared_source("text"),
            _metadata(),
            deadline_resolver=deadline,
            api_key="test-key",
        )
    assert raised.value is error
    conn.cursor.assert_not_called()
    _assert_caller_keeps_transaction(conn)


@pytest.mark.parametrize("invalid_field", ["notice_id", "metadata", "deadline_resolver"])
def test_integrated_invalid_caller_contract_stops_before_api_or_storage(
    monkeypatch: pytest.MonkeyPatch, invalid_field: str
) -> None:
    prepared = _prepared_source("text")
    metadata: Any = _metadata()
    deadline_resolver: Any = MagicMock(return_value=None)
    if invalid_field == "notice_id":
        prepared.notice_id = True
    elif invalid_field == "metadata":
        metadata = None
    else:
        deadline_resolver = None
    generate = MagicMock(side_effect=AssertionError("invalid caller must not invoke the API"))
    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    conn, _ = _connection()
    with pytest.raises(SummaryRecordError):
        summary_job.summarize_and_save_prepared_notice(
            conn, prepared, metadata, deadline_resolver=deadline_resolver, api_key="test-key"
        )
    generate.assert_not_called()
    assert prepared.calls == []
    conn.cursor.assert_not_called()


def test_integrated_notice_id_mismatch_stops_before_deadline_and_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, result, _ = _summarize(monkeypatch, "text")
    monkeypatch.setattr(
        summary_job,
        "summarize_prepared_notice",
        lambda *_args, **_kwargs: replace(result, notice_id=99),
    )
    conn, _ = _connection()
    deadline_resolver = MagicMock()
    with pytest.raises(SummaryRecordError, match="summary_notice_id_mismatch"):
        summary_job.summarize_and_save_prepared_notice(
            conn,
            _prepared_source("text"),
            _metadata(),
            deadline_resolver=deadline_resolver,
            api_key="test-key",
        )
    conn.cursor.assert_not_called()
    deadline_resolver.assert_not_called()


@pytest.mark.parametrize("failed", [False, True])
def test_integrated_warning_data_is_preserved_without_automatic_emission(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    recwarn: pytest.WarningsRecorder,
    failed: bool,
) -> None:
    prepared = _prepared_source("hwp")
    prepared.warnings = (PreparationIssue("hwp", 2, PRIVATE_MARKER),)

    def generate(**_kwargs: Any) -> str:
        if failed:
            raise GeminiRequestError("safe API failure", reason_code="api_error")
        return json.dumps(_response("hwp"))

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    conn, _ = _connection()
    stored = summary_job.summarize_and_save_prepared_notice(
        conn, prepared, _metadata(), deadline_resolver=lambda _summary: None, api_key="test-key"
    )
    warnings = stored.warnings if failed else stored.result.warnings
    assert warnings == prepared.warnings
    assert stored.status == ("failed" if failed else "summarized")
    captured = capsys.readouterr()
    assert caplog.records == []
    assert captured.out == captured.err == ""
    assert not recwarn.list


@pytest.mark.parametrize(
    ("field", "reason_code"),
    [
        ("warnings", "invalid_preparation_warnings"),
        ("media_sources", "invalid_media_sources"),
    ],
)
@pytest.mark.parametrize("invalid_value", [None, [PRIVATE_MARKER]], ids=["none", "list"])
def test_invalid_result_side_data_is_rejected_before_any_sql_write(
    field: str, reason_code: str, invalid_value: Any
) -> None:
    result = PreparedSummaryResult(
        notice_id=17,
        summary=NoticeSummary.model_validate(_response("text")),
        warnings=(),
        media_sources=(),
    )
    result = replace(result, **{field: invalid_value})
    conn, cursor = _connection()
    with pytest.raises(SummaryRecordError) as failure:
        save_prepared_summary(
            conn, result, _metadata(), deadline_on=DEADLINE, generated_at=GENERATED_AT
        )
    assert failure.value.reason_code == reason_code
    assert str(failure.value) == reason_code
    assert PRIVATE_MARKER not in str(failure.value)
    conn.cursor.assert_not_called()
    cursor.execute.assert_not_called()
    _assert_caller_keeps_transaction(conn)


@pytest.mark.parametrize("preparation_failed", [False, True])
def test_invalid_preparation_warnings_stop_integrated_job_before_api_or_sql(
    monkeypatch: pytest.MonkeyPatch, preparation_failed: bool
) -> None:
    prepared = _prepared_source("pdf")
    prepared.warnings = None
    if preparation_failed:
        prepared.failures = (PreparationIssue("file", 2, "timeout"),)
    generate = MagicMock(side_effect=AssertionError("invalid warnings must stop before the API"))
    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    conn, cursor = _connection()
    deadline_resolver = MagicMock()
    with pytest.raises(SummaryRecordError) as failure:
        summary_job.summarize_and_save_prepared_notice(
            conn, prepared, _metadata(), deadline_resolver=deadline_resolver, api_key="test-key"
        )
    assert failure.value.reason_code == "invalid_preparation_warnings"
    assert str(failure.value) == "invalid_preparation_warnings"
    assert prepared.calls == []
    generate.assert_not_called()
    deadline_resolver.assert_not_called()
    conn.cursor.assert_not_called()
    cursor.execute.assert_not_called()
    _assert_caller_keeps_transaction(conn)
