from redis.asyncio import Redis

from src.main.config import config

redis_client = Redis(
    host=config.redis.REDIS_HOST,
    port=config.redis.REDIS_PORT,
    password=config.redis.REDIS_PASSWORD,
    db=config.redis.REDIS_DATABASE,
    decode_responses=True,
)
