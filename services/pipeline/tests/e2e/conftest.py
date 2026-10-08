"""Fixtures for e2e cases: one freshly migrated database per case."""

from collections.abc import Iterator

import pytest
from support.db import owned_migrated_database


@pytest.fixture
def e2e_database() -> Iterator[dict[str, str]]:
    """A new migrated database for one case; missing configuration fails, never skips."""
    with owned_migrated_database(
        env="E2E_TEST_DATABASE_URL",
        required_prefix="pipeline_e2e_test_",
        missing="fail",
        missing_message=(
            "E2E_TEST_DATABASE_URL 미설정: e2e는 로컬 테스트 PostgreSQL이 필요합니다. "
            "예: postgresql://postgres:postgres@127.0.0.1:5432/pipeline_e2e_test_local"
        ),
        invalid_message="e2e는 pipeline_e2e_test_로 시작하는 로컬 테스트 DB에서만 실행합니다.",
    ) as info:
        yield info
