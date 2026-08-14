import asyncio
from collections.abc import Awaitable

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from loggers import get_logger
from src.core.errors.exceptions import ServiceUnavailableException
from src.system.repositories import SystemRepository
from src.system.schemas import HealthCheckResponse

logger = get_logger(__name__)

# A probe must answer in bounded time. Without this it inherits the request
# pool's 30s pool_timeout, so a saturated pool leaves the load balancer waiting
# half a minute before hearing anything at all.
POSTGRES_PROBE_TIMEOUT_SECONDS = 2.0


class ReadinessService:
    """
    Backs /ready/, and owns the PostgreSQL probe both probes share.

    Separate from HealthService so that readiness depends on PostgreSQL and
    nothing else: a Redis outage must never be able to pull every instance out
    of the load balancer.
    """

    def __init__(self, repository: SystemRepository) -> None:
        self.repository = repository

    async def ensure_ready(self, session: AsyncSession) -> None:
        """
        Gate readiness on PostgreSQL only - the single dependency without which
        the service cannot answer any meaningful request.

        Raises:
            ServiceUnavailableException: PostgreSQL is unreachable, or the
                request pool cannot hand out a connection within the probe
                timeout (HTTP 503). Not logged here: the handler logs every
                503 once, and a polling probe must not log an outage twice.
        """
        if not await self.check_postgres(session):
            raise ServiceUnavailableException(
                "Readiness check failed: PostgreSQL is unreachable",
                additional_info={"postgres": False},
            )

    async def check_postgres(self, session: AsyncSession) -> bool:
        """
        Catches everything on purpose: a host that does not resolve reaches the
        probe as a bare socket.gaierror from the driver, never wrapped into
        SQLAlchemyError, and a probe that raises on an unreachable database
        defeats its own point.
        """
        try:
            async with asyncio.timeout(POSTGRES_PROBE_TIMEOUT_SECONDS):
                await self.repository.ping(session)
            return True
        except Exception as exc:
            logger.warning("Postgres health check failed: %s", exc)
            return False


class HealthService:
    """
    Owns the detailed per-dependency report served at /health/.

    Neither this nor ReadinessService reports to Sentry. They run on a timer,
    so a single outage would file one event per poll, and the outage is what
    infrastructure alerting watches anyway. Redis degradation that actually
    affects traffic is reported once, with a cooldown, by the rate limiter
    fallback.
    """

    def __init__(self, redis_client: Redis, readiness: ReadinessService) -> None:
        self.redis_client = redis_client
        self.readiness = readiness

    async def get_status(self, session: AsyncSession) -> HealthCheckResponse:
        """
        Report the state of every dependency.

        Never raises: this is the endpoint monitoring reads to find out *which*
        dependency is down, so it must keep answering with a body exactly when
        something is broken. /ready/ is what turns a Postgres outage into a 503.
        """
        postgres_is_ok = await self.readiness.check_postgres(session)
        redis_is_ok = await self._check_redis()
        is_healthy = postgres_is_ok and redis_is_ok
        return HealthCheckResponse(
            status="ok" if is_healthy else "degraded",
            postgres=postgres_is_ok,
            redis=redis_is_ok,
        )

    async def _check_redis(self) -> bool:
        try:
            ping_result = self.redis_client.ping()
            if isinstance(ping_result, Awaitable):
                return bool(await ping_result)
            return bool(ping_result)
        except Exception as exc:
            logger.warning("Redis health check failed: %s", exc)
            return False
