import pytz
LOCAL_TZ = pytz.timezone(str(settings.tz))


def parse_date_range(
        from_date: Optional[Union[str, date, datetime]],
        to_date: Optional[Union[str, date, datetime]]
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """If only `to_date` is provided, both `from_date` and `to_date` will be set to the start and end of that day.
    If both `from_date` and `to_date` are provided, they will be converted to datetime objects
    representing the start and end of their respective days.

    :param from_date: Start date string (format YYYY-MM-DD) or None
    :param to_date: End date string (format YYYY-MM-DD) or None
    :return: Tuple (from_date, to_date) with datetime objects or (None, None) if both are None
    """

    def to_utc(input_date: Union[str, date, datetime], is_end: bool = False) -> datetime:
        """Convert local time to UTC"""
        if isinstance(input_date, str):
            _date = list(map(int, input_date.split("-")))
            time_part = time.max if is_end else time.min
            local_dt = LOCAL_TZ.localize(datetime.combine(date(_date[0], _date[1], _date[2]), time_part))
        elif isinstance(input_date, date):
            local_dt = LOCAL_TZ.localize(datetime.combine(input_date, time.max if is_end else time.min))
        elif isinstance(input_date, datetime):
            local_dt = input_date.astimezone(LOCAL_TZ)
        else:
            return None
        return local_dt.astimezone(pytz.utc)  # convert to UTC

    if to_date and not from_date:
        from_date = to_utc(to_date, is_end=False)  # Local 00:00 → UTC
        to_date = to_utc(to_date, is_end=True)  # Local 23:59:59 → UTC

    elif from_date and to_date:
        from_date = to_utc(from_date, is_end=False)
        to_date = to_utc(to_date, is_end=True)

    return from_date, to_date


def get_utc_now() -> datetime:
    """
    Get the current date and time in UTC.

    This function returns the current time with timezone information set to UTC,
    ensuring that the returned datetime object is offset-aware.

    Returns:
        datetime: The current date and time in UTC with tzinfo set to ZoneInfo("UTC").
    """
    return datetime.now(ZoneInfo("UTC"))
