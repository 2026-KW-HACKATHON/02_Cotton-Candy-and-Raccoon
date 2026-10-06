"""Offline notice adapter checks using real validated document processing results."""

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from psycopg.pq import TransactionStatus
from psycopg.rows import tuple_row

from pipeline.glossary import notice_service as module
from pipeline.glossary.client import GlossaryAPIError
from pipeline.glossary.config import GlossarySettings
from pipeline.glossary.document import RULES_VERSION
from pipeline.glossary.models import GlossaryEntry, GlossaryLookup
from pipeline.glossary.process import process_notice_glossary
from pipeline.glossary.service import query_glossary
from pipeline.glossary.source import NoticeGlossaryInput, notice_content_revision, source_hash
from pipeline.storage.glossary import GlossaryStorageError
from pipeline.storage.notice_glossary import NoticeGlossaryStorageError
from pipeline.transform.html_text import html_to_notice_text

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


def lookup_result(query, *, cached=False):
    entries = ()
    if query == "익일":
        entries = (
            GlossaryEntry(
                provider="opendict",
                entry_id="123",
                sense_id="001",
                headword=query,
                definition="어떤 날의 다음 날.",
                easy_terms=("다음 날",),
                source_url="https://opendict.korean.go.kr/dictionary/view?sense_no=123",
            ),
        )
    return GlossaryLookup(
        query=query,
        status="found" if entries else "not_found",
        entries=entries,
        providers_checked=("opendict",),
        queried_at=NOW,
        from_cache=cached,
    )


def processed(source, *, limit=100):
    return process_notice_glossary(source, lookup_result, max_queries=limit, clock=lambda: NOW)


class MemoryConnection:
    """Model caller-owned transactions and rollback a savepoint's stored changes."""

    def __init__(self):
        self.autocommit = False
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)
        self.dictionaries = {}
        self.documents = {}
        self.events = []
        self.transaction_depth = 0
        self.commit = MagicMock()
        self.rollback = MagicMock()
        self.close = MagicMock()

    def execute(self, statement):
        self.events.append(("execute", statement))
        self.info.transaction_status = TransactionStatus.INTRANS

    @contextmanager
    def transaction(self):
        assert self.info.transaction_status == TransactionStatus.INTRANS
        dictionaries, documents = dict(self.dictionaries), dict(self.documents)
        self.events.append(("savepoint",))
        self.transaction_depth += 1
        try:
            yield
        except Exception:
            self.dictionaries = dictionaries
            self.documents = documents
            self.events.append(("rollback_savepoint",))
            raise
        else:
            self.events.append(("release_savepoint",))
        finally:
            self.transaction_depth -= 1


@pytest.fixture
def store(monkeypatch):
    conn = MemoryConnection()

    def get_document(actual_conn, notice_id, *, source_hash, rules_version):
        assert actual_conn is conn
        conn.events.append(("get_document", notice_id))
        value = conn.documents.get(notice_id)
        if value and value.source_hash == source_hash and value.rules_version == rules_version:
            return value
        return None

    def save_document(actual_conn, value):
        assert actual_conn is conn and conn.transaction_depth == 1
        conn.events.append(("save_document", value.notice_id))
        conn.documents[value.notice_id] = value

    def get_dictionary(actual_conn, query):
        assert actual_conn is conn
        return conn.dictionaries.get(query)

    def save_dictionary(actual_conn, value):
        assert actual_conn is conn and conn.transaction_depth == 1
        conn.events.append(("save_dictionary", value.query))
        conn.dictionaries[value.query] = value.model_copy(update={"from_cache": True})

    mocks = SimpleNamespace(
        get_document=MagicMock(side_effect=get_document),
        save_document=MagicMock(side_effect=save_document),
        get_dictionary=MagicMock(side_effect=get_dictionary),
        save_dictionary=MagicMock(side_effect=save_dictionary),
        lookup=MagicMock(side_effect=lambda query, **kwargs: lookup_result(query)),
    )
    monkeypatch.setattr(module, "get_notice_glossary", mocks.get_document)
    monkeypatch.setattr(module, "save_notice_glossary", mocks.save_document)
    monkeypatch.setattr(module, "get_glossary", mocks.get_dictionary)
    monkeypatch.setattr(module, "save_glossary", mocks.save_dictionary)
    monkeypatch.setattr(module, "query_glossary", mocks.lookup)
    return conn, mocks


