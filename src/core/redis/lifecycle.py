from redis.asyncio import Redis

from loggers import get_logger

logger = get_logger(__name__)


async def verify_redis_client(redis_client: Redis) -> None:
    """
    Raise unless Redis answers a ping.

    An unreachable Redis must fail startup rather than surface later as a
    per-request error. The caller owns the client and closes it on failure.
    """
    if not await redis_client.ping():
        raise RuntimeError("Redis ping failed during startup")
    logger.info("Redis connection verified.")
