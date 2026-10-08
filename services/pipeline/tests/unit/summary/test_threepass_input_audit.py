"""Three distinct adversarial sweeps of the input and generation boundaries."""

import json
import logging
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import StringIO
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from google.genai import types
from support.paths import FIXTURES_DIR

from pipeline.storage.summary_record import summary_requires_review
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.transform import gemini_client, gemini_input
from pipeline.transform import summarize as summarize_module
from pipeline.transform.gemini_logging import private_gemini_logging
from pipeline.transform.grounding import REVIEW_NOTE, unknown_summary
from pipeline.transform.notice_input import NoticeInput, render_notice_input
from pipeline.transform.prepared_summary import SummaryPreparationError, prepare_gemini_request
from pipeline.transform.summary_schema import (
    GeminiNoticeSummary,
    NoticeSummary,
    SummaryValidationError,
)


def _notice(audience="월계1동 주민"):
    return NoticeInput.model_validate({
        "title": "지원 사업 신청",
        "body_text": (
            f"지원 사업 신청\n대상: {audience}\n"
            "신청방법: 월계1동 주민센터 방문 신청\n장소: 월계1동 주민센터"
        ),
        "reference_datetime": "2026-10-07T12:00:00+09:00",
    })


def _response(notice):
    audience = notice.body_text.split("대상: ", 1)[1].split("\n", 1)[0]
    data = unknown_summary(notice).model_dump(mode="json")
    data.update(
        category="application", category_code=27, summary="지원 사업 신청",
        audience=audience, audience_scope="conditional",
        action="월계1동 주민센터 방문 신청", action_requirement="optional",
        location="월계1동 주민센터", status="unknown", notice_update="new",
        uncertainties=[],
        evidence=[
            {"field": field, "excerpt": quote}
            for field, quote in (
                ("summary", "지원 사업 신청"), ("category", "지원 사업 신청"),
                ("category_code", "지원 사업 신청"),
                ("audience", f"대상: {audience}"),
                ("action", "신청방법: 월계1동 주민센터 방문 신청"),
                ("action_requirement", "신청방법: 월계1동 주민센터 방문 신청"),
                ("location", "장소: 월계1동 주민센터"),
            )
        ],
        card_summaries={
            "audience": f"{audience}이 대상이에요.", "deadline": None,
            "action": "희망하시면 월계1동 주민센터에 방문하여 신청해 주세요.",
            "notes": None,
        },
    )
    return data


def _prepared(notice, *, media=True):
    blocks = [{"type": "text", "text": render_notice_input(notice)}]
    if media:
        blocks.extend([
            {"type": "document", "mime_type": "application/pdf",
             "data": b64encode(b"%PDF-audit-original").decode()},
            {"type": "image", "mime_type": "image/png",
             "data": b64encode(b"audit-original-image").decode()},
        ])
    return SimpleNamespace(
        notice_id=17, notice=notice, failures=(), warnings=(), blocks=blocks,
        to_gemini_input=lambda: deepcopy(blocks),
    )


def _mock_outputs(monkeypatch, outputs):
    requests = []
    pending = iter(outputs)

    def generate(**kwargs):
        requests.append(deepcopy(kwargs))
        item = next(pending)
        return item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    return requests


# Sweep 1: executable contracts and ownership, before provider execution.
def test_contract_preparation_failure_prevents_key_prompt_and_sdk_work(monkeypatch):
    prepared = _prepared(_notice())
    prepared.failures = (SimpleNamespace(reason_code="download_failed"),)
    prepared.to_gemini_input = lambda: pytest.fail("preparer must not run after a failure")
    monkeypatch.setattr(summarize_module, "load_gemini_api_key", lambda: pytest.fail("key read"))
    monkeypatch.setattr(summarize_module, "load_summary_prompt", lambda: pytest.fail("prompt read"))
    calls = _mock_outputs(monkeypatch, [])
    with pytest.raises(SummaryPreparationError, match="input_preparation_failed"):
        summarize_module.summarize_prepared_notice(prepared)
    assert calls == []


