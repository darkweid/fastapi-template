from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE", "TRACE"})
RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """When a request is repeated.

    `retry_connect_errors` repeats exchanges that never reached the server,
    which is safe for any method. `retry_sent_errors` and `retry_statuses`
    repeat a request the server may already have acted on, so they apply to
    a non-idempotent method only with `allow_non_idempotent`, meant for an API
    that deduplicates by an idempotency key. A `Retry-After` longer than
    `max_retry_after_seconds` ends the retries and hands the answer back.
    No retry starts if it would end past `max_elapsed_seconds`.
    """

    max_attempts: int = 3
    backoff_base_seconds: float = 0.5
    backoff_max_seconds: float = 8
    max_elapsed_seconds: float = 30
    retry_connect_errors: bool = True
    retry_sent_errors: bool = False
    retry_statuses: frozenset[int] = frozenset()
    respect_retry_after: bool = True
    max_retry_after_seconds: float = 30
    allow_non_idempotent: bool = False

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if (
            min(
                self.backoff_base_seconds,
                self.backoff_max_seconds,
                self.max_elapsed_seconds,
                self.max_retry_after_seconds,
            )
            < 0
        ):
            raise ValueError("retry durations cannot be negative")
        if self.backoff_base_seconds > self.backoff_max_seconds:
            raise ValueError("backoff_base_seconds exceeds backoff_max_seconds")
        if any(not 100 <= status <= 599 for status in self.retry_statuses):
            raise ValueError("retry_statuses must be HTTP statuses")

    @property
    def repeats_sent_requests(self) -> bool:
        return self.retry_sent_errors or bool(self.retry_statuses)

    def ensure_allows(self, method: str) -> None:
        if (
            self.repeats_sent_requests
            and not self.allow_non_idempotent
            and method.upper() not in IDEMPOTENT_METHODS
        ):
            raise ValueError(
                f"{method.upper()} is not idempotent and this policy may send it "
                "twice; set allow_non_idempotent only for an API that "
                "deduplicates by an idempotency key"
            )

    def retries_error(self, *, request_sent: bool, transient: bool) -> bool:
        if not transient:
            return False
        return self.retry_sent_errors if request_sent else self.retry_connect_errors

    def backoff(self, attempt: int, rng: Callable[[], float]) -> float:
        """Full jitter: a random pause up to the exponential cap, so clients
        that failed together do not come back together."""
        cap = min(
            self.backoff_max_seconds, self.backoff_base_seconds * 2.0 ** (attempt - 1)
        )
        return rng() * cap


RETRY_CONNECT_ONLY = RetryPolicy()
RETRY_IDEMPOTENT = RetryPolicy(
    retry_sent_errors=True, retry_statuses=RETRYABLE_STATUSES
)


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    """Seconds to wait from delta-seconds or an HTTP-date; None when absent or
    unreadable, so the caller falls back to its backoff."""
    if value is None:
        return None
    value = value.strip()
    if value.isascii() and value.isdigit():
        return float(value)
    try:
        moment = parsedate_to_datetime(value)
    # A year past datetime's range overflows instead of failing to parse.
    except (TypeError, ValueError, OverflowError):
        return None
    if moment.tzinfo is None:
        return None
    return max(0.0, (moment - now).total_seconds())
