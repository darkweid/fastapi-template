from uuid import UUID

from fastapi import Request

from src.core.cache.interface import CacheKey


class UserCacheKeys:
    """Build cache keys for the user domain."""

    def namespace(self, user_id: UUID | str) -> str:
        return f"user:{user_id}"

    def summary(self, user_id: UUID | str) -> CacheKey:
        return CacheKey(namespace=self.namespace(user_id), suffix="summary")


user_cache_keys = UserCacheKeys()


def user_summary_route_key(request: Request, identity_id: str | None) -> CacheKey:
    return user_cache_keys.summary(request.path_params["user_id"])