def test_contract_request_manifest_owns_order_and_does_not_alias_preparer_blocks():
    prepared = _prepared(_notice())
    before = deepcopy(prepared.blocks)
    blocks, media = prepare_gemini_request(prepared)
    assert [(item.source_id, item.source_type) for item in media] == [
        ("media_1", "document"), ("media_2", "image"),
    ]
    assert blocks[:-1] == before
    blocks[0]["text"] = "changed only in caller copy"
    blocks[1]["data"] = "changed only in caller copy"
    assert prepared.blocks == before


def test_contract_total_wire_size_rejects_small_input_with_oversized_prompt_before_sdk(monkeypatch):
    monkeypatch.setattr(gemini_input, "MAX_GEMINI_INPUT_BYTES", 100)
    monkeypatch.setattr(gemini_client.genai, "Client", lambda **kwargs: pytest.fail("SDK created"))
    with pytest.raises(gemini_client.GeminiRequestError, match="input_too_large"):
        gemini_client._generate_summary_json_direct(
            prompt="instructions" * 20, notice_text="source", api_key="audit-key"
        )


@pytest.mark.parametrize("problem", [
    "missing", "null", "extra", "multiline", "dache", "missing-audience", "missing-action",
])
def test_contract_fresh_cards_rejected_then_fixed_without_source_replacement(monkeypatch, problem):
    notice = _notice()
    first = _response(notice)
    if problem == "missing":
        first.pop("card_summaries")
    elif problem == "null":
        first["card_summaries"] = None
    elif problem == "extra":
        first["card_summaries"]["DO_NOT_LOG_PRIVATE_MARKER"] = "private extra"
    elif problem == "multiline":
        first["card_summaries"]["action"] = "신청해 주세요.\n방문해 주세요."
    elif problem == "dache":
        first["card_summaries"]["action"] = "방문해야 합니다."
    else:
        first["card_summaries"][problem.removeprefix("missing-")] = None
    retry = _response(notice)
    retry["audience"] = "다른 지역 주민"
    prepared = _prepared(notice)
    before = deepcopy(prepared.blocks)
    calls = _mock_outputs(monkeypatch, [first, retry])
    summary = summarize_module.summarize_prepared_notice(prepared, api_key="audit-key").summary
    assert len(calls) == 2
    assert calls[1]["notice_text"][:-1] == calls[0]["notice_text"]
    assert prepared.blocks == before
    assert summary.audience == first["audience"]
    assert summary.card_summaries.action == retry["card_summaries"]["action"]
    assert "DO_NOT_LOG_PRIVATE_MARKER:" not in calls[1]["notice_text"][-1]["text"]


@pytest.mark.parametrize("slot", ["audience", "deadline", "action", "notes"])
def test_contract_known_source_card_cannot_be_null_in_fresh_but_legacy_keeps_it(slot):
    data = _response(_notice())
    if slot == "deadline":
        data["dates"] = [{"kind": "application", "label": "신청 기한", "text": None,
                          "start_date": None, "end_date": "2026-10-20",
                          "start_time": None, "end_time": None}]
    elif slot == "notes":
        data["notes"] = ["신분증 지참"]
    data["card_summaries"][slot] = None
    assert getattr(NoticeSummary.model_validate(data).card_summaries, slot) is None
    with pytest.raises(ValueError) as failure:
        GeminiNoticeSummary.model_validate(data)
    assert any(error["loc"] == ("card_summaries", slot) for error in failure.value.errors())


