import logging
from typing import cast

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


def create_redis_client(connection_url: str, *, decode_responses: bool = True) -> Redis:
    """
    Create a Redis async client from URL. Keeping construction here simplifies
    monkeypatching in tests and centralizes defaults.
    """
    try:
        client = Redis.from_url(connection_url, decode_responses=decode_responses)
        return cast(Redis, client)
    except Exception as exc:  # pragma: no cover - defensive log path
        logger.exception("Failed to create Redis client: %s", exc)
        raise
