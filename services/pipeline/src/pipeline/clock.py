"""The single source of the current time for summary runs.

Preparation's reference_datetime and the stored generated_at both come from now(),
so a run is judged and recorded against one clock. Replacing this one function
(for example in e2e cases) fixes the time for the whole run.
"""

from datetime import UTC, datetime


def now() -> datetime:
    """Return the current timezone-aware time in UTC."""
    return datetime.now(UTC)
