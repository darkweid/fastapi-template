from uuid import UUID

from src.core.auth.errors import RefreshTokenReuseError
from src.core.database.session import async_session
from src.core.database.uow import ApplicationUnitOfWork
from src.event_log.actor import Actor
from src.user.events import UserRefreshReuseDetected


async def record_refresh_reuse(error: RefreshTokenReuseError, ip: str | None) -> None:
    """
    The user realm's `on_refresh_reuse`.

    Runs on a session and unit of work of its own: the reuse is usually found
    by the rate limiter, which has neither, and the request it belongs to is
    about to fail.

    Side effects:
    - Appends `user.refresh_reuse_detected` to the event log and commits.
    """
    async with async_session() as session:
        async with ApplicationUnitOfWork(session) as uow:
            # Anonymous: the token proves the account, not which of its two
            # holders presented it.
            await uow.publish(
                Actor.anonymous(ip=ip),
                UserRefreshReuseDetected(
                    object_id=UUID(error.subject_id), session_id=error.session_id
                ),
            )
            await uow.commit()
