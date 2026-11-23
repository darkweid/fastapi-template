from functools import partial
from typing import Annotated, NoReturn

from fastapi import Depends
from redis.asyncio import Redis

from loggers import get_logger
from src.core.auth.errors import InvalidCredentialsError
from src.core.auth.session_issuance import LoginThrottle, issue_session_pair
from src.core.cache.interface import Cache
from src.core.cache.runtime import get_cache
from src.core.database.detached import detached_read
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.redis.dependencies import get_redis_client
from src.core.schemas import TokenModel
from src.core.utils.security import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    mask_email,
    needs_password_rehash,
    password_still_matches,
    verify_password,
)
from src.event_log.actor import Actor
from src.user.auth.realm import USER_AUTH_REALM
from src.user.auth.schemas import LoginUserModel
from src.user.cache_keys import user_cache_keys
from src.user.events import UserSignedIn, UserSignInFailed
from src.user.models import User
from src.user.policies import (
    INVALID_CREDENTIALS_MESSAGE,
    account_access_violation,
    ensure_can_authenticate,
)

logger = get_logger(__name__)

# Soft per-email throttle: window-scoped counter, no permanent lockout. The
# threshold is far above any legitimate retry pattern, so only distributed
# credential stuffing reaches it; per-IP limiting alone cannot see that attack.
# Deliberate trade-off: anyone who knows the email can fill the window with
# wrong passwords and block login for its remainder. The escape hatch is the
# password-reset flow, which proves mailbox ownership and clears the counter.
LOGIN_FAILURES_LIMIT = 25
LOGIN_FAILURES_WINDOW_SECONDS = 15 * 60


