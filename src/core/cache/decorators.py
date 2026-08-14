from collections.abc import Awaitable, Callable
from functools import wraps
import hashlib
from inspect import signature
from typing import Annotated, Any, TypeVar, get_args, get_origin, get_type_hints

from fastapi import Request, Response, status

from src.core.cache.interface import CacheKey, CacheScope
from src.core.cache.runtime import get_cache_instance
from src.core.cache.serializer import JsonSerializer

R = TypeVar("R")

CACHE_STATUS_HEADER = "X-Cache-Status"
CACHE_CONTROL_HEADER = "Cache-Control"
ETAG_HEADER = "ETag"
IF_NONE_MATCH_HEADER = "If-None-Match"
VARY_HEADER = "Vary"

_serializer = JsonSerializer()

# Every ttl a decorator hardcodes, collected at import time. A decorator's ttl is a
# literal, so it cannot be checked against CACHE_VERSION_TTL where it is written -
# the cache does not exist yet. Startup checks the collected ttls instead, because
# the alternative is a ValueError raised on every cache miss, i.e. a route that
# returns 500 under a configuration that looked valid.
_declared_ttls: list[tuple[str, int]] = []


def validate_declared_ttls(version_ttl: int) -> None:
    offenders = [
        f"{name} (ttl={ttl})" for name, ttl in _declared_ttls if ttl > version_ttl
    ]
    if offenders:
        raise ValueError(
            f"Cached callables declare a ttl above CACHE_VERSION_TTL "
            f"({version_ttl}s): {', '.join(offenders)}. Values must die before "
            "the version counters that address them."
        )


def _return_model(func: Callable[..., Any]) -> Any:
    # Not always a bare `type`: a return annotation like `list[Summary]` or
    # `Summary | None` is a generic alias / union, not a class - TypeAdapter
    # accepts all of those, so the honest signature here is Any, not type[Any].
    hints = get_type_hints(func)
    model = hints.get("return")
    if model is None:
        raise TypeError(
            f"{func.__qualname__} needs a return annotation: the cache decodes "
            "cached payloads into that type."
        )
    return model


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
        _declared_ttls.append((func.__qualname__, ttl))

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
    # include_extras keeps Annotated[X, ...] wrappers so DI-style parameters
    # (Annotated[Request, Depends(...)]) still resolve to their inner type, and
    # get_type_hints (rather than raw Parameter.annotation) resolves string
    # annotations produced by `from __future__ import annotations`.
    hints = get_type_hints(func, include_extras=True)
    for name in signature(func).parameters:
        hint = hints.get(name)
        if hint is None:
            continue
        if get_origin(hint) is Annotated:
            hint = get_args(hint)[0]
        if isinstance(hint, type) and issubclass(hint, annotation):
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
    key_builder: Callable[[Request], CacheKey],
    ttl: int,
    scope: CacheScope,
    identity: Callable[[Request], Awaitable[str | None]] | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """
    Cache an endpoint response with ETag and 304 support.

    The endpoint declares request and response itself; PRIVATE scope requires an
    identity callback so that one user's response can never be served to another.
    The identity is appended to the key by this decorator rather than by
    key_builder, so a builder cannot forget it. If the callback resolves to None
    for a given request, the cache is bypassed entirely for that call rather than
    risk collapsing distinct, unidentifiable callers onto one shared entry.

    Limitations:
    - Incompatible with `response_model_exclude_unset` /
      `response_model_exclude_defaults` on the decorated route - the decorator has
      no visibility into those flags, and the cached payload is always a full model
      dump, so a value served from the cache reports every field as set.
    - Only the body is cached: a status code or a header the endpoint writes onto
      `response` (an `X-Total-Count` on a paginated list, say) is produced on a
      miss and lost on a hit. An endpoint whose metadata matters must set it
      outside the cached function or not use this decorator.
    """
    if scope is CacheScope.PRIVATE and identity is None:
        raise ValueError("CacheScope.PRIVATE requires an identity callback.")

    def wrapper(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        request_param = _find_parameter(func, Request)
        response_param = _find_parameter(func, Response)
        _declared_ttls.append((func.__qualname__, ttl))

        @wraps(func)
        async def inner(*args: Any, **kwargs: Any) -> Any:
            request: Request = kwargs[request_param]
            response: Response = kwargs[response_param]

            identity_id = await identity(request) if identity else None
            if scope is CacheScope.PRIVATE and identity_id is None:
                # No identity means no safe cache key: every caller the identity
                # callback cannot resolve would otherwise collapse onto the same
                # namespace-less entry and receive each other's private response.
                return await func(*args, **kwargs)

            cache = get_cache_instance()
            key = key_builder(request)
            if scope is CacheScope.PRIVATE:
                # The identity belongs to the key, and this is the only place that
                # can guarantee it is there: a key_builder that took the identity
                # and ignored it would serve one caller's private body to every
                # other caller, with a matching ETag, and nothing would raise.
                key = CacheKey(key.namespace, f"{key.suffix}:{identity_id}")

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
            if scope is CacheScope.PUBLIC:
                # A PUBLIC response of an authorization-gated route is still keyed
                # by credentials for any shared cache on the path: without this a
                # CDN may replay it to a caller who sent a different Authorization
                # header, or none at all.
                headers[VARY_HEADER] = "Authorization"
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
