from __future__ import annotations

from typing import Any

from src.core.redis.cache.backend.interface import CacheBackend


class FakeCacheBackend(CacheBackend):
    def __init__(self, initialized: bool = True) -> None:
        self._store: dict[str, bytes] = {}
        self._tags: dict[str, set[str]] = {}
        self._initialized = initialized
        self.invalidated_keys: list[str] = []

    async def get_value(self, key: str) -> Any:
        return self._store.get(key)

    async def set_value(self, key: str, value: Any, ttl: int) -> None:
        self._store[key] = value

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def add_tag(self, tag: str, key: str) -> None:
        if tag not in self._tags:
            self._tags[tag] = set()
        self._tags[tag].add(key)

    async def get_tag_members(self, tag: str) -> set[str]:
        return set(self._tags.get(tag, set()))

    async def invalidate_keys(self, keys: list[str]) -> None:
        for key in keys:
            self._store.pop(key, None)
            self.invalidated_keys.append(key)

    def is_initialized(self) -> bool:
        return self._initialized

    def seed(self, key: str, value: bytes) -> None:
        self._store[key] = value
