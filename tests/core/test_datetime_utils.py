from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
import pytz

from src.core.errors.exceptions import InstanceProcessingException
from src.core.utils import datetime_utils


@pytest.fixture(autouse=True)
def _patch_local_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(datetime_utils, "LOCAL_TZ", pytz.UTC)


def test_parse_date_range_single_day() -> None:
    start, end = datetime_utils.parse_date_range(None, "2024-01-15")

    assert start == datetime(2024, 1, 15, tzinfo=pytz.UTC)
    assert end == datetime(2024, 1, 15, 23, 59, 59, 999999, tzinfo=pytz.UTC)


def test_parse_date_range_from_and_to_dates() -> None:
    start, end = datetime_utils.parse_date_range(date(2024, 5, 1), date(2024, 5, 3))

    assert start == datetime(2024, 5, 1, tzinfo=pytz.UTC)
    assert end == datetime(2024, 5, 3, 23, 59, 59, 999999, tzinfo=pytz.UTC)


def test_prepare_datetime_filter_range_single_day() -> None:
    start, end = datetime_utils.prepare_datetime_filter_range(
        date(2024, 2, 10), None, "UTC"
    )

    assert start == datetime(2024, 2, 10, tzinfo=ZoneInfo("UTC"))
    assert end == datetime(2024, 2, 10, 23, 59, 59, 999999, tzinfo=ZoneInfo("UTC"))


def test_prepare_datetime_filter_range_interval() -> None:
    start, end = datetime_utils.prepare_datetime_filter_range(
        date(2024, 3, 1), date(2024, 3, 2), "UTC"
    )

    assert start == datetime(2024, 3, 1, tzinfo=ZoneInfo("UTC"))
    assert end == datetime(2024, 3, 2, 23, 59, 59, 999999, tzinfo=ZoneInfo("UTC"))


def test_guard_not_future_local_date_allows_today(monkeypatch: pytest.MonkeyPatch):
    now = datetime(2024, 4, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(datetime_utils, "get_utc_now", lambda: now)

    datetime_utils.guard_not_future_local_date("UTC", date(2024, 4, 20))


def test_guard_not_future_local_date_raises_on_future_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2024, 4, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(datetime_utils, "get_utc_now", lambda: now)

    with pytest.raises(InstanceProcessingException):
        datetime_utils.guard_not_future_local_date("UTC", date(2024, 4, 21))
