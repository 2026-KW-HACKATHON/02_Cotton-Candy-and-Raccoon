"""Contracts for sources -> transform -> storage, without I/O or coercion.

Optional strings accept None, but not blank strings. Callers normalize empty
source values to None. Dates, URLs and HTML are normalized by transform.
FileRecord travels separately with its parent's (category, post_sn) key;
storage must verify that key before resolving notice_id.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

NoticeCategory = Literal["nowon", "dong"]
DongGroup = Literal["wolgye1", "other"]
FileKind = Literal["attachment", "inline_image"]
LicenseType = Literal["KOGL-1", "KOGL-2", "KOGL-3", "KOGL-4"]


def _string(name: str, value: object, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


def _choice(name: str, value: object, choices: tuple[str, ...]) -> None:
    _string(name, value)
    if value not in choices:
        raise ValueError(f"{name} has an unsupported value")


def _notice_fields(
    category: NoticeCategory,
    dong_group: DongGroup | None,
    is_pinned: bool,
    post_sn: str,
    title: str,
    url: str,
    department: str | None,
    body_html: str | None,
    license_type: LicenseType | None,
) -> None:
    _choice("category", category, ("nowon", "dong"))
    if dong_group is not None:
        _choice("dong_group", dong_group, ("wolgye1", "other"))
    if type(is_pinned) is not bool:
        raise TypeError("is_pinned must be a bool")
    for name, value in (("post_sn", post_sn), ("title", title), ("url", url)):
        _string(name, value)
    for name, value in (("department", department), ("body_html", body_html)):
        _string(name, value, optional=True)
    if license_type is not None:
        _choice("license_type", license_type, ("KOGL-1", "KOGL-2", "KOGL-3", "KOGL-4"))


@dataclass(frozen=True, slots=True)
class RawNotice:
    """Parsed source fields; registered_on remains the original date string.

    Source adapters map API names (ID, TITLE, etc.) to these names. No string
    trimming, HTML decoding or DB shape validation happens in this model.
    """

    category: NoticeCategory
    dong_group: DongGroup | None
    is_pinned: bool
    post_sn: str
    title: str
    department: str | None
    registered_on: str
    url: str
    body_html: str | None
    license_type: LicenseType | None

    def __post_init__(self) -> None:
        _notice_fields(
            self.category, self.dong_group, self.is_pinned, self.post_sn,
            self.title, self.url, self.department, self.body_html, self.license_type,
        )
        _string("registered_on", self.registered_on)


@dataclass(frozen=True, slots=True)
class NoticeRecord:
    """Normalized notice input; generated IDs and sync state belong to storage."""

    category: NoticeCategory
    dong_group: DongGroup | None
    is_pinned: bool
    post_sn: str
    title: str
    department: str | None
    registered_on: date
    url: str
    body_html: str | None
    license_type: LicenseType | None

    def __post_init__(self) -> None:
        _notice_fields(
            self.category, self.dong_group, self.is_pinned, self.post_sn,
            self.title, self.url, self.department, self.body_html, self.license_type,
        )
        # datetime subclasses date, but would silently introduce a time component.
        if type(self.registered_on) is not date:
            raise TypeError("registered_on must be a date, not a datetime or string")
        if self.category == "nowon" and (self.dong_group is not None or self.is_pinned):
            raise ValueError("nowon requires dong_group=None and is_pinned=False")
        if self.category == "dong" and self.dong_group is None:
            raise ValueError("dong requires dong_group")


@dataclass(frozen=True, slots=True)
class FileRecord:
    """File metadata linked by source key; storage resolves the DB notice_id."""

    category: NoticeCategory
    post_sn: str
    kind: FileKind
    file_sn: str
    file_id: str
    file_name: str | None
    url: str

    def __post_init__(self) -> None:
        _choice("category", self.category, ("nowon", "dong"))
        _choice("kind", self.kind, ("attachment", "inline_image"))
        for name, value in (
            ("post_sn", self.post_sn), ("file_sn", self.file_sn),
            ("file_id", self.file_id), ("url", self.url),
        ):
            _string(name, value)
        _string("file_name", self.file_name, optional=True)
