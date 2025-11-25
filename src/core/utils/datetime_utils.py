from datetime import date, datetime, time as datetime_time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytz

from loggers import get_logger
from src.core.errors.exceptions import InstanceProcessingException
from src.main.config import config

logger = get_logger(__name__)

LOCAL_TZ = pytz.timezone(str(config.app.LOCAL_TIMEZONE))


def get_utc_now() -> datetime:
    """
    Get the current date and time in UTC.

    This function returns the current time with timezone information set to UTC,
    ensuring that the returned datetime object is offset-aware.

    Returns:
        datetime: The current date and time in UTC with tzinfo set to ZoneInfo("UTC").
    """
    return datetime.now(ZoneInfo("UTC"))


def ensure_datetime(d: datetime | date) -> datetime:
    if isinstance(d, datetime):
        return d
    return datetime.combine(d, datetime.min.time())


def parse_date_range(
    from_date: str | date | datetime | None,
    to_date: str | date | datetime | None,
) -> tuple[datetime | None, datetime | None]:
    """If only `to_date` is provided, both `from_date` and `to_date` will be set to the start and end of that day.
    If both `from_date` and `to_date` are provided, they will be converted to datetime objects
    representing the start and end of their respective days.

    :param from_date: Start date string (format YYYY-MM-DD) or None
    :param to_date: End date string (format YYYY-MM-DD) or None
    :return: Tuple (from_date, to_date) with datetime objects or (None, None) if both are None
    """

    def to_utc(input_date: str | date | datetime, is_end: bool = False) -> datetime:
        """Convert local time to UTC"""
        if isinstance(input_date, str):
            _date = list(map(int, input_date.split("-")))
            time_part = datetime_time.max if is_end else datetime_time.min
            local_dt = LOCAL_TZ.localize(
                datetime.combine(date(_date[0], _date[1], _date[2]), time_part)
            )
        elif isinstance(input_date, datetime):
            local_dt = input_date.astimezone(LOCAL_TZ)

        elif isinstance(input_date, date):
            local_dt = LOCAL_TZ.localize(
                datetime.combine(
                    input_date, datetime_time.max if is_end else datetime_time.min
                )
            )

        return local_dt.astimezone(pytz.utc)  # convert to UTC

    result_from_date: datetime | None = None
    result_to_date: datetime | None = None

    if to_date and not from_date:
        result_from_date = to_utc(to_date, is_end=False)  # Local 00:00 → UTC
        result_to_date = to_utc(to_date, is_end=True)  # Local 23:59:59 → UTC

    elif from_date and to_date:
        result_from_date = to_utc(from_date, is_end=False)
        result_to_date = to_utc(to_date, is_end=True)

    return result_from_date, result_to_date


def prepare_date_interval(
    target_date: date | datetime, tz: str
) -> tuple[datetime, datetime]:
    """
    Returns a tuple (start_datetime, end_datetime) in the specified time zone.
    Accepts either a date or datetime.

    If a datetime is passed, its date component is extracted.
    """
    if isinstance(target_date, datetime):
        target_date = target_date.date()

    start_date = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        0,
        0,
        0,
        tzinfo=ZoneInfo(tz),
    )
    end_date = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        23,
        59,
        59,
        999999,
        tzinfo=ZoneInfo(tz),
    )

    return start_date, end_date


def prepare_local_interval(
    start_time: datetime | date, end_time: datetime | date, tz: str
) -> tuple[datetime, datetime]:
    """
    Returns a tuple (start_date, end_date) in the specified time zone (tz_name).
    """

    start_time = ensure_datetime(start_time)
    end_time = ensure_datetime(end_time)

    start_date = datetime(
        start_time.year, start_time.month, start_time.day, tzinfo=ZoneInfo(tz)
    )
    end_date = datetime(
        end_time.year, end_time.month, end_time.day, tzinfo=ZoneInfo(tz)
    ).replace(hour=23, minute=59, second=59, microsecond=999999)

    return start_date, end_date


def prepare_datetime_filter_range(
    from_date: datetime | date | None, to_date: datetime | date | None, tz: str
) -> tuple[datetime | None, datetime | None]:
    """
    Prepares a date/time filter range adjusted to the specified timezone.

    This function is used to construct a filtering interval for querying time-based
    data in databases that store timestamps in UTC. The returned datetimes are
    localized to the target timezone and cover either a single day or a custom
    date range, depending on the input.

    Args:
        from_date (datetime | date | None): The starting point of the filter range.
            Can be a datetime, date, or None.
        to_date (datetime | date | None): The end point of the filter range.
            Can be a datetime, date, or None.
        tz (str): The target timezone name (e.g., "America/Chicago").

    Returns:
        tuple[datetime | None, datetime | None]: A tuple of (start_time, end_time)
        in the specified timezone:
            - If both `from_date` and `to_date` are provided, returns a range starting
              from the beginning of the `from_date`'s day to the end of `to_date`'s day.
            - If only `from_date` is provided, returns a range covering the entire day.
            - If both are None, returns (None, None).
    """

    if from_date and not to_date:
        start_time, end_time = prepare_date_interval(ensure_datetime(from_date), tz)
    elif from_date and to_date:
        start_time, end_time = prepare_local_interval(
            ensure_datetime(from_date), ensure_datetime(to_date), tz
        )
    else:
        start_time, end_time = None, None

    return start_time, end_time


def guard_not_future_local_date(
    tz_str: str,
    target_date: date | datetime,
) -> None:
    """
    Validates that the given target date is not in the future
    relative to the current date in the specified IANA timezone.

    - If target_date is a datetime:
        * Ensures it is timezone-aware in UTC.
        * Converts to the driver's local date using the given timezone.
    - If target_date is a date:
        * Treated as already being in the driver's local date.

    :param tz_str: IANA timezone string (e.g., "America/Chicago").
    :param target_date: The date or datetime to validate.
    :raises ValueError: If the timezone string is invalid or the target_date type is unsupported.
    :raises InstanceProcessingException: If target_date is after the current local date in the given timezone.
    """

    def _ensure_aware_utc(dt: datetime) -> datetime:
        # Make datetime timezone-aware in UTC
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    try:
        tz: ZoneInfo = ZoneInfo(tz_str)
    except ZoneInfoNotFoundError:
        raise ValueError(f"Unknown IANA timezone: {tz_str!r}")

    now_local_date = get_utc_now().astimezone(tz).date()

    if isinstance(target_date, datetime):
        target_local_date = _ensure_aware_utc(target_date).astimezone(tz).date()
    elif isinstance(target_date, date):
        target_local_date = target_date

    if target_local_date > now_local_date:
        raise InstanceProcessingException("Cannot fetch information for future dates")
