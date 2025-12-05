from collections.abc import Awaitable
import logging

from redis.asyncio import Redis
import sentry_sdk
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.errors.exceptions import InfrastructureException
from src.system.schemas import HealthCheckResponse


class HealthService:
    def __init__(self, redis_client: Redis) -> None:
        self.redis_client = redis_client
        self.logger = logging.getLogger(__name__)

    async def get_status(self, session: AsyncSession) -> HealthCheckResponse:
        redis_is_ok = await self._check_redis()
        postgres_is_ok = await self._check_postgres(session)
        if not redis_is_ok or not postgres_is_ok:
            raise InfrastructureException(
                "System health check failed",
                additional_info={"redis": redis_is_ok, "postgres": postgres_is_ok},
            )
        return HealthCheckResponse(status="ok")

    async def _check_redis(self) -> bool:
        try:
            ping_result = self.redis_client.ping()
            if isinstance(ping_result, Awaitable):
                return bool(await ping_result)
            return bool(ping_result)
        except Exception as exc:
            self.logger.error("Redis health check failed", exc_info=exc)
            sentry_sdk.capture_exception(exc)
            return False

    async def _check_postgres(self, session: AsyncSession) -> bool:
        try:
            await session.execute(text("SELECT 1"))
            return True
        except SQLAlchemyError as exc:
            self.logger.error("Postgres health check failed", exc_info=exc)
            sentry_sdk.capture_exception(exc)
            return False
