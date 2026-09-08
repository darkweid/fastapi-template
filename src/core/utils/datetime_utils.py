from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def get_utc_now() -> datetime:
    """The application clock: every expiry, cache stamp and comparison this
    code computes starts here, offset-aware in UTC, so no naive value ever
    reaches one. It is not the only clock in the system - TimestampMixin leaves
    created_at/updated_at to `func.now()`, so those columns carry the database's
    time and patching this function does not move them."""
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
