from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis

from src.core.redis.dependencies import get_redis_client
from src.system.repositories import SystemRepository
from src.system.services import HealthService, ReadinessService


def get_system_repository() -> SystemRepository:
    return SystemRepository()


async def get_readiness_service(
    repository: Annotated[SystemRepository, Depends(get_system_repository)],
) -> ReadinessService:
    return ReadinessService(repository=repository)


async def get_health_service(
    redis_client: Annotated[Redis, Depends(get_redis_client)],
    readiness: Annotated[ReadinessService, Depends(get_readiness_service)],
) -> HealthService:
    return HealthService(redis_client=redis_client, readiness=readiness)
