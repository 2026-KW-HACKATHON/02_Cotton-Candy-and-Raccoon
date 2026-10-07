"""Load exact collected notice text for the Gemini conversion pipeline."""

from psycopg import Connection
from psycopg.rows import tuple_row

from pipeline.glossary.source import NoticeGlossaryInput, notice_content_revision
from pipeline.transform.html_text import html_to_notice_text


def load_notice_glossary_input(conn: Connection, notice_id: int) -> NoticeGlossaryInput:
    """Read the saved title/body without updating the collected original HTML.

    Positions refer to the returned title + newline + plain body, not HTML bytes.
    Already extracted attachment text can instead be passed directly in an input.
    """
    if isinstance(notice_id, bool) or not isinstance(notice_id, int) or notice_id < 1:
        raise ValueError("공지 ID는 양의 정수여야 합니다.")
    with conn.cursor(row_factory=tuple_row) as cursor:
        cursor.execute("select title, body_html from public.notices where id = %s", (notice_id,))
        row = cursor.fetchone()
    if row is None:
        raise ValueError("지정한 공지를 찾을 수 없습니다.")
    title, body_html = row
    body = html_to_notice_text(body_html)
    text = title + ("\n" + body if body else "")
    return NoticeGlossaryInput(
        notice_id=notice_id,
        text=text,
        notice_revision=notice_content_revision(title, body_html),
    )
