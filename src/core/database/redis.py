from redis.asyncio import Redis

from src.core.settings import settings

redis_client = Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    password=settings.redis_password,
    db=settings.redis_database,
    decode_responses=True
)
