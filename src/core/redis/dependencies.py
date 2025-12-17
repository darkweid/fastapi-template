from typing import cast

from fastapi import Request
from redis.asyncio import Redis


async def get_redis_client(request: Request) -> Redis:
    """
    Provide the request-scoped Redis client stored on app.state.
    """
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        raise RuntimeError(
            "Redis client is not initialized. Ensure startup lifecycle ran."
        )
    return cast(Redis, redis_client)
