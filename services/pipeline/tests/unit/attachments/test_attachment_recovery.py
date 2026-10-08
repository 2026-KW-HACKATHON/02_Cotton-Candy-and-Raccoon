"""Resource limits and trusted partial coverage, including real DB round trips."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import Mock

import httpx
import pytest
from support.prepared_summary_storage import _response
from support.summary_bundle import NOW, PNG, URL, file, source
from support.summary_bundle_manifest import _metadata
from support.summary_execution_storage import _row
from support.threepass_database_audit import _notice
from support.threepass_database_audit import live_db as live_db

from pipeline.attachments import download
from pipeline.attachments.inline_images import prepare_notice_body
from pipeline.attachments.summary_bundle import prepare_summary_source
from pipeline.storage.summary_source import load_summary_source
from pipeline.storage.summary_view import build_notice_summary_view
from pipeline.summary_job import StoredSummaryFailure, summarize_and_save_prepared_notice
from pipeline.transform.prepared_summary import SummaryPreparationError, prepare_gemini_request
from pipeline.transform.summary_files import PreparationOmission


@pytest.mark.parametrize("bad", ["https://[broken/a.png", "//[broken", "http://[::x]/a.png"])
def test_malformed_image_is_reported_and_next_image_survives(bad):
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=PNG)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = prepare_notice_body(
            f'<img src="{bad}"><img src="{URL}1">', source().url, client=client,
        )
    assert len(calls) == len(result.images) == 1
    assert [item.reason_code for item in result.failures] == ["invalid_url"]


def test_different_urls_share_one_cached_byte_object():
    cache = {}
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=PNG + b"x" * (4096 - len(PNG))),
    )) as client:
        result = prepare_notice_body(
            "".join(f'<img src="{URL}{i}">' for i in range(12)), source().url,
            client=client, image_cache=cache, max_total_bytes=8192,
        )
    assert result.complete and len(cache) == 12
    assert len({id(item.data) for item in cache.values()}) == 1
    assert all(item is result.images[0] for item in cache.values())


def test_image_aggregate_exceeds_ten_mib_without_exceeding_final_input_budget():
    size = 2202009
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, content=PNG + bytes([int(request.url.params["q_fileId"])]) * (size - len(PNG)),
        ),
    )) as client:
        prepared = prepare_summary_source(
            source(html="".join(f'<img src="{URL}{i}">' for i in range(5))),
            reference_datetime=NOW, client=client,
        )
    assert prepared.complete and not prepared.file_manifest.omissions
    assert len(prepared.media) == 5
    prepare_gemini_request(prepared)


def test_new_seoul_http_normalization_matches_collector_and_preserves_stored_link():
    url = "http://news.seoul.go.kr/welfare/files/2026/poster.png"
    original = replace(file(1, "poster.png"), url=url)
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=PNG)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        prepared = prepare_summary_source(
            source(original, html=f'<img src="{url}">'), reference_datetime=NOW, client=client,
        )
    assert calls == [url.replace("http:", "https:")]
    assert prepared.file_manifest.files[0].url == url
    assert prepared.complete and not prepared.file_manifest.omissions


def test_slow_stream_hits_total_deadline_and_closes(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(download, "monotonic", lambda: clock[0])

    class SlowStream(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            for chunk in (PNG, b"more", b"never retained"):
                clock[0] += 4.0
                yield chunk

        def close(self):
            self.closed = True

    stream = SlowStream()
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=stream),
    )) as client, pytest.raises(download.AttachmentDownloadError, match="time_limit"):
        download.download_image(URL + "1", client=client, max_seconds=7)
    assert clock[0] == 8 and stream.closed


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_default_download_cancels_network_wait_and_closes_owned_client(monkeypatch, phase):
    clients = []
    cancelled = []

    class SlowBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield PNG
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.append("body")
                raise

        async def aclose(self):
            self.closed = True

    stream = SlowBody()

    async def handler(request):
        if phase == "headers":
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.append("headers")
                raise
        return httpx.Response(200, stream=stream)

    real_client = httpx.AsyncClient

    def factory():
        client = real_client(transport=httpx.MockTransport(handler))
        clients.append(client)
        return client

    monkeypatch.setattr(download.httpx, "AsyncClient", factory)
    with pytest.raises(download.AttachmentDownloadError, match="time_limit"):
        download.download_image(URL + "1", max_seconds=0.1)
    assert cancelled == [phase]
    assert clients and all(client.is_closed for client in clients)
    if phase == "body":
        assert stream.closed


@pytest.mark.parametrize("inside_loop", [False, True])
def test_default_preparer_uses_owned_downloads_even_when_called_inside_event_loop(
    monkeypatch, inside_loop,
):
    clients = []
    real_client = httpx.AsyncClient

    def factory():
        client = real_client(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=PNG),
        ))
        clients.append(client)
        return client

    monkeypatch.setattr(download.httpx, "AsyncClient", factory)

    def prepare():
        return prepare_summary_source(
            source(file(1, "a.png"), html=f'<img src="{URL}1">'), reference_datetime=NOW,
        )

    async def in_loop():
        return prepare()

    prepared = asyncio.run(in_loop()) if inside_loop else prepare()
    assert len(clients) == len(prepared.media) == 1
    assert clients[0].is_closed and not prepared.file_manifest.omissions
    prepare_gemini_request(prepared)


def test_notice_deadline_stops_subsequent_requests_and_keeps_read_body(monkeypatch):
    clock = [0.0]
    calls = []
    monkeypatch.setattr(download, "monotonic", lambda: clock[0])

    def respond(request):
        calls.append(request)
        assert request.extensions["timeout"]["read"] == 2
        clock[0] = 3
        return httpx.Response(200, content=PNG)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        prepared = prepare_summary_source(
            source(file(1, "a.png"), file(2, "b.png")), reference_datetime=NOW,
            client=client, max_preparation_seconds=2,
        )
    assert len(calls) == 1 and prepared.complete
    assert [item.reason_code for item in prepared.file_manifest.omissions] == [
        "time_limit", "time_limit",
    ]


@pytest.mark.parametrize("seconds", [True, 0, -1, float("nan"), float("inf")])
def test_invalid_time_budget_rejected_before_request(seconds):
    with pytest.raises(ValueError):
        prepare_summary_source(source(), reference_datetime=NOW, max_preparation_seconds=seconds)


@pytest.mark.parametrize("suffix", ["hwpx", "xlsx", "docx", "zip", "gif", "svg"])
@pytest.mark.parametrize("body", ["<p>행사 안내</p>", None])
def test_unsupported_files_are_omissions_but_empty_sources_still_block(suffix, body):
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: pytest.fail("unsupported file must not download"),
    )) as client:
        prepared = prepare_summary_source(
            source(file(1, "신청서." + suffix), html=body), reference_datetime=NOW, client=client,
        )
    assert prepared.file_manifest.attachment_status == "unread"
    assert prepared.file_manifest.omissions[0].notice_file_id == 1
    if body:
        blocks, _ = prepare_gemini_request(prepared)
        assert "추측하지" in blocks[-1]["text"]
    else:
        with pytest.raises(SummaryPreparationError):
            prepare_gemini_request(prepared)


def _db_source(conn, *, body="<p>행사 안내</p>"):
    notice_id = _notice(conn)
    conn.execute("update notices set title='행사 안내',body_html=%s where id=%s", (body, notice_id))
    conn.execute(
        "insert into notice_files(notice_id,kind,file_key,file_id,file_name,url) "
        "values (%s,'attachment','id:1','1','poster.png',%s)", (notice_id, URL + "1"),
    )
    return load_summary_source(conn, notice_id)


def _prepare(original, status=200):
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(status, content=PNG),
    )) as client:
        return prepare_summary_source(original, reference_datetime=NOW, client=client)


def _run(conn, prepared):
    return summarize_and_save_prepared_notice(
        conn, prepared, _metadata(prepared),
        expected_source_revision=prepared.file_manifest.source_revision, api_key="test-key",
    )


def test_partial_roundtrip_recovery_and_source_invalidation(live_db, monkeypatch):
    original = _db_source(live_db)
    provider = Mock(return_value=json.dumps(_response("text"), ensure_ascii=False))
    monkeypatch.setattr("pipeline.transform.summarize.generate_summary_json", provider)
    partial = _prepare(original, 503)
    stored = _run(live_db, partial)
    row = _row(live_db, original.notice_id)
    assert stored.status == "needs_review" and row["deadline_on"] is None
    assert row["attachment_status"] == "unread"
    assert row["preparation_omissions"] == [{
        "notice_file_id": original.files[0].id, "url": URL + "1", "reason_code": "http_error",
    }]
    view = build_notice_summary_view(
        status=row["status"], result=row["result"], attachment_status=row["attachment_status"],
        file_references=row["file_references"], preparation_omissions=row["preparation_omissions"],
    )
    assert view.preparation_omissions[0].notice_file_id == original.files[0].id
    assert view.status == "needs_review" and "읽지 못한" in view.message
    live_db.execute("set role anon")
    try:
        assert live_db.execute(
            "select preparation_omissions from notice_summaries where notice_id=%s",
            (original.notice_id,),
        ).fetchone()["preparation_omissions"] == row["preparation_omissions"]
    finally:
        live_db.execute("reset role")
    provider.return_value = json.dumps(_response("image"), ensure_ascii=False)
    _run(live_db, _prepare(original))
    recovered = _row(live_db, original.notice_id)
    assert recovered["attachment_status"] == "all_read"
    assert recovered["preparation_omissions"] == []
    provider.return_value = json.dumps(_response("text"), ensure_ascii=False)
    _run(live_db, partial)
    preserved = _row(live_db, original.notice_id)
    for key in ("result", "file_manifest", "preparation_omissions", "file_references"):
        assert preserved[key] == recovered[key]
    live_db.execute("update notices set body_html='새 원문' where id=%s", (original.notice_id,))
    assert _row(live_db, original.notice_id)["preparation_omissions"] is None


def test_no_readable_content_records_failure_without_model_call(live_db, monkeypatch):
    original = _db_source(live_db, body=None)
    provider = Mock(side_effect=AssertionError("no source must not call Gemini"))
    monkeypatch.setattr("pipeline.transform.summarize.generate_summary_json", provider)
    outcome = _run(live_db, _prepare(original, 503))
    assert isinstance(outcome, StoredSummaryFailure)
    assert _row(live_db, original.notice_id)["result"] is None
    provider.assert_not_called()


def test_omission_cannot_claim_another_file_or_link():
    prepared = _prepare(source(file(1, "a.png")), 503)
    forged = prepared.file_manifest.model_copy(update={"omissions": (
        PreparationOmission(notice_file_id=99, url=URL + "1", reason_code="timeout"),
    )})
    with pytest.raises(SummaryPreparationError):
        prepare_gemini_request(replace(prepared, file_manifest=forged))
