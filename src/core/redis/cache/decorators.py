from src.core.redis.cache.backend.redis_backend import RedisCacheBackend
from src.core.redis.cache.coder.pickle_coder import PickleCoder
from src.core.redis.cache.manager.manager import CacheManager
from src.core.redis.cache.manager.route_manager import RouteCacheManager

redis_backend = RedisCacheBackend()
coder = PickleCoder()
cache_route = RouteCacheManager(backend=redis_backend, coder=coder)
cache = CacheManager(backend=redis_backend, coder=coder)
