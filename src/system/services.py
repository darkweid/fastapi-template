from collections.abc import Awaitable

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from loggers import get_logger
from src.core.errors.exceptions import ServiceUnavailableException
from src.system.repositories import SystemRepository
from src.system.schemas import HealthCheckResponse

logger = get_logger(__name__)


async def ensure_postgres_ready(
    session: AsyncSession, repository: SystemRepository
) -> None:
    """
    Gate readiness on PostgreSQL only - the single dependency without which
    the service cannot answer any meaningful request.

    A module-level function rather than a HealthService method so that /ready/
    never has to resolve a Redis client it would not use.

    The probe catches everything: a host that does not resolve reaches it as a
    bare socket.gaierror from the driver, never wrapped into SQLAlchemyError,
    and an unreachable database has to come back as a 503 rather than as an
    unexpected 500 from the middleware.

    Raises:
        ServiceUnavailableException: PostgreSQL is unreachable (HTTP 503).
    """
    try:
        await repository.ping(session)
    except Exception as exc:
        logger.error("Postgres health check failed", exc_info=exc)
        raise ServiceUnavailableException(
            "Readiness check failed: PostgreSQL is unreachable",
            additional_info={"postgres": False},
        ) from exc


class HealthService:
    """
    Owns the detailed per-dependency report served at /health/.

    /live/ checks nothing and /ready/ uses ensure_postgres_ready above, so this
    class exists for the one endpoint that legitimately needs a Redis client.

    Neither probe reports to Sentry: they run on a timer, so a single outage
    would file one event per poll. Redis degradation that actually affects
    traffic is reported once, with a cooldown, by the rate limiter fallback.
    """

    def __init__(self, redis_client: Redis, repository: SystemRepository) -> None:
        self.redis_client = redis_client
        self.repository = repository

    async def get_status(self, session: AsyncSession) -> HealthCheckResponse:
        """
        Report the state of every dependency.

        PostgreSQL being down raises 503, matching /ready/. Redis being down
        only degrades the status: the process still serves requests, and
        failing the check would take a healthy container out of rotation.

        Raises:
            ServiceUnavailableException: PostgreSQL is unreachable (HTTP 503).
        """
        await ensure_postgres_ready(session, self.repository)
        redis_is_ok = await self._check_redis()
        return HealthCheckResponse(
            status="ok" if redis_is_ok else "degraded",
            postgres=True,
            redis=redis_is_ok,
        )

    async def _check_redis(self) -> bool:
        try:
            ping_result = self.redis_client.ping()
            if isinstance(ping_result, Awaitable):
                return bool(await ping_result)
            return bool(ping_result)
        except Exception as exc:
            logger.error("Redis health check failed", exc_info=exc)
            return False
