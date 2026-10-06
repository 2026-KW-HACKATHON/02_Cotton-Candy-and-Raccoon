"""Recover only verified original UUIDs; never guess or silently discard files."""

import os
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg
import pytest
from bs4 import BeautifulSoup

from pipeline.attachments.nowon_html import (
    AttachmentError,
    extract_files,
    recover_masked_body_urls,
)
from pipeline.cli import main
from pipeline.collect_nowon import _prepare_notice, collect_and_save_nowon
from pipeline.config import DatabaseSettings, NowonSettings
from pipeline.models import RawNotice
from pipeline.sources.nowon_api import NowonCollection, PageStatus
from pipeline.storage.notice_bundle import save_notice_with_files

FILE_ID = "21986319-6614-4482-8756-e5ff5b2908a1"
MASK = "21***-e5ff5b2908a1"
SETTINGS = NowonSettings("private-key", 2, 7)
EMPTY_FILES = "<table><tr><th>첨부파일</th><td>첨부파일이 없습니다.</td></tr></table>"


def file_url(file_id: str = FILE_ID, sn: str = "310123") -> str:
    return f"/component/file/ND_fileDownload.do?q_fileSn={sn}&amp;q_fileId={file_id}"


def notice(post_sn: str = "001234", html: str | None = None) -> RawNotice:
    return RawNotice(
        category="nowon",
        source_board="1001",
        dong_group=None,
        is_pinned=False,
        post_sn=post_sn,
        title="Notice",
        department=None,
        registered_on="2026-10-02",
        url="http://www.nowon.kr:80/www/user/bbs/BD_selectBbs.do"
        f"?q_bbsCode=1001&q_bbscttSn={post_sn}",
        body_html=html if html is not None else f'<p>Body</p><img src="{file_url(MASK)}">',
        license_type="KOGL-4",
    )


def page(post_sn: str = "001234", html: str | None = None) -> str:
    return (
        f'<input name="q_bbscttSn" value="{post_sn}">'
        '<div class="article-body">'
        + (html if html is not None else f'<img src="{file_url()}">')
        + "</div>"
        + EMPTY_FILES
    )


def test_repair_body_and_metadata_together_without_changing_input() -> None:
    source = notice()
    repaired = recover_masked_body_urls(source, page())
    assert "***" in source.body_html
    assert "***" not in repaired.body_html
    image = BeautifulSoup(repaired.body_html, "html.parser").img
    assert image["src"].startswith("https://www.nowon.kr/component/file/")
    files = extract_files(repaired)
    assert files[0].file_id == FILE_ID
    assert files[0].file_key == f"id:{FILE_ID}"
    assert files[0].url == image["src"]
    assert repaired.post_sn == "001234"
    assert recover_masked_body_urls(repaired, page()) is repaired


@pytest.mark.parametrize(
    "html",
    [
        '<p class="x">Body &amp; original spacing</p>',
        '<a href="https://example.org/***">ordinary link</a>',
        '<img src="/editor/image.png">',
        "<p>*** is ordinary text</p>",
    ],
)
def test_unmasked_files_preserve_exact_api_html(html: str) -> None:
    source = notice(html=html)
    assert recover_masked_body_urls(source, "not a verified page") is source


def test_encoded_stars_entities_and_duplicate_images_are_repaired() -> None:
    source = notice(html=f'<img src="{file_url(MASK.replace("*", "%2A"))}">' * 2)
    repaired = recover_masked_body_urls(source, page(html=f'<img src="{file_url()}">' * 2))
    assert len(extract_files(repaired)) == 1
    assert len(BeautifulSoup(repaired.body_html, "html.parser").select("img")) == 2


def test_masked_body_download_link_keeps_its_name_and_role() -> None:
    source = notice(html=f'<a href="{file_url(MASK)}">보고서.pdf</a>')
    repaired = recover_masked_body_urls(
        source,
        page(html=f'<a href="{file_url()}">원문 이름</a>'),
    )
    file = extract_files(repaired)[0]
    assert file.kind == "attachment" and file.file_name == "보고서.pdf"
    assert file.file_id == FILE_ID


@pytest.mark.parametrize(
    "original",
    [
        '<img src="/component/file/ND_fileDownload.do?q_fileSn=999&q_fileId=' + FILE_ID + '">',
        '<img src="' + file_url("21986319-6614-4482-8756-000000000000") + '">',
        '<img src="' + file_url(MASK) + '">',
        '<img src="https://evil.example' + file_url() + '">',
        '<img src="' + file_url().replace("ND_fileDownload.do", "other.do") + '">',
        '<img src="' + file_url() + '&amp;q_fileId=duplicate">',
        '<a href="' + file_url() + '">not an image role</a>',
        "",
    ],
)
def test_no_matching_official_image_is_not_guessed(original: str) -> None:
    with pytest.raises(AttachmentError) as caught:
        recover_masked_body_urls(notice(), page(html=original))
    assert caught.value.code == "masked_file_unresolved"