def test_contract_explicit_no_action_needs_prose_and_unknown_slots_remain_null():
    data = unknown_summary(_notice()).model_dump(mode="json")
    assert GeminiNoticeSummary.model_validate(data).card_summaries.model_dump() == {
        "audience": None, "deadline": None, "action": None, "notes": None,
    }
    data["action_requirement"] = "none"
    # A classification-default none without a quoted statement is not a
    # known resident action and must not force a new no-action card sentence.
    assert GeminiNoticeSummary.model_validate(data).card_summaries.action is None
    data["evidence"].append({
        "field": "action_requirement", "excerpt": "별도의 신청은 필요 없습니다.",
    })
    with pytest.raises(ValueError) as failure:
        GeminiNoticeSummary.model_validate(data)
    assert any(
        error["loc"] == ("card_summaries", "action") for error in failure.value.errors()
    )
    data["card_summaries"]["action"] = "별도로 해야 할 일은 없어요."
    assert GeminiNoticeSummary.model_validate(data).card_summaries.action == data[
        "card_summaries"
    ]["action"]


@pytest.mark.parametrize("kind", ["text", "document", "image"])
def test_contract_bad_input_prevents_sdk_creation(monkeypatch, kind):
    monkeypatch.setattr(gemini_client.genai, "Client", lambda **kwargs: pytest.fail("SDK created"))
    blocks = (
        [{"type": "text", "text": " "}] if kind == "text"
        else [{"type": kind, "mime_type": "application/pdf" if kind == "document"
               else "image/png", "data": "not-base64"}]
    )
    with pytest.raises(gemini_client.GeminiRequestError):
        gemini_client._generate_summary_json_direct(prompt="instructions", notice_text=blocks,
                                            api_key="audit-key")


