"""Helpers shared from test_summary_bundle_manifest.py."""

from pipeline.storage.summary_metadata import (
    build_summary_metadata_from_manifest,
)
from pipeline.transform.gemini_client import DEFAULT_MODEL

__all__ = [
    "_metadata",
]


def _metadata(prepared):
    return build_summary_metadata_from_manifest(
        notice=prepared.notice, file_manifest=prepared.file_manifest, model=DEFAULT_MODEL,
    )
