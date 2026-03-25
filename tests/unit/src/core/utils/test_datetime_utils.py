from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
import pytz

from src.core.errors.exceptions import InstanceProcessingException
from src.core.utils import datetime_utils

NOW_2024_04_20 = datetime(2024, 4, 20, 12, 0, tzinfo=timezone.utc)
NOW_2024_01_10 = datetime(2024, 1, 10, 8, 0, tzinfo=timezone.utc)


def fixed_now_apr_20() -> datetime:
    return NOW_2024_04_20


def fixed_now_jan_10() -> datetime:
    return NOW_2024_01_10


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
    monkeypatch.setattr(datetime_utils, "get_utc_now", fixed_now_apr_20)

    datetime_utils.guard_not_future_local_date("UTC", date(2024, 4, 20))


def test_guard_not_future_local_date_raises_on_future_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(datetime_utils, "get_utc_now", fixed_now_apr_20)

    with pytest.raises(InstanceProcessingException):
        datetime_utils.guard_not_future_local_date("UTC", date(2024, 4, 21))


def test_parse_date_range_none_returns_none() -> None:
    start, end = datetime_utils.parse_date_range(None, None)

    assert start is None
    assert end is None


def test_parse_date_range_datetime_inputs_preserve_time() -> None:
    to_date = datetime(2024, 1, 5, 10, 30, tzinfo=pytz.UTC)
    start, end = datetime_utils.parse_date_range(None, to_date)

    assert start == to_date
    assert end == to_date


def test_prepare_date_interval_accepts_datetime() -> None:
    start, end = datetime_utils.prepare_date_interval(
        datetime(2024, 6, 1, 15, 30, tzinfo=ZoneInfo("UTC")), "UTC"
    )

    assert start == datetime(2024, 6, 1, tzinfo=ZoneInfo("UTC"))
    assert end == datetime(2024, 6, 1, 23, 59, 59, 999999, tzinfo=ZoneInfo("UTC"))


def test_prepare_local_interval_builds_day_bounds() -> None:
    start, end = datetime_utils.prepare_local_interval(
        date(2024, 7, 1), date(2024, 7, 3), "UTC"
    )

    assert start == datetime(2024, 7, 1, tzinfo=ZoneInfo("UTC"))
    assert end == datetime(2024, 7, 3, 23, 59, 59, 999999, tzinfo=ZoneInfo("UTC"))


def test_guard_not_future_local_date_raises_on_invalid_timezone() -> None:
    with pytest.raises(ValueError):
        datetime_utils.guard_not_future_local_date("Invalid/Zone", date(2024, 1, 1))


def test_guard_not_future_local_date_allows_naive_datetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(datetime_utils, "get_utc_now", fixed_now_jan_10)

    datetime_utils.guard_not_future_local_date("UTC", datetime(2024, 1, 9, 10, 0))