# Sweep 2: mutation, malformed final response and runtime/logger counterexamples.
@pytest.mark.parametrize("entrypoint", ["text", "prepared"])
def test_runtime_caller_mutation_does_not_change_sent_source_validation(monkeypatch, entrypoint):
    original = _notice()
    forged = _notice("노원구 만 65세 이상 주민")
    sent = []

    def generate(**kwargs):
        sent.append(deepcopy(kwargs["notice_text"]))
        original.body_text = forged.body_text
        original.reference_datetime = forged.reference_datetime.replace(year=2028)
        return json.dumps(_response(forged), ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    result = (
        summarize_module.summarize_notice(original, api_key="audit-key")
        if entrypoint == "text"
        else summarize_module.summarize_prepared_notice(
            _prepared(original), api_key="audit-key"
        ).summary
    )
    assert len(sent) == 1
    assert "65세" not in str(sent)
    assert REVIEW_NOTE in result.uncertainties
    assert summary_requires_review(result, attachment_status="all_read")
    assert any(item.field == "audience" and item.verification is None for item in result.evidence)


def test_runtime_preparer_callback_cannot_replace_notice_identity_or_source_snapshot(monkeypatch):
    original = _notice()
    prepared = _prepared(original, media=False)
    before = deepcopy(prepared.blocks)
    first_warning = SimpleNamespace(stage="extract", item_id=1, reason_code="layout_uncertain")
    prepared.warnings = (first_warning,)

    def prepare():
        prepared.notice_id = 99
        prepared.notice = _notice("다른 지역 주민")
        prepared.warnings = ()
        return deepcopy(before)

    prepared.to_gemini_input = prepare
    calls = _mock_outputs(monkeypatch, [_response(original)])
    result = summarize_module.summarize_prepared_notice(prepared, api_key="audit-key")
    assert len(calls) == 1
    assert result.notice_id == 17
    assert result.warnings == (first_warning,)
    assert result.summary.audience == "월계1동 주민"
    assert result.summary.uncertainties == []


@pytest.mark.parametrize("entrypoint", ["text", "prepared"])
def test_runtime_reference_clock_and_nested_attachment_text_are_request_snapshots(
    monkeypatch, entrypoint
):
    original = NoticeInput.model_validate(_notice().model_dump() | {
        "attachments": [{"name": "안내.hwp", "text": "참가비 20,000원"}],
    })
    original.body_text += "\n신청 기간: 2026-10-03~2026-10-20"
    response = _response(original)
    response.update(
        status="open", notes=["참가비 20,000원"],
        dates=[{"kind": "application", "label": "신청 기간", "text": None,
                "start_date": "2026-10-03", "end_date": "2026-10-20",
                "start_time": None, "end_time": None}],
    )
    response["evidence"].extend([
        {"field": "notes", "excerpt": "참가비 20,000원"},
        {"field": "dates", "excerpt": "신청 기간: 2026-10-03~2026-10-20"},
    ])
    response["card_summaries"].update(
        deadline="신청 기간은 2026-10-03부터 2026-10-20까지예요.",
        notes="참가비는 20,000원이에요.",
    )
    requests = []

    def generate(**kwargs):
        requests.append(kwargs)
        original.reference_datetime = original.reference_datetime.replace(year=2028)
        original.attachments[0].text = "참가비 90,000원"
        return json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(summarize_module, "generate_summary_json", generate)
    result = (
        summarize_module.summarize_notice(original, api_key="audit-key")
        if entrypoint == "text"
        else summarize_module.summarize_prepared_notice(
            _prepared(original), api_key="audit-key"
        ).summary
    )
    assert len(requests) == 1
    assert result.status == "open"
    assert result.notes == ["참가비 20,000원"]
    assert result.uncertainties == []
    assert all(item.verification == "text_matched" for item in result.evidence)


def test_runtime_nested_attachment_mutation_does_not_gain_text_matched_evidence(monkeypatch):
    original = _notice()
    original.attachments = [{"name": "MOCK_PRIVATE_MUTATED_ATTACHMENT", "text": None}]
    calls = _mock_outputs(monkeypatch, [])
    with pytest.raises(gemini_input.GeminiInputError, match="invalid_input") as failure:
        summarize_module.summarize_notice(original, api_key="audit-key")
    assert calls == []
    assert "MOCK_PRIVATE" not in str(failure.value)


def test_runtime_same_note_facts_in_retry_order_do_not_falsely_require_review():
    notice = _notice()
    notice.body_text += "\n신분증 지참\n예약 필수"
    first_data = _response(notice)
    first_data["notes"] = ["신분증 지참", "예약 필수"]
    first_data["evidence"].extend([
        {"field": "notes", "excerpt": "신분증 지참"},
        {"field": "notes", "excerpt": "예약 필수"},
    ])
    first_data["card_summaries"]["notes"] = "신분증을 지참하고 미리 예약해 주세요."
    retry_data = deepcopy(first_data)
    retry_data["notes"].reverse()
    retry_data["card_summaries"]["notes"] = "미리 예약하고 신분증을 지참해 주세요."
    first = summarize_module._validate_summary(
        json.dumps(first_data, ensure_ascii=False), notice, require_card_summaries=True,
    )
    retry = summarize_module._validate_summary(
        json.dumps(retry_data, ensure_ascii=False), notice, require_card_summaries=True,
    )
    assert first.uncertainties == retry.uncertainties == []
    before = first.model_dump(mode="json")
    merged = summarize_module._require_generated_cards(
        summarize_module._merge_note_correction(first, retry, notice), notice=notice,
    )
    assert merged.notes == first_data["notes"]
    assert merged.card_summaries.notes == retry_data["card_summaries"]["notes"]
    assert merged.uncertainties == []
    assert first.model_dump(mode="json") == before
    view = build_notice_summary_view(status="summarized", result=merged, attachment_status="none")
    assert view.status == "summarized"
    assert view.message is None


@pytest.mark.parametrize("final", ["broken-json", "missing-field", "invalid-card"])
def test_runtime_unusable_final_does_not_trigger_third_request_or_become_success(
    monkeypatch, final
):
    notice = _notice()
    first = _response(notice)
    first["card_summaries"]["action"] = "다로 끝납니다."
    final_data = _response(notice)
    if final == "broken-json":
        final_data = "{private invalid JSON"
    elif final == "missing-field":
        final_data.pop("audience")
    else:
        final_data["card_summaries"]["action"] = "반복해서 다로 끝납니다."
    calls = _mock_outputs(monkeypatch, [first, final_data])
    with pytest.raises(SummaryValidationError) as failure:
        summarize_module.summarize_prepared_notice(_prepared(notice), api_key="audit-key")
    assert len(calls) == 2
    assert "private" not in str(failure.value)


def test_runtime_sdk_diagnostics_from_a_late_child_logger_are_private(monkeypatch, caplog):
    marker = "MOCK_PRIVATE_SDK_DIAGNOSTIC_MARKER"
    logger_name = f"google.genai.future.{uuid4().hex}"

    class Client:
        def __init__(self, **kwargs):
            self.interactions = SimpleNamespace(
                create=self.create, sdk_configuration=SimpleNamespace(retry_config=None)
            )

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def create(self, **kwargs):
            logging.getLogger(logger_name).warning("%s", marker)
            logging.getLogger("pipeline.audit.application").warning("normal app diagnostic")
            return SimpleNamespace(status="completed", errors=None, output_text='{"ok":true}')

    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(gemini_client.genai, "Client", Client)
    assert gemini_client._generate_summary_json_direct(
        prompt="instructions", notice_text="notice", api_key="audit-key"
    ) == '{"ok":true}'
    assert marker not in caplog.text
    assert "normal app diagnostic" in caplog.text
    logging.getLogger(logger_name).warning("post-context logging restored")
    assert "post-context logging restored" in caplog.text


def test_runtime_new_nonpropagating_sdk_handler_is_suppressed_and_restored():
    stream = StringIO()
    original_dispatch = logging.Logger.callHandlers
    name = f"google.genai.late.own_handler.{uuid4().hex}"
    logger = None
    with private_gemini_logging():
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.addHandler(logging.StreamHandler(stream))
        logger.warning("MOCK_PRIVATE_OWN_HANDLER")
    assert stream.getvalue() == ""
    assert logging.Logger.callHandlers is original_dispatch
    logger.warning("restored own handler")
    assert "restored own handler" in stream.getvalue()


def test_runtime_nested_private_logging_keeps_outer_scope_and_restores_on_exception(caplog):
    original_dispatch = logging.Logger.callHandlers
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger(f"httpx.late.nested.{uuid4().hex}")
    with private_gemini_logging():
        with pytest.raises(RuntimeError, match="mock failure"):
            with private_gemini_logging():
                logger.warning("MOCK_PRIVATE_INNER")
                raise RuntimeError("mock failure")
        logger.warning("MOCK_PRIVATE_OUTER")
    assert "MOCK_PRIVATE" not in caplog.text
    assert logging.Logger.callHandlers is original_dispatch
    logger.warning("post-exception restored")
    assert "post-exception restored" in caplog.text


def test_runtime_private_logging_preserves_an_unrelated_thread_and_app_logger(caplog):
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger(f"httpcore.late.other_thread.{uuid4().hex}")
    with ThreadPoolExecutor(max_workers=1) as executor:
        with private_gemini_logging():
            logger.warning("MOCK_PRIVATE_CURRENT_THREAD")
            executor.submit(logger.warning, "other thread retained").result(timeout=5)
            logging.getLogger("pipeline.application").warning("same thread app retained")
    assert "MOCK_PRIVATE_CURRENT_THREAD" not in caplog.text
    assert "other thread retained" in caplog.text
    assert "same thread app retained" in caplog.text


def test_runtime_overlapping_request_scopes_remain_private_until_the_last_exit(caplog):
    original_dispatch = logging.Logger.callHandlers
    caplog.set_level(logging.DEBUG)
    entered = Event()
    release = Event()
    logger = logging.getLogger(f"google_genai.parallel.{uuid4().hex}")

    def second_request():
        with private_gemini_logging():
            entered.set()
            assert release.wait(timeout=5)
            logger.warning("MOCK_PRIVATE_SECOND_REQUEST")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with private_gemini_logging():
            worker = executor.submit(second_request)
            assert entered.wait(timeout=5)
            logger.warning("MOCK_PRIVATE_FIRST_REQUEST")
        assert logging.Logger.callHandlers is not original_dispatch
        release.set()
        worker.result(timeout=5)
    assert logging.Logger.callHandlers is original_dispatch
    assert "MOCK_PRIVATE" not in caplog.text


def test_runtime_locked_sdk_direct_call_disables_hidden_http_retries(monkeypatch):
    real_client = gemini_client.genai.Client
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(503, json={"error": {"code": 503, "message": "MOCK_PRIVATE"}})

    def factory(*, api_key, http_options):
        options = http_options.model_copy(update={
            "base_url": "https://gemini.invalid",
            "client_args": {**(http_options.client_args or {}),
                            "transport": httpx.MockTransport(handler)},
        })
        return real_client(api_key=api_key, http_options=options)

    monkeypatch.setattr(gemini_client.genai, "Client", factory)
    monkeypatch.setattr(
        gemini_client, "run_gemini_request",
        lambda _operation, payload: gemini_client._generate_summary_json_direct(**payload),
    )
    with pytest.raises(gemini_client.GeminiRequestError):
        gemini_client._generate_summary_json_direct(prompt="instructions", notice_text="notice",
                                            api_key="audit-key")
    # The compatibility adapter disables the otherwise hidden Interactions retry.
    assert len(requests) == 1


# Sweep 3: captured real source replay through the public entrypoint and real SDK.
CAPTURE_PATH = FIXTURES_DIR / "card_text_highlights.json"


@pytest.mark.parametrize("case_name", [
    "moss_exhibition", "library_committee", "online_english", "library_committee_pdf_only",
])
def test_replay_captured_source_and_generated_cards_preserve_public_warning_and_evidence(
    monkeypatch, case_name
):
    case = json.loads(CAPTURE_PATH.read_text(encoding="utf-8"))["cases"][case_name]
    notice = NoticeInput.model_validate(case["notice_input"])
    raw = case["summary"]
    calls = _mock_outputs(monkeypatch, [raw, raw])
    prepared = _prepared(notice, media=False)
    if case_name.startswith("library_committee"):
        prepared.blocks.append({"type": "document", "mime_type": "application/pdf",
                                "data": b64encode(b"%PDF-capture-reference").decode()})
    result = summarize_module.summarize_prepared_notice(prepared, api_key="audit-key")
    view = build_notice_summary_view(
        status="summarized", result=result.summary,
        attachment_status="all_read" if result.media_sources else "none", notice=notice,
    )
    assert 1 <= len(calls) <= 2
    assert view.content is not None
    assert result.summary.card_summaries.model_dump() == raw["card_summaries"]
    assert result.summary.audience == raw["audience"]
    assert result.summary.notes == raw["notes"]
    assert view.content.headline.value == raw["summary"]
    assert result.summary.uncertainties or view.status == "summarized"
    if result.summary.uncertainties:
        assert view.status == "needs_review"
        assert view.message == "원문 확인 요함"


def test_replay_real_sdk_serialization_retains_media_bytes_and_strict_fresh_schema(monkeypatch):
    real_client = gemini_client.genai.Client
    requests = []
    blocks = _prepared(_notice()).blocks
    before = deepcopy(blocks)

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "status": "completed", "steps": [{"type": "model_output",
            "content": [{"type": "text", "text": '{"summary":"captured mock"}'}]}],
        })

    def factory(*, api_key, http_options):
        options = http_options.model_copy(update={
            "base_url": "https://gemini.invalid",
            "client_args": {**(http_options.client_args or {}),
                            "transport": httpx.MockTransport(handler)},
            "retry_options": types.HttpRetryOptions(attempts=1),
        })
        return real_client(api_key=api_key, http_options=options)

    monkeypatch.setattr(gemini_client.genai, "Client", factory)
    monkeypatch.setattr(
        gemini_client, "run_gemini_request",
        lambda _operation, payload: gemini_client._generate_summary_json_direct(**payload),
    )
    assert gemini_client._generate_summary_json_direct(
        prompt="instructions", notice_text=blocks, api_key="audit-key"
    ) == '{"summary":"captured mock"}'
    assert len(requests) == 1
    wire = requests[0]
    assert wire["input"] == [{"type": "user_input", "content": before}]
    assert wire["store"] is False
    schema = wire["response_format"]["schema"]
    assert "card_summaries" in schema["required"]
    assert set(schema["$defs"]["GeminiCardSummaries"]["required"]) == {
        "audience", "deadline", "action", "notes",
    }
    assert blocks == before


