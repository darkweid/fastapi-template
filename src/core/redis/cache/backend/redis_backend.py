from typing import Any

from redis import asyncio as aioredis

from src.core.patterns.singleton import singleton
from src.core.redis.cache.backend.interface import CacheBackend


@singleton
class RedisCacheBackend(CacheBackend):
    def __init__(self) -> None:
        self.redis: aioredis.Redis | None = None

    @staticmethod
    def _normalize_tag_member(member: Any) -> str:
        if isinstance(member, bytes):
            return member.decode()
        return str(member)

    async def connect(self, url: str) -> None:
        """Connect to the Redis server."""
        if self.redis is None:
            self.redis = await aioredis.Redis.from_url(url)

    async def close(self) -> None:
        """Close the Redis connection."""
        if self.redis is not None:
            await self.redis.aclose()
            self.redis = None

    async def get_value(self, key: str) -> Any:
        """Retrieve a value from the cache by its key."""
        if self.redis is not None:
            return await self.redis.get(key)
        return None

    async def set_value(self, key: str, value: Any, ttl: int) -> None:
        """Store a value in the cache with a specified time-to-live (ttl)."""
        if self.redis is not None:
            await self.redis.set(key, value, ex=ttl)

    async def delete(self, key: str) -> None:
        """Delete a value from the cache by its key."""
        if self.redis is not None:
            await self.redis.delete(key)

    async def add_tag(self, tag: str, key: str) -> None:
        """Associate a key with a tag for cache invalidation purposes."""
        if self.redis is not None:
            result = self.redis.sadd(f"tag:{tag}", key)
            if hasattr(result, "__await__"):
                await result

    async def get_tag_members(self, tag: str) -> set[str]:
        """Get all keys associated with a specific tag."""
        if self.redis is not None:
            tag_key = f"tag:{tag}"
            result = self.redis.smembers(tag_key)
            if hasattr(result, "__await__"):
                keys = await result
                return {self._normalize_tag_member(k) for k in keys}
            return {self._normalize_tag_member(k) for k in result}
        return set()

    async def invalidate_keys(self, keys: list[str]) -> None:
        """Invalidate multiple keys."""
        if keys and self.redis is not None:
            await self.redis.delete(*keys)

    def is_initialized(self) -> bool:
        """Check if the cache backend is initialized."""
        return self.redis is not None
