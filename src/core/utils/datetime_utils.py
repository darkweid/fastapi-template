from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def get_utc_now() -> datetime:
    """
    Get the current date and time in UTC.

    This function returns the current time with timezone information set to UTC,
    ensuring that the returned datetime object is offset-aware.

    Returns:
        datetime: The current date and time in UTC with tzinfo set to ZoneInfo("UTC").
    """
    return datetime.now(ZoneInfo("UTC"))


def ensure_aware_utc(dt: datetime) -> datetime:
    """Return `dt` as an offset-aware UTC datetime.

    A naive value is read as UTC rather than rejected: every timestamp column
    in this project is `DateTime(timezone=True)`, so a naive bound has no other
    sensible meaning, and mixing naive and aware values raises `TypeError` on
    the first comparison between them.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
