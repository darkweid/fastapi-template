from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import Response

from src.core.redis.cache.backend.interface import CacheBackend
from src.core.redis.cache.coder.interface import Coder
from src.core.redis.cache.tags import CacheTags

R = TypeVar("R")


class AbstractCacheManager(ABC):
    def __init__(self, backend: CacheBackend, coder: Coder) -> None:
        self.backend = backend
        self.coder = coder

    @abstractmethod
    async def key_builder(self, *args: object, **kwargs: object) -> str:
        """
        Abstract method to build a cache key based on the request and optional identity ID.
        """
        raise NotImplementedError

    @abstractmethod
    async def invalidate_tags(self, tags: list[str] | list[CacheTags]) -> None:
        raise NotImplementedError

    @abstractmethod
    def decorator(
        self,
        *,
        ttl: int = 3600,
        tags: list[str] | list[CacheTags] | None = None,
        **kwargs: object,
    ) -> Callable[
        [Callable[..., Awaitable[R]]], Callable[..., Awaitable[R | Response]]
    ]:
        """
        Abstract method to decorate route functions with caching logic.

        Required keyword arguments:
        - ttl: Time-to-live for the cache in seconds.
        - tags: List of tags associated with this cache entry.

        Additional keyword arguments can be passed and handled by subclasses.
        """
        raise NotImplementedError