def test_replay_public_prepared_entrypoint_uses_actual_sdk_transport_and_validation(monkeypatch):
    real_client = gemini_client.genai.Client
    notice = _notice()
    raw = json.dumps(_response(notice), ensure_ascii=False)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "status": "completed", "steps": [{"type": "model_output",
            "content": [{"type": "text", "text": raw}]}],
        })

    def factory(*, api_key, http_options):
        options = http_options.model_copy(update={
            "base_url": "https://gemini.invalid",
            "client_args": {**(http_options.client_args or {}),
                            "transport": httpx.MockTransport(handler)},
        })
        return real_client(api_key=api_key, http_options=options)

    monkeypatch.setattr(gemini_client.genai, "Client", factory)
    monkeypatch.setattr(
        gemini_client, "run_gemini_request",
        lambda _operation, payload: gemini_client._generate_summary_json_direct(**payload),
    )
    result = summarize_module.summarize_prepared_notice(_prepared(notice), api_key="audit-key")
    view = build_notice_summary_view(
        status="summarized", result=result.summary, attachment_status="all_read", notice=notice,
    )
    assert len(requests) == 1
    assert result.summary.audience == "월계1동 주민"
    assert result.summary.uncertainties == []
    assert view.status == "summarized"
    assert view.content.cards.audience.text == "월계1동 주민이 대상이에요."
    assert view.text_highlights.cards.audience.status == "ready"