@pytest.mark.parametrize(
    "original_page",
    [
        page("999"),
        page().replace('name="q_bbscttSn"', 'name="other"'),
        page() + '<input name="q_bbscttSn" value="001234">',
        page().replace('class="article-body"', 'class="other"'),
        page() + '<div class="article-body"></div>',
    ],
)
def test_original_identity_and_body_must_be_verified(original_page: str) -> None:
    with pytest.raises(AttachmentError) as caught:
        recover_masked_body_urls(notice(), original_page)
    assert caught.value.code == "masked_file_source_invalid"


def test_ambiguous_uuid_is_not_selected_by_order() -> None:
    second = FILE_ID.replace("21986319", "21986318")
    html = f'<img src="{file_url()}"><img src="{file_url(second)}">'
    with pytest.raises(AttachmentError) as caught:
        recover_masked_body_urls(notice(), page(html=html))
    assert caught.value.code == "masked_file_ambiguous"


def test_external_masked_url_is_rejected() -> None:
    source = notice(html=f'<img src="https://evil.example{file_url(MASK)}">')
    with pytest.raises(AttachmentError) as caught:
        recover_masked_body_urls(source, page())
    assert caught.value.code == "masked_file_source_invalid"


def collision_input(post_sn: str = "001234") -> tuple[RawNotice, str, set[str]]:
    second_id = FILE_ID.replace("21986319", "21986318")
    source = notice(
        post_sn, html=(f'<img src="{file_url(MASK, "1")}"><img src="{file_url(MASK, "2")}">')
    )
    original = page(
        post_sn,
        html=(f'<img src="{file_url(FILE_ID, "1")}"><img src="{file_url(second_id, "2")}">'),
    )
    return source, original, {FILE_ID, second_id}


def test_same_masked_ids_are_recovered_before_collection_duplicate_check() -> None:
    source, original, ids = collision_input()
    with patch(
        "pipeline.collect_nowon.fetch_notice_page",
        return_value=(source.url, original),
    ) as fetch:
        prepared = _prepare_notice(source, SETTINGS)
    fetch.assert_called_once()
    assert prepared.failure is None
    assert {file.file_id for file in prepared.files} == ids
    assert len(prepared.files) == 2
    assert "***" not in prepared.record.body_html


def test_cli_same_masked_ids_are_recovered_before_duplicate_check(
    monkeypatch,
    mock_collect_db,
) -> None:
    monkeypatch.setenv("NOWON_NOTICE_API_KEY", "private-key")
    source, original, ids = collision_input()
    with (
        patch("pipeline.cli.collect_one", return_value=source),
        patch("pipeline.cli.fetch_notice_page", return_value=(source.url, original)),
        patch("pipeline.cli.save_notice_with_files", return_value=42) as save,
    ):
        assert main(["collect-one", "--source", "nowon"]) == 0
    assert {file.file_id for file in save.call_args.args[2]} == ids


@pytest.mark.parametrize(
    "reference",
    [
        "javascript:location.href='/component/file/ND_fileDownload.do?q_fileSn=310123'",
        "javascript:location.href='/component/file/ND_fileDownload.do?q_fileSn=310123&q_fileId=21***'",
        "mailto:person@example.org?q_fileSn=310123",
        "data:text/plain,/component/file/ND_fileDownload.do?q_fileSn=310123",
        "tel:123?q_fileSn=310123",
    ],
)
def test_non_http_links_do_not_require_file_identity_or_modify_body(reference: str) -> None:
    source = notice(html=f'<a href="{reference}">link</a>')
    assert extract_files(source) == []
    assert recover_masked_body_urls(source, "no original needed") is source
    with patch("pipeline.collect_nowon.fetch_notice_page", return_value=(source.url, page())):
        prepared = _prepare_notice(source, SETTINGS)
    assert prepared.failure is None and prepared.files == []
    assert prepared.record.body_html == source.body_html


def test_equivalent_candidate_queries_are_deduplicated_and_order_independent() -> None:
    reversed_url = f"/component/file/ND_fileDownload.do?q_fileId={FILE_ID}&amp;q_fileSn=310123"
    outcomes = []
    for refs in ((file_url(), reversed_url), (reversed_url, file_url())):
        repaired = recover_masked_body_urls(
            notice(),
            page(html="".join(f'<img src="{ref}">' for ref in refs)),
        )
        files = extract_files(repaired)
        assert len(files) == 1 and files[0].file_id == FILE_ID
        outcomes.append(repaired.body_html)
    assert outcomes[0] == outcomes[1]


