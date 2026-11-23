import asyncio
from collections.abc import Awaitable, Callable

import sentry_sdk

from loggers import get_logger
from src.core.auth.errors import RefreshTokenReuseError
from src.core.errors.scrubbed import scrubbed

logger = get_logger(__name__)

# What a realm does about a detected reuse, given the caller's address. It runs
# after the session has ended, and must bring its own database session: the
# rate limiter that usually detects the reuse has none.
RefreshReuseCallback = Callable[[RefreshTokenReuseError, str | None], Awaitable[None]]

# The callback runs inside the request it belongs to, which must get its 401
# even while the database hangs.
REUSE_CALLBACK_TIMEOUT_SECONDS = 5


async def report_refresh_reuse(
    callback: RefreshReuseCallback | None,
    error: RefreshTokenReuseError,
    ip: str | None,
) -> None:
    """Run the realm's callback, if it has one. Never raises: the caller goes on
    to answer 401, and a failure to record must not turn that into a 500."""
    if callback is None:
        return
    try:
        async with asyncio.timeout(REUSE_CALLBACK_TIMEOUT_SECONDS):
            await callback(error, ip)
    except Exception as exc:
        # A driver error quotes the row it failed on.
        failure = scrubbed(
            f"Refresh token reuse was not recorded: {type(exc).__name__}", exc
        )
        logger.error("Refresh token reuse was not recorded", exc_info=failure)
        sentry_sdk.capture_exception(failure)
