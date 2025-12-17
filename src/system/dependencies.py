from fastapi import Depends
from redis.asyncio import Redis

from src.core.redis.dependencies import get_redis_client
from src.system.services import HealthService


async def get_health_service(
    redis_client: Redis = Depends(get_redis_client),
) -> HealthService:
    return HealthService(redis_client=redis_client)