@pytest.mark.parametrize(
    "body_html", [None, "", "<p>익일에 접수합니다.</p><p>공람 가능합니다.</p>"]
)
def test_load_existing_title_and_html_uses_shared_converter_without_source_writes(
    monkeypatch, body_html
):
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = ("안내", body_html)
    convert = MagicMock(wraps=html_to_notice_text)
    monkeypatch.setattr(module, "html_to_notice_text", convert)
    source = module.load_notice_glossary_input(conn, 42)
    body = html_to_notice_text(body_html)
    assert source == NoticeGlossaryInput(
        notice_id=42,
        text="안내" + ("\n" + body if body else ""),
        notice_revision=notice_content_revision("안내", body_html),
    )
    convert.assert_called_once_with(body_html)
    conn.cursor.assert_called_once_with(row_factory=tuple_row)
    cursor.execute.assert_called_once_with(
        "select title, body_html from public.notices where id = %s", (42,)
    )
    conn.execute.assert_not_called()
    conn.commit.assert_not_called()
    conn.close.assert_not_called()


@pytest.mark.parametrize("notice_id", [0, -1, True, "42", None])
def test_invalid_notice_id_is_rejected_before_database_read(notice_id):
    conn = MagicMock()
    with pytest.raises(ValueError):
        module.load_notice_glossary_input(conn, notice_id)
    conn.cursor.assert_not_called()


def test_missing_notice_is_reported_without_writing():
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    with pytest.raises(ValueError, match="찾을 수"):
        module.load_notice_glossary_input(conn, 42)
    conn.execute.assert_not_called()
    conn.commit.assert_not_called()


@pytest.mark.parametrize("invalid", ["no_notice_id", "autocommit"])
def test_invalid_db_storage_input_fails_before_lookup_or_writes(store, invalid):
    conn, mocks = store
    source = NoticeGlossaryInput(notice_id=None if invalid == "no_notice_id" else 42, text="익일")
    conn.autocommit = invalid == "autocommit"
    with pytest.raises(ValueError):
        module.process_and_store_notice_glossary(conn, source)
    mocks.get_document.assert_not_called()
    mocks.lookup.assert_not_called()
    assert conn.events == []


@pytest.mark.parametrize("limit", [0, -1, True, "1", None])
@pytest.mark.parametrize("completed_cache", [False, True])
def test_invalid_query_limit_fails_even_with_completed_cache(store, limit, completed_cache):
    conn, mocks = store
    source = NoticeGlossaryInput(notice_id=42, text="익일")
    if completed_cache:
        conn.documents[42] = processed(source)
    old = dict(conn.documents)
    with pytest.raises(ValueError):
        module.process_and_store_notice_glossary(conn, source, max_queries=limit)
    mocks.lookup.assert_not_called()
    mocks.save_document.assert_not_called()
    assert conn.documents == old


def test_completed_result_reuses_all_saved_work_without_credentials(store, monkeypatch):
    conn, mocks = store
    source = NoticeGlossaryInput(notice_id=42, text="익일 공람")
    saved = processed(source)
    conn.documents[42] = saved
    credentials = MagicMock(side_effect=AssertionError("credentials must not be read"))
    monkeypatch.setattr("pipeline.glossary.config.GlossarySettings.from_env", credentials)
    returned = module.process_and_store_notice_glossary(conn, source)
    assert returned == saved.model_copy(update={"new_query_count": 0})
    assert saved.new_query_count == 2
    mocks.lookup.assert_not_called()
    mocks.save_document.assert_not_called()
    mocks.get_dictionary.assert_not_called()
    assert conn.events == [("get_document", 42)]


def test_partial_resume_spends_budget_only_on_remaining_queries(store):
    conn, mocks = store
    source = NoticeGlossaryInput(notice_id=42, text="익일 공람")
    conn.documents[42] = processed(source, limit=1)
    returned = module.process_and_store_notice_glossary(
        conn, source, max_queries=1, clock=lambda: NOW
    )
    assert [call.args[0] for call in mocks.lookup.call_args_list] == ["공람"]
    assert returned.status == "completed"
    assert returned.new_query_count == 1
    assert returned.easy_text == "다음 날 공람"
    assert returned.original_text == source.text


def test_dictionary_cache_hits_do_not_spend_new_query_budget_or_get_resaved(store):
    conn, mocks = store
    source = NoticeGlossaryInput(notice_id=42, text="익일 공람")
    conn.dictionaries = {query: lookup_result(query, cached=True) for query in ("익일", "공람")}
    returned = module.process_and_store_notice_glossary(
        conn, source, max_queries=1, clock=lambda: NOW
    )
    assert returned.status == "completed" and returned.new_query_count == 0
    mocks.lookup.assert_not_called()
    mocks.save_dictionary.assert_not_called()
    mocks.save_document.assert_called_once()


