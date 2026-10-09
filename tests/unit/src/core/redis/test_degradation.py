from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from src.core.redis.degradation import RedisDegradationReporter


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock()
    monkeypatch.setattr("src.core.redis.degradation.sentry_sdk.capture_message", mock)
    return mock


def _clock(values: list[int]) -> Callable[[], int]:
    return lambda: values.pop(0)


def test_first_degradation_is_reported(capture: MagicMock) -> None:
    reporter = RedisDegradationReporter("Cache", clock=_clock([0]))

    reporter.report_degraded(RuntimeError("boom"))

    assert capture.call_count == 1
    assert "[Cache]" in capture.call_args.args[0]


def test_repeated_degradation_within_cooldown_is_silent(capture: MagicMock) -> None:
    reporter = RedisDegradationReporter("Cache", clock=_clock([0, 1_000, 299_000]))

    reporter.report_degraded(RuntimeError("boom"))
    reporter.report_degraded(RuntimeError("boom"))
    reporter.report_degraded(RuntimeError("boom"))

    assert capture.call_count == 1


def test_degradation_is_reported_again_after_cooldown(capture: MagicMock) -> None:
    reporter = RedisDegradationReporter("Cache", clock=_clock([0, 300_001]))

    reporter.report_degraded(RuntimeError("boom"))
    reporter.report_degraded(RuntimeError("boom"))

    assert capture.call_count == 2


def test_recovery_reports_downtime_once(capture: MagicMock) -> None:
    reporter = RedisDegradationReporter(
        "Cache", clock=_clock([0, 5_000, 65_000, 66_000])
    )

    reporter.report_degraded(RuntimeError("boom"))
    reporter.report_degraded(RuntimeError("boom"))
    reporter.report_recovered()
    reporter.report_recovered()

    assert capture.call_count == 2
    message = capture.call_args.args[0]
    assert "Redis recovered after 2 failure(s)" in message
    assert "the first 65000ms ago, the last 60000ms ago" in message


def test_recovery_resets_the_cooldown(capture: MagicMock) -> None:
    # A new incident right after a recovery is a new outage, not a repeat the
    # cooldown should mute.
    reporter = RedisDegradationReporter("Cache", clock=_clock([0, 60_000, 61_000]))

    reporter.report_degraded(RuntimeError("boom"))
    reporter.report_recovered()
    reporter.report_degraded(RuntimeError("boom"))

    assert capture.call_count == 3
    assert "Redis is degraded" in capture.call_args.args[0]


def test_success_within_the_quiet_window_keeps_the_incident_open(
    capture: MagicMock,
) -> None:
    reporter = RedisDegradationReporter(
        "Cache", clock=_clock([0, 59_999, 60_000, 119_999])
    )

    reporter.report_degraded(RuntimeError("boom"))
    reporter.report_recovered()
    reporter.report_degraded(RuntimeError("boom"))
    reporter.report_recovered()

    assert capture.call_count == 1
    assert "Redis is degraded" in capture.call_args.args[0]


def test_a_redis_flapping_at_maxmemory_is_one_incident(capture: MagicMock) -> None:
    # At the edge of maxmemory one write is refused and the next accepted. Were
    # every accepted write a recovery, it would reset the cooldown and each
    # refusal after it would be reported at once: a pair per flap.
    ticks = [step * 20_000 for step in range(40)]
    reporter = RedisDegradationReporter("Cache", clock=_clock(ticks))

    for _ in range(20):
        reporter.report_degraded(RuntimeError("OOM"))
        reporter.report_recovered()

    messages = [call.args[0] for call in capture.call_args_list]
    assert not any("recovered" in message for message in messages)
    # 780s of flapping: the opening report plus one per elapsed cooldown.
    assert len(messages) == 3


def test_recovery_without_degradation_is_silent(capture: MagicMock) -> None:
    reporter = RedisDegradationReporter("Cache", clock=_clock([0]))

    reporter.report_recovered()

    assert capture.call_count == 0


def test_degradation_report_names_the_cause(capture: MagicMock) -> None:
    # A full Redis is reachable; a report saying "unavailable" would send the
    # on-call engineer after the network instead of maxmemory.
    reporter = RedisDegradationReporter("Cache", clock=_clock([0]))

    reporter.report_degraded(RuntimeError("OOM command not allowed"))

    message = capture.call_args.args[0]
    assert "unavailable" not in message
    assert "RuntimeError: OOM command not allowed" in message
