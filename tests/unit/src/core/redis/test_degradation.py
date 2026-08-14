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
    reporter = RedisDegradationReporter("Cache", clock=_clock([0, 5_000, 6_000]))

    reporter.report_degraded(RuntimeError("boom"))
    reporter.report_recovered()
    reporter.report_recovered()

    assert capture.call_count == 2
    assert "5000ms" in capture.call_args.args[0]


def test_recovery_without_degradation_is_silent(capture: MagicMock) -> None:
    reporter = RedisDegradationReporter("Cache", clock=_clock([0]))

    reporter.report_recovered()

    assert capture.call_count == 0