def test_two_active_empty_searches_save_and_reuse_not_found_across_notices_without_krdict(store):
    conn, mocks = store
    mocks.lookup.side_effect = query_glossary
    onterm, opendict = MagicMock(), MagicMock()
    onterm.lookup.return_value = opendict.lookup.return_value = ()
    settings = GlossarySettings(onterm_api_key="onterm-fake", opendict_api_key="opendict-fake")
    assert not hasattr(settings, "krdict_api_key")
    original = "정산없음"
    first = module.process_and_store_notice_glossary(
        conn,
        NoticeGlossaryInput(notice_id=42, text=original),
        settings=settings,
        onterm_client=onterm,
        opendict_client=opendict,
        clock=lambda: NOW,
    )
    assert first.status == "completed" and first.new_query_count == 1
    assert first.terms[0].status == "not_found"
    assert first.queries[0].lookup.providers_checked == ("onterm", "opendict")
    assert conn.dictionaries[original].status == "not_found"
    onterm.lookup.side_effect = opendict.lookup.side_effect = AssertionError(
        "cached miss must be reused"
    )
    second = module.process_and_store_notice_glossary(
        conn,
        NoticeGlossaryInput(notice_id=43, text=original),
        settings=settings,
        onterm_client=onterm,
        opendict_client=opendict,
        clock=lambda: NOW,
    )
    assert second.status == "completed" and second.new_query_count == 0
    assert (
        first.original_text
        == first.easy_text
        == second.original_text
        == second.easy_text
        == original
    )
    assert second.queries[0].lookup.from_cache
    onterm.lookup.assert_called_once_with(original)
    opendict.lookup.assert_called_once_with(original)
    assert mocks.lookup.call_count == 1
    assert mocks.save_dictionary.call_count == 1
    assert mocks.save_document.call_count == 2


@pytest.mark.parametrize("stale", ["source", "rules"])
def test_stale_source_or_rules_are_not_reused(store, stale):
    conn, mocks = store
    source = NoticeGlossaryInput(notice_id=42, text="익일")
    old = processed(NoticeGlossaryInput(notice_id=42, text="공람"))
    if stale == "rules":
        old = processed(source).model_copy(update={"rules_version": "older-rules"})
    conn.documents[42] = old
    returned = module.process_and_store_notice_glossary(conn, source, clock=lambda: NOW)
    mocks.lookup.assert_called_once()
    assert returned.source_hash == source_hash(source)
    assert returned.rules_version == RULES_VERSION
    mocks.get_document.assert_any_call(
        conn, 42, source_hash=source_hash(source), rules_version=RULES_VERSION
    )


def test_refresh_reprocesses_matching_completed_document(store):
    conn, mocks = store
    source = NoticeGlossaryInput(notice_id=42, text="익일")
    conn.documents[42] = processed(source)
    returned = module.process_and_store_notice_glossary(
        conn, source, refresh=True, clock=lambda: NOW
    )
    mocks.lookup.assert_called_once()
    assert returned.new_query_count == 1
    mocks.save_document.assert_called_once()


@pytest.mark.parametrize("transaction_status", [TransactionStatus.IDLE, TransactionStatus.INTRANS])
def test_saves_use_callers_connection_savepoint_and_read_back(transaction_status, store):
    conn, mocks = store
    conn.info.transaction_status = transaction_status
    source = NoticeGlossaryInput(notice_id=42, text="익일 공람")
    settings = GlossarySettings(opendict_api_key="fake-key")
    clients = [MagicMock() for _ in range(2)]

    def clock():
        return NOW

    returned = module.process_and_store_notice_glossary(
        conn,
        source,
        max_queries=1,
        settings=settings,
        clock=clock,
        onterm_client=clients[0],
        opendict_client=clients[1],
    )
    assert returned is conn.documents[42]
    assert returned.status == "partial" and returned.new_query_count == 1
    mocks.lookup.assert_called_once_with(
        "익일",
        settings=settings,
        onterm_client=clients[0],
        opendict_client=clients[1],
        clock=clock,
    )
    assert (("execute", "select 1") in conn.events) == (
        transaction_status == TransactionStatus.IDLE
    )
    assert conn.events[-4:] == [
        ("save_dictionary", "익일"),
        ("save_document", 42),
        ("get_document", 42),
        ("release_savepoint",),
    ]
    conn.commit.assert_not_called()
    conn.rollback.assert_not_called()
    conn.close.assert_not_called()
    for client in clients:
        client.close.assert_not_called()


