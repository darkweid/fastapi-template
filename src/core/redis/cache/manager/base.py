from abc import abstractmethod
from collections.abc import Awaitable, Callable, Iterable
import inspect
from typing import Any, cast

from fastapi import Response

from loggers import get_logger
from src.core.database.base import Base as SQLAlchemyBase
from src.core.pagination import PaginatedResponse
from src.core.redis.cache.backend.interface import CacheBackend
from src.core.redis.cache.coder.interface import Coder
from src.core.redis.cache.manager.interface import AbstractCacheManager, R
from src.core.redis.cache.tags import CacheTags
from src.core.schemas import Base as PydanticBase

logger = get_logger(__name__)


class BaseCacheManager(AbstractCacheManager):
    def __init__(self, backend: CacheBackend, coder: Coder) -> None:
        super().__init__(
            backend=backend,
            coder=coder,
        )

    async def _set_tags(
        self, *, tags: list[str] | list[CacheTags], cache_key: str, **kwargs: Any
    ) -> None:
        logger.debug("Setting tags for key '%s': '%s'", cache_key, tags)

        for tag in set(tags):
            if isinstance(tag, CacheTags):
                tag = tag.value
            await self.backend.add_tag(tag, cache_key)

    async def _get_tags_members(
        self, tags: list[str] | list[CacheTags]
    ) -> list[set[str]]:
        key_sets = []
        for tag in tags:
            if isinstance(tag, CacheTags):
                tag_name = tag.value
            else:
                tag_name = str(tag)
            members = await self.backend.get_tag_members(tag_name)
            key_sets.append(members)
        logger.debug("Got keys: %s for tags: %s", key_sets, tags)
        return key_sets

    @staticmethod
    def _extend_tags_using_result(tags: list[str], result: R) -> list[str]:
        new_tags: set[Any] = set()
        if isinstance(result, dict):
            new_tags = {v for k, v in result.items() if "id" in k.lower()}
        elif isinstance(result, PydanticBase):
            data = result.model_dump(exclude_none=True, exclude_unset=True)
            new_tags = {v for k, v in data.items() if "id" in k.lower()}
        elif isinstance(result, SQLAlchemyBase):
            # Convert generator to set
            new_tags = {
                getattr(result, key)
                for key in vars(result)
                if "id" in key.lower() and not key.startswith("_")
            }
        elif isinstance(result, (list, set, PaginatedResponse)):
            # Extract items to process based on result type
            if isinstance(result, PaginatedResponse):
                items_to_process: Iterable[Any] = result.items
            else:
                items_to_process = cast(Iterable[Any], result)

            new_tags = set()
            for item in items_to_process:
                if isinstance(item, PydanticBase):
                    item = item.model_dump(exclude_none=True, exclude_unset=True)
                elif isinstance(item, SQLAlchemyBase):
                    logger.debug("Processing SQLAlchemyBase item: %s", item)
                    tags_from_item = (
                        getattr(item, key)
                        for key in vars(item)
                        if "id" in key.lower() and not key.startswith("_")
                    )
                    new_tags = new_tags.union(set(tags_from_item))
                    continue
                elif isinstance(item, dict):
                    pass
                else:
                    continue

                for k, v in item.items():
                    if "id" in k.lower():
                        new_tags.add(v)

        if new_tags:
            tags.extend(list(new_tags))
            logger.debug("Extended tags with new tags: %s", new_tags)

        return tags

    @staticmethod
    def _extend_tags_using_params(tags: list[str], **kwargs: Any) -> list[str]:
        new_tags: set[Any] = set()
        for key, value in kwargs.items():
            if "id" in key.lower() and value is not None:
                new_tags.add(value)

        if new_tags:
            tags.extend(list(new_tags))

        return tags

    @staticmethod
    def _filter_arguments(
        func: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        sig = inspect.signature(func)
        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        filtered_arguments = {k: v for k, v in bound.arguments.items()}

        return filtered_arguments

    async def _invalidate_tags(
        self,
        tags: list[str] | list[CacheTags],
        excluded_tags: list[str] | list[CacheTags],
    ) -> None:
        key_sets = await self._get_tags_members(tags)

        excluded_key_sets = await self._get_tags_members(excluded_tags)

        if key_sets:
            keys = set.intersection(*key_sets)
            if excluded_key_sets:
                # Convert to list of sets first, then use set.union
                excluded_keys: set[str] = set()
                for key_set in excluded_key_sets:
                    excluded_keys = excluded_keys.union(key_set)
                keys = keys - excluded_keys
            logger.debug("Invalidating keys: %s for tags: %s", keys, tags)
            await self.backend.invalidate_keys(list(keys))

    @abstractmethod
    def decorator(
        self,
        *,
        ttl: int = 3600,
        tags: list[str] | list[CacheTags] | None = None,
        **kwargs: Any,
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

    async def invalidate_tags(
        self,
        tags: list[str] | list[CacheTags],
        excluded_tags: list[str] | list[CacheTags] | None = None,
    ) -> None:
        if excluded_tags is None:
            excluded_tags = []
        await self._invalidate_tags(tags=tags, excluded_tags=excluded_tags)