class LoginUserUseCase:
    """
    Log in a user and issue an access and refresh token pair.

    The per-email failure counter is checked before any database or password
    hash work, so a flood of guesses costs one Redis read each. An unknown
    email is still verified against a dummy hash, so timing does not reveal
    whether the account exists. Every credential failure feeds the counter; an
    admission failure with a correct password does not, since that caller
    already proved ownership of the account.

    Wrong credentials, an unverified account and a blocked account all answer
    InvalidCredentialsError under one code, by design - the client learns that
    login failed and nothing else.

    Side effects:
    - Appends `user.signed_in` or `user.sign_in_failed` to the event log. The
      success row is written only once the session exists, so it cannot claim
      a sign-in whose credentials never reached the caller; a failure commits
      its row on its own before raising, so the trail of a credential-stuffing
      run survives the rejection it describes.
    - Persists a rehashed password when the stored hash uses outdated parameters.
    - Bumps the user:{id} cache namespace version twice, pre- and post-commit.
      Every write to the user row does this unconditionally, this one included,
      where the row is only sometimes touched - so no one has to remember an
      exception to the rule.

    Errors:
    - TooManyRequestsException: the email has exhausted its failure window.
    - InvalidCredentialsError: wrong credentials, unverified or blocked account.
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
        self.throttle = LoginThrottle(
            USER_AUTH_REALM, LOGIN_FAILURES_LIMIT, LOGIN_FAILURES_WINDOW_SECONDS
        )

    async def execute(
        self,
        data: LoginUserModel,
        ip: str | None = None,
    ) -> TokenModel:
        # data.email is already normalized by EmailNormalizationMixin, so the
        # counter key cannot be split across spellings of one address.
        await self.throttle.ensure_under_limit(data.email, self.redis_client)

        # Argon2 runs before any transaction: it waits for a bounded executor,
        # and a pooled connection held through that wait starves the API.
        async with detached_read(self.uow.session) as read:
            candidate = await self.uow.users.get_single(
                self.uow.session, email=data.email
            )
            read.detach(candidate)

        verified_hash = candidate.password_hash if candidate else DUMMY_PASSWORD_HASH
        password_matches = await verify_password(data.password, verified_hash)
        if candidate is None or not password_matches:
            async with self.uow as uow:
                await self._reject_credentials(
                    uow,
                    data.email,
                    ip,
                    reason="unknown_email" if candidate is None else "wrong_password",
                )

        new_hash = await self._rehash_if_needed(candidate, data.password)

        async with self.uow as uow:
            # Held until the session is in Redis and the commit is done: a
            # password change takes the same lock before it wipes sessions, so
            # it either sees this session or has already replaced the hash.
            user = await uow.users.get_single(
                uow.session,
                for_update=True,
                populate_existing=True,
                id=candidate.id,
            )
            if user is None or not await password_still_matches(
                data.password, verified_hash, user.password_hash
            ):
                await self._reject_credentials(
                    uow, data.email, ip, reason="wrong_password"
                )

            violation = account_access_violation(user)
            if violation is not None:
                logger.debug(
                    "[LoginUser] Account of '%s' fails admission (%s).",
                    mask_email(data.email),
                    violation,
                )
                # The password already matched, so this attempt is the
                # account owner's and the row is attributed to them.
                await uow.publish(
                    Actor.user(user.id, ip=ip),
                    UserSignInFailed(email=data.email, reason=violation),
                )
                await uow.commit()
                # Raised here rather than after the block: the commit above
                # completed the UoW, so nothing may touch it again.
                ensure_can_authenticate(user)

            # A row rehashed or changed meanwhile keeps its own newer hash.
            if new_hash is not None and user.password_hash == verified_hash:
                await uow.users.update(
                    uow.session, {"password_hash": new_hash}, id=user.id
                )
            await self.cache.invalidate(user_cache_keys.namespace(user.id))
            uow.add_after_commit_hook(
                partial(self.cache.invalidate, user_cache_keys.namespace(user.id))
            )
            # The session is issued before the row that records it, and the
            # commit is the last thing that happens: a Redis failure in either
            # call below must leave no `user.signed_in` claiming a sign-in the
            # caller never got credentials for. The reverse order costs less -
            # a commit failure here strands a session in Redis that nothing can
            # reach, since its tokens only exist in a response that never
            # arrives, and it expires on its own.
            tokens = await issue_session_pair(
                realm=USER_AUTH_REALM,
                subject_id=str(user.id),
                claims={},
                redis_client=self.redis_client,
            )
            await self.throttle.clear(data.email, self.redis_client)
            await uow.publish(
                Actor.user(user.id, ip=ip),
                UserSignedIn(object_id=user.id),
            )
            await uow.commit()
            return tokens

    async def _reject_credentials(
        self,
        uow: ApplicationUnitOfWork,
        email: str,
        ip: str | None,
        *,
        reason: str,
    ) -> NoReturn:
        logger.debug(
            "[LoginUser] Credentials for '%s' rejected (%s).",
            mask_email(email),
            reason,
        )
        await self.throttle.record_failure(email, self.redis_client)
        # Anonymous even for an existing account: nothing here proves who made
        # the attempt. Nothing else is pending, so this commit carries only the
        # failed attempt.
        await uow.publish(
            Actor.anonymous(ip=ip), UserSignInFailed(email=email, reason=reason)
        )
        await uow.commit()
        raise InvalidCredentialsError(INVALID_CREDENTIALS_MESSAGE)

    async def _rehash_if_needed(self, user: User, raw_password: str) -> str | None:
        # An account that fails admission is not rehashed: the extra Argon2
        # call would tell a correct password from a wrong one by timing.
        if account_access_violation(user) is not None:
            return None
        if not needs_password_rehash(user.password_hash):
            return None
        return await hash_password(raw_password)


def get_login_user_use_case(
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
    redis_client: Annotated[Redis, Depends(get_redis_client)],
    cache: Annotated[Cache, Depends(get_cache)],
) -> LoginUserUseCase:
    return LoginUserUseCase(
        uow=uow,
        redis_client=redis_client,
        cache=cache,
    )
