from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis

from src.core.auth.usecases.logout import LogoutUseCase
from src.core.redis.dependencies import get_redis_client
from src.user.auth.realm import USER_AUTH_REALM


def get_logout_use_case(
    redis_client: Annotated[Redis, Depends(get_redis_client)],
) -> LogoutUseCase:
    return LogoutUseCase(redis_client, realm=USER_AUTH_REALM)
