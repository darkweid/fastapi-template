from abc import ABC, abstractmethod
from typing import Any


class CacheBackend(ABC):
    @abstractmethod
    async def get_value(self, key: str) -> Any:
        """Retrieve a value from the cache by its key."""
        raise NotImplementedError

    @abstractmethod
    async def set_value(self, key: str, value: Any, ttl: int) -> None:
        """Store a value in the cache with a specified time-to-live (ttl)."""
        raise NotImplementedError

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete a value from the cache by its key."""
        raise NotImplementedError

    @abstractmethod
    async def add_tag(self, tag: str, key: str) -> None:
        """Associate a key with a tag for cache invalidation purposes."""
        raise NotImplementedError

    @abstractmethod
    async def get_tag_members(self, tag: str) -> set[str]:
        """Get all members(keys) associated with a specific tag."""
        raise NotImplementedError

    @abstractmethod
    async def invalidate_keys(self, keys: list[str]) -> None:
        """Invalidate multiple keys in the cache."""
        raise NotImplementedError

    @abstractmethod
    def is_initialized(self) -> bool:
        """Check if the cache backend is initialized."""
        raise NotImplementedError
