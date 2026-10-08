"""Stable paths for tests, independent of where a test file lives under tests/."""

from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[1]
FIXTURES_DIR = TESTS_DIR / "fixtures"
PIPELINE_DIR = TESTS_DIR.parent
REPO_ROOT = PIPELINE_DIR.parents[1]
