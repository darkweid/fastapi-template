from collections.abc import Callable
from threading import Lock
import time

import sentry_sdk

DEFAULT_COOLDOWN_MS = 5 * 60_000


def _monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


class RedisDegradationReporter:
    """
    Report Redis degradation to Sentry with a cooldown and a paired recovery notice.

    Degraded infrastructure that does not raise is the one case where a manual
    capture_message is allowed; the cooldown keeps a flapping Redis from
    flooding Sentry, and the recovery message closes the incident with its
    duration.
    """

    def __init__(
        self,
        component: str,
        *,
        cooldown_ms: int = DEFAULT_COOLDOWN_MS,
        clock: Callable[[], int] = _monotonic_ms,
    ) -> None:
        self._component = component
        self._cooldown_ms = cooldown_ms
        self._clock = clock
        self._lock = Lock()
        self._degraded_since_ms: int | None = None
        self._last_report_ms: int | None = None

    def report_degraded(self, error: Exception) -> None:
        now_ms = self._clock()
        with self._lock:
            if self._degraded_since_ms is None:
                self._degraded_since_ms = now_ms
            elif (
                self._last_report_ms is not None
                and now_ms - self._last_report_ms < self._cooldown_ms
            ):
                return
            self._last_report_ms = now_ms

        sentry_sdk.capture_message(
            f"[{self._component}] Redis is unavailable. "
            f"Error: {type(error).__name__}: {error}",
            level="error",
        )

    def report_recovered(self) -> None:
        now_ms = self._clock()
        with self._lock:
            degraded_since_ms = self._degraded_since_ms
            if degraded_since_ms is None:
                return
            self._degraded_since_ms = None
            self._last_report_ms = None

        sentry_sdk.capture_message(
            f"[{self._component}] Redis recovered. "
            f"Downtime: {now_ms - degraded_since_ms}ms.",
            level="info",
        )
