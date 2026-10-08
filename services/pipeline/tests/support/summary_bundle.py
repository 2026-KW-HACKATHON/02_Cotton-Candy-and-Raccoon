"""Helpers shared from test_summary_bundle.py."""

from datetime import UTC, date, datetime

from pipeline.storage.summary_source import StoredFile, SummarySource

__all__ = [
    "NOW",
    "PNG",
    "URL",
    "file",
    "source",
]


URL = "https://www.nowon.kr/component/file/ND_fileDownload.do?q_fileSn=1&q_fileId="


PNG = b"\x89PNG\r\n\x1a\nimage"


NOW = datetime(2026, 10, 1, tzinfo=UTC)


def source(*files: StoredFile, html: str | None = "<p>행사 안내</p>") -> SummarySource:
    return SummarySource(
        7,
        "행사",
        "nowon",
        "1001",
        "00123",
        "행정과",
        date(2026, 10, 1),
        "https://www.nowon.kr/www/notice",
        html,
        files,
        4,
    )


def file(number: int, name: str | None, kind: str = "attachment") -> StoredFile:
    return StoredFile(number, kind, "id:" + str(number), str(number), "1", name, URL + str(number))