def test_different_query_values_are_not_silently_merged() -> None:
    html = f'<img src="{file_url()}&amp;variant=one"><img src="{file_url()}&amp;variant=two">'
    with pytest.raises(AttachmentError) as caught:
        recover_masked_body_urls(notice(), page(html=html))
    assert caught.value.code == "masked_file_ambiguous"


def test_one_missing_image_does_not_allow_partial_repair_to_be_saved() -> None:
    source = notice(html=notice().body_html + f'<img src="{file_url("22***-other", "other")}">')
    with patch("pipeline.collect_nowon.fetch_notice_page", return_value=(source.url, page())):
        prepared = _prepare_notice(source, SETTINGS)
    assert prepared.failure.reason_code == "masked_file_unresolved"
    assert prepared.files == []
    assert "***" in prepared.record.body_html


def test_failed_repair_skips_only_that_notice_and_never_saves_its_partial_files() -> None:
    sources = (notice(), notice("002", "<p>Good</p>"))
    listing = NowonCollection(sources, (PageStatus(1, 2, 1, True),), 2, True, (), ())
    conn = MagicMock()
    conn.closed = False
    with (
        patch("pipeline.collect_nowon.collect_all", return_value=listing),
        patch(
            "pipeline.collect_nowon.fetch_notice_page", return_value=(sources[0].url, page(html=""))
        ),
        patch("pipeline.collect_nowon.psycopg.connect", return_value=conn),
        patch("pipeline.collect_nowon.save_notice_with_files") as save,
    ):
        result = collect_and_save_nowon(SETTINGS, DatabaseSettings("postgresql://unused/test"))
    assert result.saved_count == 1 and not result.complete
    assert result.failures[0].reason_code == "masked_file_unresolved"
    assert save.call_args.args[1].post_sn == "002"


def test_cli_collect_one_uses_recovered_body_and_files(monkeypatch, mock_collect_db) -> None:
    monkeypatch.setenv("NOWON_NOTICE_API_KEY", "private-key")
    source = notice()
    with (
        patch("pipeline.cli.collect_one", return_value=source),
        patch("pipeline.cli.fetch_notice_page", return_value=(source.url, page())),
        patch("pipeline.cli.save_notice_with_files", return_value=42) as save,
    ):
        assert main(["collect-one", "--source", "nowon"]) == 0
    assert "***" not in save.call_args.args[1].body_html
    assert save.call_args.args[2][0].file_id == FILE_ID


def test_cli_failed_repair_does_not_save(monkeypatch, mock_collect_db) -> None:
    monkeypatch.setenv("NOWON_NOTICE_API_KEY", "private-key")
    source = notice()
    with (
        patch("pipeline.cli.collect_one", return_value=source),
        patch("pipeline.cli.fetch_notice_page", return_value=(source.url, page(html=""))),
        patch("pipeline.cli.save_notice_with_files") as save,
    ):
        assert main(["collect-one", "--source", "nowon"]) == 1
    save.assert_not_called()


@pytest.mark.parametrize("collision", [False, True])
def test_repaired_notice_repeat_storage_keeps_files_and_not_modified(collision: bool) -> None:
    dsn = os.getenv("PIPELINE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("PIPELINE_TEST_DATABASE_URL is required for recovery storage integration")
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.info.host == "127.0.0.1"
        assert conn.info.dbname.startswith("pipeline_schema_test_")
        post_sn = str(uuid4().int)
        if collision:
            source, original, expected_ids = collision_input(post_sn)
        else:
            source, original, expected_ids = notice(post_sn), page(post_sn), {FILE_ID}
        with patch(
            "pipeline.collect_nowon.fetch_notice_page",
            return_value=(source.url, original),
        ):
            prepared = _prepare_notice(source, SETTINGS)
        assert prepared.failure is None
        with conn.transaction(force_rollback=True):
            first = save_notice_with_files(conn, prepared.record, prepared.files)
            second = save_notice_with_files(conn, prepared.record, prepared.files)
            assert first == second
            row = conn.execute(
                "select body_html,is_modified from notices where id=%s", (first,)
            ).fetchone()
            assert "***" not in row[0] and row[1] is False
            stored_ids = conn.execute(
                "select file_id from notice_files where notice_id=%s", (first,)
            ).fetchall()
            assert len(stored_ids) == len(expected_ids)
            assert {item[0] for item in stored_ids} == expected_ids
