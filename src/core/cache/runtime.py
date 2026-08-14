from src.core.cache.interface import Cache

_cache: Cache | None = None


def set_cache(cache: Cache) -> None:
    global _cache
    _cache = cache


def reset_cache() -> None:
    global _cache
    _cache = None


def get_cache_instance() -> Cache:
    # Decorators are applied at import time, long before the app starts, so they
    # resolve the cache through this holder instead of capturing an instance.
    if _cache is None:
        raise RuntimeError("Cache is not initialized. Ensure startup lifecycle ran.")
    return _cache
