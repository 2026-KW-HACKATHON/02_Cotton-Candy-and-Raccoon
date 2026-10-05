from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime

import pytest

from pipeline.models import FileRecord, NoticeRecord, RawNotice


@pytest.fixture
def raw_notice() -> RawNotice:
    return RawNotice(
        source_board="1001",
        category="nowon", dong_group=None, is_pinned=False, post_sn="001234",
        title="공지", department=None, registered_on="2026-09-24",
        url="http://www.nowon.kr:80/example", body_html="<p>&lt; 안내</p>",
        license_type="KOGL-4",
    )


@pytest.fixture
def notice() -> NoticeRecord:
    return NoticeRecord(
        source_board="1001",
        category="nowon", dong_group=None, is_pinned=False, post_sn="001234",
        title="공지", department=None, registered_on=date(2026, 9, 24),
        url="https://www.nowon.kr/example", body_html=None, license_type=None,
    )


@pytest.fixture
def file_record() -> FileRecord:
    return FileRecord(
        source_board="1001",
        category="nowon", post_sn="001234", kind="attachment", file_sn="0001",
        file_id="file-id", file_name="안내.pdf", url="https://www.nowon.kr/file",
    )


def test_valid_models_preserve_source_values_and_keys(
    raw_notice: RawNotice, notice: NoticeRecord, file_record: FileRecord,
) -> None:
    assert raw_notice.post_sn == notice.post_sn == file_record.post_sn == "001234"
    assert raw_notice.registered_on == "2026-09-24"
    assert raw_notice.body_html == "<p>&lt; 안내</p>"
    assert raw_notice.url == "http://www.nowon.kr:80/example"
    assert notice.registered_on == date(2026, 9, 24)
    assert file_record.file_sn == "0001"
    other_source = replace(file_record, category="dong", source_board="1042")
    assert (other_source.category, other_source.post_sn) == ("dong", "001234")
    assert (file_record.category, file_record.post_sn) != (
        other_source.category, other_source.post_sn,
    )


@pytest.mark.parametrize("fixture_name", ["raw_notice", "notice", "file_record"])
@pytest.mark.parametrize("bad_id", [1234, None, True, b"001234"])
def test_post_sn_rejects_non_string(
    fixture_name: str, bad_id: object, request: pytest.FixtureRequest,
) -> None:
    with pytest.raises(TypeError, match="post_sn"):
        replace(request.getfixturevalue(fixture_name), post_sn=bad_id)


@pytest.mark.parametrize("fixture_name,field_name", [
    ("raw_notice", "post_sn"), ("raw_notice", "title"), ("raw_notice", "url"),
    ("raw_notice", "registered_on"),
    ("notice", "post_sn"), ("notice", "title"), ("notice", "url"),
    ("file_record", "post_sn"), ("file_record", "url"),
    ("raw_notice", "source_board"), ("notice", "source_board"),
    ("file_record", "source_board"),
])
@pytest.mark.parametrize("value", ["", " \t\n", None, 123])
def test_required_strings_reject_invalid_values(
    fixture_name: str, field_name: str, value: object, request: pytest.FixtureRequest,
) -> None:
    error = ValueError if isinstance(value, str) else TypeError
    with pytest.raises(error, match=field_name):
        replace(request.getfixturevalue(fixture_name), **{field_name: value})


@pytest.mark.parametrize("fixture_name,field_name", [
    ("raw_notice", "department"), ("raw_notice", "body_html"),
    ("notice", "department"), ("notice", "body_html"), ("file_record", "file_name"),
])
def test_optional_strings_accept_none_and_preserve_text(
    fixture_name: str, field_name: str, request: pytest.FixtureRequest,
) -> None:
    model = request.getfixturevalue(fixture_name)
    assert getattr(replace(model, **{field_name: None}), field_name) is None
    assert getattr(replace(model, **{field_name: " 값 "}), field_name) == " 값 "
    for value in ("", " \n", 123):
        error = ValueError if isinstance(value, str) else TypeError
        with pytest.raises(error, match=field_name):
            replace(model, **{field_name: value})


