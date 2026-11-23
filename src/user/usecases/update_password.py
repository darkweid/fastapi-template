from functools import partial
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from redis.asyncio import Redis

from loggers import get_logger
from src.core.auth.challenges import ActiveChallengeRegistry
from src.core.auth.errors import InvalidCredentialsError
from src.core.auth.token_helpers import invalidate_all_sessions
from src.core.cache.interface import Cache
from src.core.cache.runtime import get_cache
from src.core.database.detached import detached_read
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.errors.exceptions import (
    InstanceNotFoundException,
    InstanceProcessingException,
)
from src.core.redis.dependencies import get_redis_client
from src.core.schemas import SuccessResponse
from src.core.utils.security import (
    hash_password,
    mask_email,
    password_still_matches,
    verify_password,
)
from src.event_log.actor import Actor
from src.user.auth.realm import RESET_PASSWORD_PURPOSE, USER_AUTH_REALM
from src.user.auth.schemas import UserNewPassword
from src.user.cache_keys import user_cache_keys
from src.user.events import UserPasswordChanged

logger = get_logger(__name__)


class UpdateUserPasswordUseCase:
    """
    Change the caller's own password and end every session it had.

    Self-service, so it always proves knowledge of the current password and
    refuses a new password equal to it. An administrative reset of someone
    else's password is a separate scenario and must not ask for the current one.

    Side effects:
    - Appends `user.password_changed` to the event log, in this transaction.
    - Deletes every session key of the user before the commit rather than
      after: with Redis unavailable the change then fails as a whole, instead
      of leaving a new password alongside sessions holding the old one.
    - Retires the reset link live at that moment the same way: whoever could
      read that mailbox must not undo the change with it. A reset mail still
      queued issues its link later, and a confirm already past its link check
      runs on; neither is stopped here.
    - Bumps the user:{id} cache namespace version twice, pre- and post-commit.
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork,
        redis_client: Redis,
        cache: Cache,
    ) -> None:
        self.uow = uow
        self.redis_client = redis_client
        self.cache = cache

    async def execute(
        self, data: UserNewPassword, user_id: UUID, actor: Actor
    ) -> SuccessResponse:
        async with detached_read(self.uow.session) as read:
            user = await self.uow.users.get_single(self.uow.session, id=user_id)
            read.detach(user)
        if not user:
            logger.info("[UpdateUserPassword] User not found.")
            raise InstanceNotFoundException("User not found.")

        if not await verify_password(data.current_password, user.password_hash):
            logger.debug(
                "[UpdateUserPassword] Wrong current password for %s.",
                mask_email(user.email),
            )
            raise InvalidCredentialsError("Current password is incorrect.")

        if data.password == data.current_password:
            # Not a no-op: going through with it would still sign every
            # session out, so a mistyped form would look like a hijack.
            raise InstanceProcessingException(
                "New password must differ from the current one."
            )

        new_password_hash = await hash_password(data.password)
        async with self.uow as uow:
            locked_user = await uow.users.get_single(
                uow.session, for_update=True, populate_existing=True, id=user_id
            )
            if not locked_user:
                logger.info("[UpdateUserPassword] User deleted during the change.")
                raise InstanceNotFoundException("User not found.")
            # A reset that committed since the check above must not be undone
            # by someone who only knows the password it replaced.
            if not await password_still_matches(
                data.current_password, user.password_hash, locked_user.password_hash
            ):
                logger.debug(
                    "[UpdateUserPassword] Password of %s changed concurrently.",
                    mask_email(locked_user.email),
                )
                raise InvalidCredentialsError("Current password is incorrect.")

            update_data = {"password_hash": new_password_hash}
            updated_user = await uow.users.update(uow.session, update_data, id=user_id)
            if not updated_user:
                raise InstanceNotFoundException("User not found.")
            await uow.flush()
            await invalidate_all_sessions(
                str(updated_user.id), self.redis_client, keys=USER_AUTH_REALM.keys
            )
            await ActiveChallengeRegistry(USER_AUTH_REALM).invalidate(
                RESET_PASSWORD_PURPOSE, locked_user.email, self.redis_client
            )
            await self.cache.invalidate(user_cache_keys.namespace(updated_user.id))
            uow.add_after_commit_hook(
                partial(
                    self.cache.invalidate, user_cache_keys.namespace(updated_user.id)
                )
            )
            # No payload: the only thing that changed is a secret, and an audit
            # row outlives the account it describes.
            await uow.publish(actor, UserPasswordChanged(object_id=user_id))
            await uow.commit()
            logger.debug(
                "[UpdateUserPassword] %s password updated successfully.",
                mask_email(updated_user.email),
            )
            logger.debug(
                "[UpdateUserPassword] All user %s sessions invalidated.",
                mask_email(updated_user.email),
            )
            return SuccessResponse(success=True)


def get_update_user_password_use_case(
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
    redis_client: Annotated[Redis, Depends(get_redis_client)],
    cache: Annotated[Cache, Depends(get_cache)],
) -> UpdateUserPasswordUseCase:
    return UpdateUserPasswordUseCase(
        uow=uow,
        redis_client=redis_client,
        cache=cache,
    )
