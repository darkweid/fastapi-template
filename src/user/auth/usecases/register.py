from typing import Annotated

from fastapi import Depends
from redis.exceptions import RedisError

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.errors.exceptions import InstanceProcessingException
from src.core.utils.security import hash_password
from src.event_log.actor import Actor
from src.user.auth.realm import USER_AUTH_REALM
from src.user.auth.schemas import CreateUserModel
from src.user.auth.services.email_notifier import (
    EmailNotifier,
    ThrottleSlot,
    get_verification_notifier,
)
from src.user.events import UserRegistered
from src.user.models import User
from src.user.schemas import UserProfileViewModel

logger = get_logger(__name__)


class RegisterUseCase:
    """
    Register a user and send the verification email.

    The email is written to the outbox inside the same transaction as the user
    row, so a rolled back registration never leaves a stray send. It claims the
    window a resend to the same address claims: while that one is taken (the
    address had an account deleted moments ago) no email is queued, the
    registration succeeds all the same, and the user asks for a resend. With
    Redis unreachable the email is queued unthrottled, or an outage would stop
    registration; one address still gets one email, since it cannot register
    twice.

    The user row is flushed before the claim, so a duplicate claims nothing.
    The claim is released when anything after it fails. A duplicate email or
    username reaches the database error middleware as an IntegrityError and is
    answered 409 `already_exists` there, not here.

    Side effects:
    - Appends `user.registered` to the event log, in this transaction.
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork,
        notifier: EmailNotifier,
    ) -> None:
        self.uow = uow
        self.notifier = notifier

    async def execute(
        self, data: CreateUserModel, ip: str | None = None
    ) -> UserProfileViewModel:
        user_data = data.model_dump()
        raw_password = user_data.pop("password")
        user_data["password_hash"] = await hash_password(raw_password)
        async with self.uow as uow:
            user = await uow.users.create(
                session=uow.session,
                data=user_data,
            )
            await uow.flush()
            # Outbox row rides the same transaction: a rollback cancels the
            # email, a broker outage no longer fails registration.
            slot = await self._queue_verification(uow, user)
            try:
                # Anonymous: nothing has proved yet that the person filling the
                # form owns the address they registered with.
                await uow.publish(
                    Actor.anonymous(ip=ip), UserRegistered(object_id=user.id)
                )
                await uow.commit()
            except Exception:
                if slot:
                    await self.notifier.release_throttle(slot)
                raise

        logger.info("[Register User] User %s registered successfully.", user.id)
        return UserProfileViewModel.model_validate(user)

    async def _queue_verification(
        self, uow: ApplicationUnitOfWork, user: User
    ) -> ThrottleSlot | None:
        throttle_key = USER_AUTH_REALM.keys.throttle("resend_verification", user.email)
        try:
            return await self.notifier.send(
                uow=uow, user=user, throttle_key=throttle_key
            )
        except InstanceProcessingException:
            logger.info("[Register User] Verification email skipped by the throttle.")
        except RedisError:
            logger.warning("[Register User] Throttle unavailable, email unthrottled.")
            return await self.notifier.send(uow=uow, user=user)
        return None


def get_register_use_case(
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
    notifier: Annotated[EmailNotifier, Depends(get_verification_notifier)],
) -> RegisterUseCase:
    return RegisterUseCase(uow=uow, notifier=notifier)
