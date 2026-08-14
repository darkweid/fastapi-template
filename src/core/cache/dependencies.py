from src.core.cache.interface import Cache
from src.core.cache.runtime import get_cache_instance


async def get_cache() -> Cache:
    """
    Provide the application-wide cache instance.
    """
    return get_cache_instance()
