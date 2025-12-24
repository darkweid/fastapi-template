from collections.abc import Awaitable, Callable
from functools import wraps
import hashlib
from inspect import Parameter, Signature
import json
from typing import Any, cast

from fastapi import Request, Response, status
from fastapi.dependencies.utils import get_typed_signature

from loggers import get_logger
from src.core.redis.cache.manager.base import BaseCacheManager
from src.core.redis.cache.manager.interface import AbstractCacheManager, R
from src.core.redis.cache.tags import CacheTags

logger = get_logger(__name__)


class RouteCacheManager(BaseCacheManager, AbstractCacheManager):
    def __init__(self, backend: Any, coder: Any) -> None:
        super().__init__(backend=backend, coder=coder)
        self.cache_status_header = "X-Cache-Status"
        self.cache_control_header = "Cache-Control"
        self.etag_header = "ETag"
        self.if_none_header = "If-None-Match"

    @staticmethod
    async def key_builder(  # type: ignore
        request: Request,
        identity_id: str | None = None,
    ) -> str:
        func_name = f"{request.url.path}:{request.method}"
        query_params = dict(request.query_params)
        path_params = dict(request.path_params)

        payload = {
            "query": query_params,
            "path": path_params,
            "identity": identity_id,
        }
        logger.debug("%s | %s", path_params, query_params)

        raw = json.dumps(payload, sort_keys=True)
        hashed = hashlib.md5(raw.encode()).hexdigest()

        return f"cache:{func_name}:{hashed}"

    @staticmethod
    async def _get_tags_from_request(request: Request) -> list[str]:
        query_params = dict(request.query_params)
        path_params = dict(request.path_params)

        tags = []
        params = query_params | path_params
        for key, value in params.items():
            if "id" in key:
                tags.append(value)

        return tags

    async def _set_tags(
        self, *, tags: list[str] | list[CacheTags], cache_key: str, **kwargs: Any
    ) -> None:
        request = kwargs.get("request")
        if request is not None:
            tags_from_request = await self._get_tags_from_request(request)
            if tags_from_request:
                tags = list(set(tags + tags_from_request))
            logger.debug("Setting tags: %s for cache key: %s", tags, cache_key)

        return await super()._set_tags(tags=tags, cache_key=cache_key)

    @staticmethod
    def _locate_param(
        sig: Signature, dep: Parameter, to_inject: list[Parameter]
    ) -> Parameter:
        """Locate an existing parameter in the decorated endpoint

        If not found, returns the injectable parameter, and adds it to the to_inject list.
        """
        param = next(
            (p for p in sig.parameters.values() if p.annotation is dep.annotation), None
        )
        if param is None:
            to_inject.append(dep)
            param = dep
        return param

    @staticmethod
    def _augment_signature(signature: Signature, *extra: Parameter) -> Signature:
        if not extra:
            return signature

        parameters = list(signature.parameters.values())
        variadic_keyword_params: list[Parameter] = []
        while parameters and parameters[-1].kind is Parameter.VAR_KEYWORD:
            variadic_keyword_params.append(parameters.pop())

        return signature.replace(
            parameters=[*parameters, *extra, *variadic_keyword_params]
        )

    def decorator(  # type: ignore
        self,
        *,
        ttl: int = 3600,
        tags: list[str] | list[CacheTags] | None = None,
        identity: Callable[[Request], Awaitable[str | None]] | None = None,
        **kwargs: Any,
    ) -> Callable[
        [Callable[..., Awaitable[R]]], Callable[..., Awaitable[R | Response]]
    ]:
        if tags is None:
            tags = []

        injected_request = Parameter(
            name="__fastapi_cache_request",
            annotation=Request,
            kind=Parameter.KEYWORD_ONLY,
        )
        injected_response = Parameter(
            name="__fastapi_cache_response",
            annotation=Response,
            kind=Parameter.KEYWORD_ONLY,
        )

        def wrapper(
            func: Callable[..., Awaitable[R]],
        ) -> Callable[..., Awaitable[R | Response]]:
            wrapped_signature = get_typed_signature(func)
            to_inject: list[Parameter] = []
            request_param = self._locate_param(
                wrapped_signature, injected_request, to_inject
            )
            response_param = self._locate_param(
                wrapped_signature, injected_response, to_inject
            )

            @wraps(func)
            async def inner(*args: Any, **kwargs: Any) -> R | Response:
                if not self.backend.is_initialized():
                    raise RuntimeError("Cache backend is not initialized")

                logger.debug("Cache decorator called for function: %s", func.__name__)
                local_tags = (
                    tags.copy()
                )  # avoid mutating shared list across decorator calls
                sanitized_kwargs = dict(kwargs)
                sanitized_kwargs.pop(request_param.name, None)
                sanitized_kwargs.pop(response_param.name, None)

                filtered_kwargs = self._filter_arguments(
                    func, *args, **sanitized_kwargs
                )
                normalized_tags: list[str] = []
                for tag in local_tags:
                    if isinstance(tag, CacheTags):
                        normalized_tags.append(tag.value)
                    else:
                        normalized_tags.append(str(tag))
                local_tags = self._extend_tags_using_params(
                    tags=normalized_tags, **filtered_kwargs
                )

                request = kwargs.pop(request_param.name, None) or next(
                    (arg for arg in args if isinstance(arg, Request)), None
                )
                response = kwargs.pop(response_param.name, None) or next(
                    (arg for arg in args if isinstance(arg, Response)), None
                )
                if not request:
                    raise ValueError("Request object not found")

                identity_id = await identity(request) if identity else None
                if identity_id:
                    local_tags.append(identity_id)

                cache_key = await self.key_builder(request, identity_id)
                try:
                    cached = await self.backend.get_value(cache_key)
                except Exception:
                    logger.exception("Failed to get cache for key %s", cache_key)

                    cached = None

                if cached is None:
                    # Or use request.headers.get("Cache-Control") == "no-cache" if headers are considered later
                    logger.debug("Cache miss for key: %s", cache_key)

                    result = await func(*args, **kwargs)
                    to_cache = self.coder.encode(result)
                    try:
                        await self.backend.set_value(cache_key, to_cache, ttl)
                    except Exception:
                        logger.exception("Failed to set cache for key %s", cache_key)

                    try:
                        local_tags = self._extend_tags_using_result(
                            tags=local_tags, result=result
                        )
                        await self._set_tags(
                            tags=local_tags, cache_key=cache_key, request=request
                        )
                    except Exception:
                        logger.exception("Failed to set tags for key %s", cache_key)

                    if response:
                        response.headers.update(
                            {
                                self.cache_control_header: f"max-age={ttl}",
                                self.etag_header: f"W/{hash(to_cache)}",
                                self.cache_status_header: "MISS",
                            }
                        )
                else:
                    logger.debug("Cache hit for key: %s", cache_key)

                    if response:
                        etag = f"W/{hash(cached)}"
                        response.headers.update(
                            {
                                self.cache_control_header: f"max-age={ttl}",
                                self.etag_header: etag,
                                self.cache_status_header: "HIT",
                            }
                        )

                        if_none_match = request and request.headers.get(
                            self.if_none_header
                        )
                        if if_none_match == etag:
                            response.status_code = status.HTTP_304_NOT_MODIFIED
                            return response

                    result = cast(R, self.coder.decode(cached))

                return result

            inner.__signature__ = self._augment_signature(wrapped_signature, *to_inject)  # type: ignore
            return inner

        return wrapper
