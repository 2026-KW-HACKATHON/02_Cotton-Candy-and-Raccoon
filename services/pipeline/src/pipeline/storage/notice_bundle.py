"""Atomically store a fully collected notice and its file references."""

from collections.abc import Sequence

from psycopg import Connection

from pipeline.models import FileRecord, NoticeRecord
from pipeline.storage.notices import insert_notice_if_absent, save_notice

StoredFile = tuple[str | None, str | None, str | None, str]


def _new_files(
    notice: NoticeRecord,
    files: Sequence[FileRecord],
) -> dict[tuple[str, str], FileRecord]:
    result: dict[tuple[str, str], FileRecord] = {}
    for file in files:
        if (file.category, file.source_board, file.post_sn) != (
            notice.category,
            notice.source_board,
            notice.post_sn,
        ):
            raise ValueError("파일의 출처·게시물 번호가 공지와 일치하지 않습니다.")
        key = (file.file_key, file.kind)
        previous = result.get(key)
        if previous is not None and previous != file:
            raise ValueError("같은 file_key와 kind의 파일 정보가 충돌합니다.")
        result[key] = file
    return result


def _stored_files(conn: Connection, notice_id: int) -> dict[tuple[str, str], StoredFile]:
    with conn.cursor() as cursor:
        cursor.execute(
            "select file_key, kind, file_sn, file_id, file_name, url "
            "from notice_files where notice_id = %s",
            (notice_id,),
        )
        return {
            (file_key, kind): (file_sn, file_id, file_name, url)
            for file_key, kind, file_sn, file_id, file_name, url in cursor.fetchall()
        }


def _insert_file(conn: Connection, notice_id: int, file: FileRecord) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "insert into notice_files "
            "(notice_id, kind, file_sn, file_id, file_key, file_name, url) "
            "values (%s, %s, %s, %s, %s, %s, %s)",
            (
                notice_id,
                file.kind,
                file.file_sn,
                file.file_id,
                file.file_key,
                file.file_name,
                file.url,
            ),
        )


def save_notice_with_files(
    conn: Connection,
    notice: NoticeRecord,
    files: Sequence[FileRecord],
) -> int:
    """Save a complete notice and file list; a failure rolls back this unit.

    An empty list means a successfully collected notice with no files. Callers
    must not pass a partial list after a failed API or page request.
    """
    if files is None:
        raise ValueError("파일 목록 수집이 완료되지 않았습니다.")
    incoming = _new_files(notice, files)
    incoming_values = {
        key: (file.file_sn, file.file_id, file.file_name, file.url)
        for key, file in incoming.items()
    }

    with conn.transaction():
        new_id = insert_notice_if_absent(conn, notice)
        is_new = new_id is not None
        notice_id = new_id if is_new else save_notice(conn, notice)
        existing_values = _stored_files(conn, notice_id)
        if existing_values != incoming_values:
            with conn.cursor() as cursor:
                cursor.execute("delete from notice_files where notice_id = %s", (notice_id,))
            for key in sorted(incoming):
                _insert_file(conn, notice_id, incoming[key])
            if not is_new:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "update notices set is_modified = true where id = %s",
                        (notice_id,),
                    )
    return notice_id
