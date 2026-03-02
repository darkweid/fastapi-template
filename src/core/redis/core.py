import logging

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


def create_redis_client(connection_url: str, *, decode_responses: bool = True) -> Redis:
    """
    Create a Redis async client from URL.
    """
    try:
        return Redis.from_url(connection_url, decode_responses=decode_responses)
    except Exception:  # pragma: no cover - defensive log path
        logger.exception("Failed to create Redis client")
        raise
