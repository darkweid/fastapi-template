from functools import partial
from typing import Annotated
from uuid import uuid4

from fastapi import Depends
from redis.asyncio import Redis

from loggers import get_logger
from src.core.cache.dependencies import get_cache
from src.core.cache.interface import Cache
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.errors.exceptions import TooManyRequestsException
from src.core.redis.dependencies import get_redis_client
from src.core.schemas import TokenModel
from src.core.utils.security import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    mask_email,
    needs_password_rehash,
    verify_password,
)
from src.user.auth.errors import InvalidCredentialsError
from src.user.auth.redis_keys import auth_redis_keys
from src.user.auth.schemas import LoginUserModel
from src.user.auth.security import create_access_token, create_refresh_token
from src.user.cache_keys import user_cache_keys
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
    Log in a user and return access and refresh tokens.

    Inputs:
    - data: LoginUserModel containing email and password.

    Validations:
    - The email must be under the failed-login limit for the current window.
    - User must exist.
    - Password must be correct.
    - Account must pass admission (verified and active) - see policies.py.

    Workflow:
    1) Reject when the per-email failure counter has reached the limit,
       before any database or password-hash work is spent.
    2) Retrieve user by email.
    3) Verify password (using dummy hash if user not found to prevent timing
       attacks). Either failure - unknown email included - feeds the counter;
       an admission violation with a correct password does not.
    4) Check account admission; a violation is masked into the same error as
       bad credentials (anti-enumeration).
    5) Rehash and persist the password if needed.
    6) Invalidate the user cache namespace.
    7) Commit the transaction, clear the failure counter, and generate access
       and refresh tokens.

    Side effects:
    - Persists password hash updates when rehashing is required.
    - Bumps the user:{id} cache namespace version twice (pre- and post-commit) -
      every write to the user row does this unconditionally, including this one
      where the row is only sometimes touched (the rehash branch), so no one
      has to remember an exception to the rule.
    - Token creation handles its own caching.

    Errors:
    - TooManyRequestsException: the email has exhausted the failure window.
    - InvalidCredentialsError: wrong credentials, unverified or blocked account -
      one code by design (anti-enumeration).

    Returns:
    - TokenModel with access and refresh tokens.
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
        self,
        data: LoginUserModel,
    ) -> TokenModel:
        # data.email is already normalized by EmailNormalizationMixin, so the
        # counter key cannot be split across spellings of one address.
        failures_key = auth_redis_keys.login_failures(data.email)
        await self._ensure_email_not_throttled(failures_key)

        async with self.uow as uow:
            user = await uow.users.get_single(uow.session, email=data.email)
            if not user:
                logger.debug(
                    "[LoginUser] User with email '%s' not found.",
                    mask_email(data.email),
                )
                await verify_password(data.password, DUMMY_PASSWORD_HASH)
                await self._register_login_failure(failures_key)
                raise InvalidCredentialsError(INVALID_CREDENTIALS_MESSAGE)

            correct_password = await verify_password(data.password, user.password_hash)
            if not correct_password:
                logger.debug(
                    "[LoginUser] Incorrect password for user '%s'",
                    mask_email(data.email),
                )
                await self._register_login_failure(failures_key)
                raise InvalidCredentialsError(INVALID_CREDENTIALS_MESSAGE)

            violation = account_access_violation(user)
            if violation is not None:
                logger.debug(
                    "[LoginUser] Account of '%s' fails admission (%s).",
                    mask_email(data.email),
                    violation,
                )
            ensure_can_authenticate(user)

            await self._rehash_password_if_needed(uow, user, data.password)
            session_id = str(uuid4())
            token_data = {"sub": str(user.id)}
            await self.cache.invalidate(user_cache_keys.namespace(user.id))
            uow.add_after_commit_hook(
                partial(self.cache.invalidate, user_cache_keys.namespace(user.id))
            )
            await uow.commit()
            await self.redis_client.delete(failures_key)
            return TokenModel(
                access_token=await create_access_token(
                    token_data, redis_client=self.redis_client, session_id=session_id
                ),
                refresh_token=await create_refresh_token(
                    token_data,
                    redis_client=self.redis_client,
                    session_id=session_id,
                ),
            )

    async def _ensure_email_not_throttled(self, failures_key: str) -> None:
        failures = await self.redis_client.get(failures_key)
        if failures is None or int(failures) < LOGIN_FAILURES_LIMIT:
            return

        retry_after = await self.redis_client.ttl(failures_key)
        if retry_after < 0:
            # A counter stranded without a TTL (crash between INCR and EXPIRE)
            # never reaches _register_login_failure again - this gate rejects
            # first - so the gate must arm the window or the lockout is permanent.
            await self.redis_client.expire(
                failures_key, LOGIN_FAILURES_WINDOW_SECONDS, nx=True
            )
            retry_after = LOGIN_FAILURES_WINDOW_SECONDS
        raise TooManyRequestsException(
            "Too many failed login attempts. Try again later.",
            retry_after=retry_after,
        )

    async def _register_login_failure(self, failures_key: str) -> None:
        await self.redis_client.incr(failures_key)
        # NX arms the window only when the key has no TTL yet, so later
        # failures never push the reset forward - and unlike an "only on the
        # first INCR" guard, a crash between INCR and EXPIRE cannot strand a
        # persistent counter: the next failure arms it.
        await self.redis_client.expire(
            failures_key, LOGIN_FAILURES_WINDOW_SECONDS, nx=True
        )

    async def _rehash_password_if_needed(
        self,
        uow: ApplicationUnitOfWork,
        user: User,
        raw_password: str,
    ) -> None:
        if not needs_password_rehash(user.password_hash):
            return
        new_hash = await hash_password(raw_password)
        await uow.users.update(
            uow.session,
            {"password_hash": new_hash},
            id=user.id,
        )


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
