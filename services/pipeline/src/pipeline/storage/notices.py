"""Store one normalized notice in the caller's PostgreSQL transaction."""

from psycopg import Connection

from pipeline.models import NoticeRecord

UPSERT_NOTICE = """
insert into notices (
    category, source_board, dong_group, is_pinned, post_sn, title, department,
    registered_on, url, body_html, license_type
) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (category, source_board, post_sn) do update set
    dong_group = excluded.dong_group,
    is_pinned = excluded.is_pinned,
    title = excluded.title,
    department = excluded.department,
    registered_on = excluded.registered_on,
    url = excluded.url,
    body_html = excluded.body_html,
    license_type = excluded.license_type,
    is_modified = notices.is_modified or (
        notices.title is distinct from excluded.title
        or notices.body_html is distinct from excluded.body_html
        or notices.registered_on is distinct from excluded.registered_on
        or notices.url is distinct from excluded.url
        or notices.license_type is distinct from excluded.license_type
    ),
    is_visible = true,
    updated_at = now()
returning id
"""

INSERT_NOTICE_IF_ABSENT = """
insert into notices (
    category, source_board, dong_group, is_pinned, post_sn, title, department,
    registered_on, url, body_html, license_type
) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (category, source_board, post_sn) do nothing
returning id
"""


def _values(record: NoticeRecord) -> tuple[object, ...]:
    return (
        record.category,
        record.source_board,
        record.dong_group,
        record.is_pinned,
        record.post_sn,
        record.title,
        record.department,
        record.registered_on,
        record.url,
        record.body_html,
        record.license_type,
    )


def insert_notice_if_absent(conn: Connection, record: NoticeRecord) -> int | None:
    """Return a new notice ID, or None when the source key already exists."""
    with conn.cursor() as cursor:
        cursor.execute(INSERT_NOTICE_IF_ABSENT, _values(record))
        row = cursor.fetchone()
    return row[0] if row is not None else None


def save_notice(conn: Connection, record: NoticeRecord) -> int:
    """Upsert a notice and return its DB ID; never commit or close the connection."""
    with conn.cursor() as cursor:
        cursor.execute(UPSERT_NOTICE, _values(record))
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("공지 저장 후 ID를 반환받지 못했습니다.")
    return row[0]
