import hashlib
from functools import wraps
from types import FunctionType
from typing import cast
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from src.core.database.models import Base
from src.core.redis.cache.manager.base import BaseCacheManager
from src.core.redis.cache.manager.interface import AbstractCacheManager, R
from src.core.redis.cache.tags import CacheTags
from loggers import get_logger

logger = get_logger(__name__)


class CacheManager(BaseCacheManager, AbstractCacheManager):

    @staticmethod
    async def key_builder(
        func: Callable,
        **filtered_kwargs,
    ) -> str:
        excluded_args = {"session", "self", "cls"}
        filtered_kwargs = {
            k: v for k, v in filtered_kwargs.items() if k not in excluded_args
        }

        func = cast(FunctionType, func)

        cache_key = hashlib.md5(  # noqa: S324
            f"{func.__module__}:{func.__name__}:{filtered_kwargs}".encode()
        ).hexdigest()

        return f"cache:{cache_key}"

    @staticmethod
    def _extract_session(**filtered_kwargs) -> AsyncSession | None:
        return filtered_kwargs.get("session")

    @staticmethod
    def _parse_filters(filters: dict, **filtered_kwargs) -> dict | None:
        parsed_filters = {}
        for key, value in filters.items():
            if value not in filtered_kwargs:
                logger.debug(
                    "%s not provided | returning None from _parse_filters", key
                )
                return None

            parsed_filters[key] = filtered_kwargs[value]

        return parsed_filters

    @staticmethod
    async def _get_identity(
        session: AsyncSession,
        model: Base,
        field: InstrumentedAttribute | str,
        **filters,
    ) -> str:
        field = (
            field if isinstance(field, InstrumentedAttribute) else getattr(model, field)
        )
        logger.debug("Getting identity for field: %s, with filters: %s", field, filters)

        order_by = getattr(model, "created_at", None)
        if order_by is None:
            order_by = getattr(model, "id", None)

        query = select(field).filter_by(**filters).order_by(order_by.desc())

        result = await session.execute(query)
        return result.scalars().first()

    def decorator(
        self,
        ttl: int = 3600,
        tags: list[str] | list[CacheTags] | None = None,
        identity_field: InstrumentedAttribute | str | None = None,
        identity_model: Base | None = None,
        **filters,
    ) -> Callable[[Callable[..., Awaitable[R]]], Callable[..., Awaitable[R]]]:
        """
        Decorator to cache the result of a function call.

        Args:
            ttl (int): Time-to-live for the cache in seconds. Default is 3600 seconds.
            tags (list[str] | list[CacheTags] | None): List of tags associated with this cache entry.
            identity_field (InstrumentedAttribute | str | None): Field to identify the entity for tagging.
            identity_model (Base | None): Model class to use for fetching the identity.
            **filters: Additional filters to apply when fetching the identity.

        Returns:
            Callable: A decorator that wraps the function to add caching logic.
        """

        if tags is None:
            tags = []

        def wrapper(func: Callable) -> Callable[..., Awaitable[R]]:
            @wraps(func)
            async def inner(*args, **kwargs) -> R:
                if not self.backend.is_initialized():
                    raise RuntimeError("Cache backend is not initialized")

                logger.debug("Cache decorator called for function: %s", func.__name__)

                local_tags = tags.copy()

                filtered_kwargs = self._filter_arguments(func, *args, **kwargs)
                local_tags = self._extend_tags_using_params(
                    tags=local_tags, **filtered_kwargs
                )

                cache_key = await self.key_builder(func, **filtered_kwargs)
                try:
                    cached = await self.backend.get_value(cache_key)
                except Exception:
                    logger.exception("Failed to get cache for key %s", cache_key)
                    cached = None

                if cached is None:
                    logger.debug("Cache miss for key: %s", cache_key)

                    result = await func(*args, **kwargs)
                    logger.debug("Cache result: %s", result)
                    to_cache = self.coder.encode(result)
                    try:
                        logger.debug("Setting cache for key: %s", cache_key)

                        await self.backend.set_value(cache_key, to_cache, ttl)
                    except Exception:
                        logger.exception("Failed to set cache for key %s", cache_key)

                    try:
                        if identity_field and identity_model:
                            session = self._extract_session(**filtered_kwargs)
                            parsed_filters = self._parse_filters(
                                filters=filters, **filtered_kwargs
                            )
                            if session and parsed_filters:
                                identity_id = await self._get_identity(
                                    session=session,
                                    model=identity_model,
                                    field=identity_field,
                                    **parsed_filters,
                                )
                                if identity_id:
                                    local_tags.append(identity_id)

                        if result:
                            try:
                                local_tags = self._extend_tags_using_result(
                                    tags=local_tags, result=result
                                )
                            except Exception:
                                logger.exception(
                                    "Failed to extract tags from result for key %s",
                                    cache_key,
                                )

                        await self._set_tags(tags=local_tags, cache_key=cache_key)
                    except Exception:
                        logger.exception("Failed to set tags for key %s", cache_key)
                else:
                    logger.debug(f"Cache hit for key: {cache_key}")
                    result = cast(R, self.coder.decode(cached))

                return result

            return inner

        return wrapper