def test_replay_one_correction_sends_two_posts_with_identical_original_media(monkeypatch):
    real_client = gemini_client.genai.Client
    notice = _notice()
    first = _response(notice)
    first["card_summaries"]["action"] = "방문해야 합니다."
    retry = _response(notice)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        raw = json.dumps(first if len(requests) == 1 else retry, ensure_ascii=False)
        return httpx.Response(200, json={
            "status": "completed", "steps": [{"type": "model_output",
            "content": [{"type": "text", "text": raw}]}],
        })

    def factory(*, api_key, http_options):
        assert http_options.retry_options.attempts == 1
        options = http_options.model_copy(update={
            "base_url": "https://gemini.invalid",
            "client_args": {**(http_options.client_args or {}),
                            "transport": httpx.MockTransport(handler)},
        })
        return real_client(api_key=api_key, http_options=options)

    monkeypatch.setattr(gemini_client.genai, "Client", factory)
    monkeypatch.setattr(
        gemini_client, "run_gemini_request",
        lambda _operation, payload: gemini_client._generate_summary_json_direct(**payload),
    )
    prepared = _prepared(notice)
    before = deepcopy(prepared.blocks)
    result = summarize_module.summarize_prepared_notice(prepared, api_key="audit-key")
    assert len(requests) == 2
    original_content = requests[0]["input"][0]["content"]
    retry_content = requests[1]["input"][0]["content"]
    assert retry_content[:-1] == original_content
    assert prepared.blocks == before
    assert result.summary.audience == first["audience"]
    assert result.summary.card_summaries.action == retry["card_summaries"]["action"]
