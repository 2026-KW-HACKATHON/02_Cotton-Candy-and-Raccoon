"""Read one visible notice and its files in one database snapshot."""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from psycopg import Connection


@dataclass(frozen=True, slots=True)
class StoredFile:
    id: int
    kind: Literal["attachment", "inline_image"]
    file_id: str
    file_name: str | None
    url: str


@dataclass(frozen=True, slots=True)
class SummarySource:
    notice_id: int
    title: str
    category: str
    department: str | None
    registered_on: date
    url: str
    body_html: str | None = field(repr=False)
    files: tuple[StoredFile, ...]


def load_summary_source(conn: Connection, notice_id: int) -> SummarySource | None:
    """Read only; never commit, write, or close the caller's connection."""
    if isinstance(notice_id, bool) or not isinstance(notice_id, int) or notice_id < 1:
        raise ValueError("notice_id must be a positive integer")
    with conn.cursor() as cursor:
        cursor.execute(
            """
            select n.id, n.title, n.category, n.department, n.registered_on,
                   n.url, n.body_html,
                   coalesce((select jsonb_agg(jsonb_build_object(
                       'id', f.id, 'kind', f.kind, 'file_id', f.file_id,
                       'file_name', f.file_name, 'url', f.url
                   ) order by f.id) from notice_files f where f.notice_id = n.id), '[]'::jsonb)
            from notices n where n.id = %s and n.is_visible = true
            """,
            (notice_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return SummarySource(*row[:7], tuple(StoredFile(**item) for item in row[7]))