def test_reversed_sources_save_dictionary_results_in_the_same_order(store):
    conn, mocks = store
    write_orders = []
    for notice_id, text in ((42, "익일 공람"), (43, "공람 익일")):
        # Use uncached words in each run so both documents exercise dictionary writes.
        conn.dictionaries.clear()
        mocks.save_dictionary.reset_mock()
        returned = module.process_and_store_notice_glossary(
            conn, NoticeGlossaryInput(notice_id=notice_id, text=text), clock=lambda: NOW
        )
        write_orders.append([call.args[1].query for call in mocks.save_dictionary.call_args_list])
        assert [outcome.query for outcome in returned.queries] == text.split()
        assert [term.word for term in returned.terms] == text.split()
    assert write_orders == [["공람", "익일"], ["공람", "익일"]]


@pytest.mark.parametrize("failure_at", ["dictionary", "document"])
def test_atomic_save_failure_rolls_back_dictionary_and_document_changes(store, failure_at):
    conn, mocks = store
    source = NoticeGlossaryInput(notice_id=42, text="익일 공람")
    old = processed(NoticeGlossaryInput(notice_id=42, text="예전"))
    conn.documents[42] = old
    conn.dictionaries["예전"] = lookup_result("예전", cached=True)
    old_dictionaries = dict(conn.dictionaries)
    original_save = mocks.save_dictionary.side_effect
    if failure_at == "dictionary":

        def fail_second(actual_conn, value):
            if value.query == "익일":
                raise GlossaryStorageError("private-storage-secret")
            original_save(actual_conn, value)

        mocks.save_dictionary.side_effect = fail_second
        expected_error = GlossaryStorageError
    else:
        mocks.save_document.side_effect = NoticeGlossaryStorageError("private-storage-secret")
        expected_error = NoticeGlossaryStorageError
    with pytest.raises(expected_error):
        module.process_and_store_notice_glossary(conn, source, clock=lambda: NOW)
    assert conn.dictionaries == old_dictionaries
    assert conn.documents == {42: old}
    assert ("save_dictionary", "공람") in conn.events
    assert conn.events[-1] == ("rollback_savepoint",)
    conn.commit.assert_not_called()
    conn.rollback.assert_not_called()
    conn.close.assert_not_called()


def test_expected_api_failure_saves_explicit_partial_result_without_exception_text(store):
    conn, mocks = store
    mocks.lookup.side_effect = GlossaryAPIError("opendict", "private-api-secret")
    returned = module.process_and_store_notice_glossary(
        conn, NoticeGlossaryInput(notice_id=42, text="익일"), clock=lambda: NOW
    )
    assert returned.status == "partial"
    assert returned.terms[0].error_code == "api"
    assert "private-api-secret" not in returned.model_dump_json()
    mocks.save_dictionary.assert_not_called()


def test_missing_readback_is_an_error_instead_of_unverified_success(store):
    conn, mocks = store
    old = processed(NoticeGlossaryInput(notice_id=42, text="예전"))
    conn.documents[42] = old
    mocks.get_document.side_effect = [None, None]
    with pytest.raises(RuntimeError, match="재조회"):
        module.process_and_store_notice_glossary(
            conn, NoticeGlossaryInput(notice_id=42, text="익일"), clock=lambda: NOW
        )
    assert mocks.get_document.call_count == 2
    assert conn.documents == {42: old}
    assert conn.dictionaries == {}
    assert conn.events[-1] == ("rollback_savepoint",)
    conn.commit.assert_not_called()
    conn.close.assert_not_called()


def test_readback_for_another_revision_is_rejected_and_writes_rolled_back(store, monkeypatch):
    conn, mocks = store
    source = NoticeGlossaryInput(
        notice_id=42, text="익일", notice_revision=notice_content_revision("익일", "<p></p>")
    )
    other_source = source.model_copy(
        update={"notice_revision": notice_content_revision("익일", "<div></div>")}
    )
    older = processed(other_source)
    monkeypatch.setattr(module, "load_notice_glossary_input", lambda *args: source)
    mocks.get_document.side_effect = [None, older]
    with pytest.raises(NoticeGlossaryStorageError, match="원문 버전"):
        module.process_and_store_notice_glossary(conn, source, clock=lambda: NOW)
    assert conn.documents == {}
    assert conn.dictionaries == {}
    assert conn.events[-1] == ("rollback_savepoint",)
    conn.commit.assert_not_called()
