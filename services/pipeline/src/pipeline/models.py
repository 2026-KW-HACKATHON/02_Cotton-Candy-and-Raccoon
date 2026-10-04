"""Contracts for sources -> transform -> storage, without I/O or coercion.

Optional strings accept None, but not blank strings. Callers normalize empty
source values to None. Dates, URLs and HTML are normalized by transform.
FileRecord travels separately with its parent's (category, source_board, post_sn) key;
storage must verify that key before resolving notice_id.
"""

from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
from typing import Literal

NoticeCategory = Literal["nowon", "dong", "seoul"]
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


def _source_board(category: NoticeCategory, source_board: str) -> None:
    _choice("category", category, ("nowon", "dong", "seoul"))
    _string("source_board", source_board)
    boards = {"nowon": ("1001",), "dong": ("1042",),
              "seoul": ("21", "22", "23", "24", "25", "26", "27", "30")}
    if source_board not in boards[category]:
        raise ValueError("source_board does not match category")


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
    _choice("category", category, ("nowon", "dong", "seoul"))
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
    source_board: str
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
        _source_board(self.category, self.source_board)
        _notice_fields(
            self.category, self.dong_group, self.is_pinned, self.post_sn,
            self.title, self.url, self.department, self.body_html, self.license_type,
        )
        _string("registered_on", self.registered_on)


@dataclass(frozen=True, slots=True)
class RawSeoulNotice:
    """SeoulNewsList fields before source URL, license and date normalization.

    POST_CONTENT is the full supplied HTML; POST_EXCERPT is kept separately.
    No invented LINK/license or manager name/phone is added to this contract.
    """

    source_board: str
    post_sn: str
    blog_name: str
    title: str
    registered_on: str
    modified_on: str | None
    post_status: str
    department: str | None
    body_html: str | None = field(repr=False)
    excerpt_html: str | None = field(repr=False)
    thumbnail_url: str | None

    def __post_init__(self) -> None:
        _source_board("seoul", self.source_board)
        for name in ("post_sn", "blog_name", "title", "registered_on"):
            _string(name, getattr(self, name))
        if not self.post_sn.isascii() or not self.post_sn.isdecimal():
            raise ValueError("post_sn must contain ASCII digits")
        _choice("post_status", self.post_status, ("publish",))
        for name in ("modified_on", "department", "body_html", "excerpt_html", "thumbnail_url"):
            _string(name, getattr(self, name), optional=True)


@dataclass(frozen=True, slots=True)
class NoticeRecord:
    """Normalized notice input; generated IDs and sync state belong to storage."""

    category: NoticeCategory
    source_board: str
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
        _source_board(self.category, self.source_board)
        _notice_fields(
            self.category, self.dong_group, self.is_pinned, self.post_sn,
            self.title, self.url, self.department, self.body_html, self.license_type,
        )
        # datetime subclasses date, but would silently introduce a time component.
        if type(self.registered_on) is not date:
            raise TypeError("registered_on must be a date, not a datetime or string")
        if self.category in ("nowon", "seoul") and (
            self.dong_group is not None or self.is_pinned
        ):
            raise ValueError(f"{self.category} requires dong_group=None and is_pinned=False")
        if self.category == "dong" and self.dong_group is None:
            raise ValueError("dong requires dong_group")


@dataclass(frozen=True, slots=True)
class FileRecord:
    """File metadata linked by source key; storage resolves the DB notice_id."""

    category: NoticeCategory
    source_board: str
    post_sn: str
    kind: FileKind
    file_sn: str | None
    file_id: str | None
    file_name: str | None
    url: str

    def __post_init__(self) -> None:
        _source_board(self.category, self.source_board)
        _choice("kind", self.kind, ("attachment", "inline_image"))
        for name, value in (
            ("post_sn", self.post_sn), ("url", self.url),
        ):
            _string(name, value)
        _string("file_name", self.file_name, optional=True)
        for name, value in (("file_sn", self.file_sn), ("file_id", self.file_id)):
            _string(name, value, optional=True)
            if value is not None and value != value.strip():
                raise ValueError(f"{name} must not have surrounding whitespace")
        if self.url != self.url.strip():
            raise ValueError("url must not have surrounding whitespace")

    @property
    def file_key(self) -> str:
        """Derive the DB key from the actual ID or the already-normalized URL."""
        if self.file_id is not None:
            return "id:" + self.file_id
        return "url:" + sha256(self.url.encode("utf-8")).hexdigest()
