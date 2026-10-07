"""Read a visible notice, files and source revision in one database snapshot."""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from psycopg import Connection
from psycopg.rows import tuple_row


@dataclass(frozen=True, slots=True)
class StoredFile:
    id: int
    kind: Literal["attachment", "inline_image"]
    file_key: str
    file_id: str | None
    file_sn: str | None
    file_name: str | None
    url: str


@dataclass(frozen=True, slots=True)
class SummarySource:
    """Preparation owner retains content_revision with these exact source files."""

    notice_id: int
    title: str
    category: str
    source_board: str
    post_sn: str
    department: str | None
    registered_on: date
    url: str
    body_html: str | None = field(repr=False)
    files: tuple[StoredFile, ...]
    content_revision: int


def load_summary_source(conn: Connection, notice_id: int) -> SummarySource | None:
    """Read without committing, locking across Gemini, or closing the caller DB.

    The statement binds body, file identities and revision to the same snapshot.
    Pass this revision unchanged to expected_source_revision after preparation;
    a fresh revision fetched separately would not belong to the prepared input.
    Explicit tuple rows work with caller connections using another row factory.
    """
    if type(notice_id) is not int or not 0 < notice_id <= 2**63 - 1:
        raise ValueError("invalid_notice_id")
    with conn.cursor(row_factory=tuple_row) as cursor:
        cursor.execute(
            """
            select n.id, n.title, n.category, n.source_board, n.post_sn,
                   n.department, n.registered_on, n.url, n.body_html,
                   coalesce((select jsonb_agg(jsonb_build_object(
                       'id', f.id, 'kind', f.kind, 'file_key', f.file_key,
                       'file_id', f.file_id, 'file_sn', f.file_sn,
                       'file_name', f.file_name, 'url', f.url
                   ) order by f.file_key, f.kind, f.id)
                       from public.notice_files f where f.notice_id = n.id), '[]'::jsonb),
                   n.content_revision
            from public.notices n where n.id = %s and n.is_visible = true
            """,
            (notice_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return SummarySource(*row[:9], tuple(StoredFile(**item) for item in row[9]), row[10])
