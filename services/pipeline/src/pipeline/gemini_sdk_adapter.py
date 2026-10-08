"""Small locked-SDK boundary: one HTTP attempt and safe provider failure metadata."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime

import httpx
from google.genai import errors

from pipeline.gemini_execution import GeminiExecutionError, record_http_dispatch


def record_request(_request: httpx.Request) -> None:
    """Count dispatch, without exposing request URLs, headers or bodies."""
    record_http_dispatch()


def reject_failed_response(response: httpx.Response) -> None:
    """Preserve retry instructions before the SDK waits for an error response body.

    HTTPX runs response hooks after headers and before reading the body. Raising
    here closes that response and allows the parent to honor Retry-After even when
    the provider never finishes its error body. Provider messages are never needed.
    """
    if response.status_code >= 400:
        error = httpx.HTTPStatusError(
            "Gemini API request failed.", request=response.request, response=response,
        )
        raise safe_api_failure(error) from None


def disable_interaction_retries(resource: object) -> None:
    """Disable the Interactions retry loop that ignores HttpRetryOptions(attempts=0).

    This isolated compatibility boundary uses the installed SDK resource's exposed
    configuration. Contract tests check actual POST counts, rather than assuming
    that the similarly named public options behave like Generate Content.
    """
    configuration = getattr(resource, "sdk_configuration", None)
    if configuration is None or not hasattr(configuration, "retry_config"):
        raise GeminiExecutionError("configuration_error")
    configuration.retry_config = None


def is_api_error(error: Exception) -> bool:
    if isinstance(error, (errors.APIError, httpx.HTTPError)):
        return True
    return any(
        base.__name__ in {"APIError", "GenAiError"}
        and (base.__module__ == "google.genai" or base.__module__.startswith("google.genai."))
        for base in type(error).__mro__
    )


def _api_status(error: Exception) -> int | None:
    values = [getattr(error, "status_code", None), getattr(error, "code", None)]
    response = getattr(error, "response", None)
    if isinstance(response, httpx.Response):
        values.append(response.status_code)
    return next((value for value in values if type(value) is int and 400 <= value <= 599), None)


def _transport_reason(error: BaseException) -> str:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.TimeoutException):
            return "api_timeout"
        if isinstance(current, httpx.HTTPError):
            return "api_connection_error"
        current = current.__cause__
    return "api_error"


def retry_at_from_headers(
    headers: httpx.Headers, *, now: datetime | None = None,
) -> datetime | None:
    """Keep the latest valid server delay; never shorten it to this job's budget."""
    now = now or datetime.now(UTC)
    dates: list[datetime] = []
    for name, divisor in (("retry-after-ms", 1000), ("retry-after", 1)):
        raw = headers.get(name)
        if raw is None or len(raw) > 128:
            continue
        try:
            seconds = Decimal(raw) / divisor
        except InvalidOperation:
            if name != "retry-after":
                continue
            try:
                parsed = parsedate_to_datetime(raw)
                if parsed.tzinfo is not None:
                    dates.append(max(now, parsed.astimezone(UTC)))
            except (TypeError, ValueError, OverflowError):
                pass
            continue
        if not seconds.is_finite() or seconds < 0:
            continue
        # Unrepresentably long delays must defer forever, not become an early retry.
        try:
            dates.append(now + timedelta(seconds=float(seconds)))
        except (OverflowError, ValueError):
            dates.append(datetime.max.replace(tzinfo=UTC))
    return max(dates) if dates else None


def safe_api_failure(error: Exception) -> GeminiExecutionError:
    """Classify only HTTP status/transport types, discarding all provider text."""
    status = _api_status(error)
    reason = "input_too_large" if status == 413 else (
        _transport_reason(error) if status is None else "api_error"
    )
    retryable = status in {408, 429, 500, 502, 503, 504} or (
        status is None and reason in {"api_timeout", "api_connection_error"}
    )
    response = getattr(error, "response", None)
    retry_at = retry_at_from_headers(response.headers) if (
        retryable and isinstance(response, httpx.Response)
    ) else None
    return GeminiExecutionError(
        reason,
        failure_kind="transient" if retryable else "permanent",
        retryable=retryable,
        retry_at=retry_at,
        status_code=status,
    )
