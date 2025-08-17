from typing import Any

from redis import asyncio as aioredis

from src.core.patterns.singleton import singleton
from src.core.redis.cache.backend.interface import CacheBackend


@singleton
class RedisCacheBackend(CacheBackend):
    def __init__(self) -> None:
        self.redis: aioredis.Redis | None = None

    async def connect(self, url: str) -> None:
        """Connect to the Redis server."""
        if not self.is_initialized():
            self.redis = await aioredis.Redis.from_url(url)

    async def close(self) -> None:
        """Close the Redis connection."""
        if self.is_initialized():
            await self.redis.aclose()
            self.redis = None

    async def get_value(self, key: str) -> Any:
        """Retrieve a value from the cache by its key."""
        return await self.redis.get(key)

    async def set_value(self, key: str, value: Any, ttl: int) -> None:
        """Store a value in the cache with a specified time-to-live (ttl)."""
        await self.redis.set(key, value, ex=ttl)

    async def delete(self, key: str) -> None:
        """Delete a value from the cache by its key."""
        await self.redis.delete(key)

    async def add_tag(self, tag: str, key: str) -> None:
        """Associate a key with a tag for cache invalidation purposes."""
        await self.redis.sadd(f"tag:{tag}", key)

    async def get_tag_members(self, tag: str) -> set[str]:
        """Get all keys associated with a specific tag."""
        tag_key = f"tag:{tag}"
        keys = await self.redis.smembers(tag_key)
        return keys

    async def invalidate_keys(self, keys: list[str]) -> None:
        """Invalidate multiple keys."""
        if keys:
            await self.redis.delete(*keys)

    def is_initialized(self) -> bool:
        """Check if the redis cache backend is initialized."""
        return bool(self.redis)
