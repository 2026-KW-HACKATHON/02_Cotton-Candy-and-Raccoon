"""Prevent SDK diagnostics from logging private request/response contents."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from threading import get_ident

_LOGGER_PREFIXES = ("google.genai", "google_genai", "httpx", "httpcore")
_LOGGER_NAMES = (
    *_LOGGER_PREFIXES,
    "google_genai._api_client",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
)


class _RequestLogFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self.request_thread = get_ident()

    def filter(self, record: logging.LogRecord) -> bool:
        # Filtering a record before formatting also drops exc_info and headers/body
        # arguments. Other threads' requests keep their own logging configuration.
        return record.thread != self.request_thread


@contextmanager
def private_gemini_logging() -> Iterator[None]:
    """Mute SDK/HTTP diagnostics only on the thread making this synchronous call.

    In particular, GOOGLE_GENAI_DEBUG logs full headers and document/image data.
    Our caller reports sanitized exception reason codes instead of those records.
    """
    names = set(_LOGGER_NAMES)
    names.update(
        name
        for name in tuple(logging.Logger.manager.loggerDict)
        if any(name == prefix or name.startswith(prefix + ".") for prefix in _LOGGER_PREFIXES)
    )
    log_filter = _RequestLogFilter()
    loggers = [logging.getLogger(name) for name in names]
    for logger in loggers:
        logger.addFilter(log_filter)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeFilter(log_filter)
