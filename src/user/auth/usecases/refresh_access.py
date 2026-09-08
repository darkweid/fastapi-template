from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis

from src.core.auth.usecases.refresh_access import RefreshAccessUseCase
from src.core.redis.dependencies import get_redis_client
from src.user.auth.realm import USER_AUTH_REALM
from src.user.models import User
from src.user.policies import ensure_can_use_session


def get_refresh_access_use_case(
    redis_client: Annotated[Redis, Depends(get_redis_client)],
) -> RefreshAccessUseCase[User]:
    return RefreshAccessUseCase(
        redis_client,
        realm=USER_AUTH_REALM,
        claims_builder=lambda user: {"sub": str(user.id)},
        admission=ensure_can_use_session,
    )