@pytest.mark.parametrize("value", ["2026-09-24", datetime(2026, 9, 24), None, 20260924])
def test_notice_requires_exact_date(notice: NoticeRecord, value: object) -> None:
    with pytest.raises(TypeError, match="registered_on"):
        replace(notice, registered_on=value)


def test_raw_date_remains_unparsed(raw_notice: RawNotice) -> None:
    assert replace(raw_notice, registered_on="source-date").registered_on == "source-date"
    with pytest.raises(TypeError, match="registered_on"):
        replace(raw_notice, registered_on=date(2026, 9, 24))


@pytest.mark.parametrize("fixture_name,field_name", [
    ("raw_notice", "category"), ("raw_notice", "dong_group"),
    ("raw_notice", "license_type"), ("notice", "category"),
    ("notice", "dong_group"), ("notice", "license_type"),
    ("file_record", "category"), ("file_record", "kind"),
])
@pytest.mark.parametrize("value", ["invalid", "", " ", 123, []])
def test_enum_fields_are_checked_at_runtime(
    fixture_name: str, field_name: str, value: object, request: pytest.FixtureRequest,
) -> None:
    error = ValueError if isinstance(value, str) else TypeError
    with pytest.raises(error, match=field_name):
        replace(request.getfixturevalue(fixture_name), **{field_name: value})


@pytest.mark.parametrize("value", [None, "KOGL-1", "KOGL-2", "KOGL-3", "KOGL-4"])
def test_all_license_types(notice: NoticeRecord, value: str | None) -> None:
    assert replace(notice, license_type=value).license_type == value


@pytest.mark.parametrize("fixture_name", ["raw_notice", "notice"])
@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_is_pinned_requires_bool(
    fixture_name: str, value: object, request: pytest.FixtureRequest,
) -> None:
    with pytest.raises(TypeError, match="is_pinned"):
        replace(request.getfixturevalue(fixture_name), is_pinned=value)


def test_notice_enforces_db_shape(notice: NoticeRecord, raw_notice: RawNotice) -> None:
    with pytest.raises(ValueError, match="nowon"):
        replace(notice, is_pinned=True)
    for group in ("wolgye1", "other"):
        with pytest.raises(ValueError, match="nowon"):
            replace(notice, dong_group=group)
        for pinned in (True, False):
            record = replace(notice, category="dong", source_board="1042",
                             dong_group=group, is_pinned=pinned)
            assert record.dong_group == group
            assert record.is_pinned is pinned
    with pytest.raises(ValueError, match="dong"):
        replace(notice, category="dong", source_board="1042")
    # Source shape may be incomplete; transform must resolve it before storage.
    assert replace(raw_notice, category="dong", source_board="1042").dong_group is None


def test_inline_image_accepts_missing_name(file_record: FileRecord) -> None:
    image = replace(file_record, kind="inline_image", file_name=None)
    assert image.kind == "inline_image"
    assert image.file_name is None


@pytest.mark.parametrize("fixture_name", ["raw_notice", "notice", "file_record"])
def test_missing_fields_and_mutation_are_rejected(
    fixture_name: str, request: pytest.FixtureRequest,
) -> None:
    model = request.getfixturevalue(fixture_name)
    values = {field.name: getattr(model, field.name) for field in fields(model)}
    del values["post_sn"]
    with pytest.raises(TypeError, match="post_sn"):
        type(model)(**values)
    with pytest.raises(FrozenInstanceError):
        model.post_sn = "changed"


def test_notice_excludes_storage_managed_fields(notice: NoticeRecord) -> None:
    names = {field.name for field in fields(notice)}
    assert not names & {"id", "created_at", "updated_at", "is_modified", "is_visible"}
