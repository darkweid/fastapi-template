from typing import Annotated
from uuid import UUID

from fastapi import Depends
from redis.asyncio import Redis

from src.core.auth.credentials import SessionIdentity
from src.core.auth.usecases.logout import LogoutUseCase
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.redis.dependencies import get_redis_client
from src.core.schemas import SuccessResponse
from src.event_log.actor import Actor
from src.user.auth.realm import USER_AUTH_REALM
from src.user.events import UserSignedOut


class UserLogoutUseCase:
    """
    End the caller's session, or all of them, and record it in the event log.

    The realm-agnostic `LogoutUseCase` stays free of any module's event
    catalog, so the realm wraps it here instead: the sessions are invalidated
    first, because that is the part a caller is owed even if the audit row
    cannot be written.

    The identity comes from a token this realm signed, so the row is attributed
    to its subject; logout accepts an expired access token, which proves the
    same thing a live one does about who issued it.
    """

    def __init__(self, uow: ApplicationUnitOfWork, logout: LogoutUseCase) -> None:
        self.uow = uow
        self.logout = logout

    async def execute(
        self,
        *,
        identity: SessionIdentity,
        terminate_all_sessions: bool = False,
        ip: str | None = None,
    ) -> SuccessResponse:
        result = await self.logout.execute(
            subject_id=identity.subject_id,
            session_id=identity.session_id,
            terminate_all_sessions=terminate_all_sessions,
        )
        subject_id = UUID(identity.subject_id)
        async with self.uow as uow:
            await uow.event_logs.record(
                uow.session,
                Actor.user(subject_id, ip=ip),
                UserSignedOut(
                    object_id=subject_id, all_sessions=terminate_all_sessions
                ),
            )
            await uow.commit()
        return result


def get_logout_use_case(
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
    redis_client: Annotated[Redis, Depends(get_redis_client)],
) -> UserLogoutUseCase:
    return UserLogoutUseCase(
        uow=uow, logout=LogoutUseCase(redis_client, realm=USER_AUTH_REALM)
    )
