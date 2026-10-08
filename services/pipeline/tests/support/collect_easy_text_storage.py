"""Helpers shared from test_collect_easy_text_storage.py."""

from collections.abc import Iterator

import pytest
from psycopg.conninfo import make_conninfo

from pipeline.config import DatabaseSettings
from support.db import TEST_ROLES, owned_migrated_database

__all__ = [
    "_TEST_ROLES",
    "committed_easy_db",
]


_TEST_ROLES = TEST_ROLES


@pytest.fixture
def committed_easy_db() -> Iterator[DatabaseSettings]:
    """Create and remove an owned DB; never commit fixtures into the supplied DB.

    This module runs serially with the other local integration tests. Track roles
    created here because the suite's rollback fixtures expect no persistent roles.
    """
    with owned_migrated_database(
        env="GLOSSARY_TEST_DATABASE_URL",
        required_prefix="pipeline_glossary_test_",
        missing="skip",
        missing_message="GLOSSARY_TEST_DATABASE_URL 미설정: 수집 후 저장 통합 테스트 생략",
        invalid_message="자동 쉬운말 검증은 전용 로컬 테스트 DB에서만 가능합니다.",
    ) as info:
        yield DatabaseSettings(make_conninfo(**info))
