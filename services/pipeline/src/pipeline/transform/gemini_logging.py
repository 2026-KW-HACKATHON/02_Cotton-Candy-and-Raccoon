"""Prevent SDK diagnostics from logging private request/response contents."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock, get_ident

_LOGGER_PREFIXES = ("google.genai", "google_genai", "httpx", "httpcore")
_request_depths: dict[int, int] = {}
_dispatch_lock = RLock()
_original_dispatch = logging.Logger.callHandlers


def _private_dispatch(logger: logging.Logger, record: logging.LogRecord) -> None:
    """Gate SDK records before any handler, including handlers created mid-request."""
    private_logger = any(
        record.name == prefix or record.name.startswith(prefix + ".")
        for prefix in _LOGGER_PREFIXES
    )
    with _dispatch_lock:
        target = _original_dispatch
        suppressed = private_logger and record.thread in _request_depths
    if not suppressed:
        target(logger, record)


@contextmanager
def private_gemini_logging() -> Iterator[None]:
    """Mute SDK/HTTP diagnostics only on the thread making this synchronous call.

    In particular, GOOGLE_GENAI_DEBUG logs full headers and document/image data.
    Our caller reports sanitized exception reason codes instead of those records.
    """
    global _original_dispatch
    request_thread = get_ident()
    with _dispatch_lock:
        if not _request_depths:
            _original_dispatch = logging.Logger.callHandlers
            logging.Logger.callHandlers = _private_dispatch
        _request_depths[request_thread] = _request_depths.get(request_thread, 0) + 1
    try:
        yield
    finally:
        with _dispatch_lock:
            depth = _request_depths[request_thread] - 1
            if depth:
                _request_depths[request_thread] = depth
            else:
                del _request_depths[request_thread]
            if not _request_depths and logging.Logger.callHandlers is _private_dispatch:
                logging.Logger.callHandlers = _original_dispatch
