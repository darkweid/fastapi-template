from collections.abc import Awaitable, Callable
from functools import wraps
import hashlib
from inspect import signature
from typing import Any, TypeVar, cast, get_type_hints

from fastapi import Request, Response, status

from loggers import get_logger
from src.core.cache.interface import CacheKey, CacheScope
from src.core.cache.runtime import get_cache_instance
from src.core.cache.serializer import JsonSerializer

logger = get_logger(__name__)

R = TypeVar("R")

CACHE_STATUS_HEADER = "X-Cache-Status"
CACHE_CONTROL_HEADER = "Cache-Control"
ETAG_HEADER = "ETag"
IF_NONE_MATCH_HEADER = "If-None-Match"

_serializer = JsonSerializer()


def _return_model(func: Callable[..., Any]) -> type[Any]:
    hints = get_type_hints(func)
    model = hints.get("return")
    if model is None:
        raise TypeError(
            f"{func.__qualname__} needs a return annotation: the cache decodes "
            "cached payloads into that type."
        )
    return cast(type[Any], model)


def cached(
    *,
    key_builder: Callable[..., CacheKey],
    ttl: int,
) -> Callable[[Callable[..., Awaitable[R]]], Callable[..., Awaitable[R]]]:
    """
    Cache an async function result.

    key_builder is called with exactly the arguments the wrapped function
    received - for a method that includes self.
    """

    def wrapper(func: Callable[..., Awaitable[R]]) -> Callable[..., Awaitable[R]]:
        model = _return_model(func)

        @wraps(func)
        async def inner(*args: Any, **kwargs: Any) -> R:
            cache = get_cache_instance()
            key = key_builder(*args, **kwargs)
            return await cache.get_or_set(
                key,
                lambda: func(*args, **kwargs),
                ttl=ttl,
                model=model,
            )

        return inner

    return wrapper


def _find_parameter(func: Callable[..., Any], annotation: type[Any]) -> str:
    for name, parameter in signature(func).parameters.items():
        if parameter.annotation is annotation:
            return name
    raise TypeError(
        f"{func.__qualname__} must declare a parameter annotated as "
        f"{annotation.__name__}: the cache decorator reads it instead of "
        "patching the endpoint signature."
    )


def _build_etag(payload: str) -> str:
    return f'W/"{hashlib.sha256(payload.encode()).hexdigest()}"'


def _matches_if_none_match(header: str | None, etag: str) -> bool:
    if header is None:
        return False
    normalized_etag = etag.removeprefix("W/")
    for candidate in header.split(","):
        normalized = candidate.strip().removeprefix("W/")
        if normalized in ("*", normalized_etag):
            return True
    return False


def cached_route(
    *,
    key_builder: Callable[[Request, str | None], CacheKey],
    ttl: int,
    scope: CacheScope,
    identity: Callable[[Request], Awaitable[str | None]] | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """
    Cache an endpoint response with ETag and 304 support.

    The endpoint declares request and response itself; PRIVATE scope requires an
    identity callback so that one user's response can never be served to another.
    """
    if scope is CacheScope.PRIVATE and identity is None:
        raise ValueError("CacheScope.PRIVATE requires an identity callback.")

    def wrapper(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        request_param = _find_parameter(func, Request)
        response_param = _find_parameter(func, Response)

        @wraps(func)
        async def inner(*args: Any, **kwargs: Any) -> Any:
            cache = get_cache_instance()
            request: Request = kwargs[request_param]
            response: Response = kwargs[response_param]

            identity_id = await identity(request) if identity else None
            key = key_builder(request, identity_id)

            raw = await cache.get_raw(key)
            cache_status = "HIT"
            if raw is None:
                cache_status = "MISS"
                result = await func(*args, **kwargs)
                raw = _serializer.dumps(result)
                await cache.set_raw(key, raw, ttl=ttl)

            etag = _build_etag(raw)
            headers = {
                CACHE_CONTROL_HEADER: f"{scope.value}, max-age={ttl}",
                ETAG_HEADER: etag,
                CACHE_STATUS_HEADER: cache_status,
            }
            response.headers.update(headers)

            if _matches_if_none_match(request.headers.get(IF_NONE_MATCH_HEADER), etag):
                return Response(
                    status_code=status.HTTP_304_NOT_MODIFIED, headers=headers
                )

            # The parsed payload is returned, not a model instance - FastAPI runs it
            # through response_model, so a hit and a miss produce an identical body.
            return _serializer.loads(raw)

        # Marks the wrapper so a test can detect @cached_route applied outside
        # @router.get, where functools.wraps would otherwise hide the mistake.
        inner.__cached_route__ = True  # type: ignore[attr-defined]

        return inner

    return wrapper
