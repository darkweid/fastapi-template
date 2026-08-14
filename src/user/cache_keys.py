from uuid import UUID

from fastapi import Request

from src.core.cache.interface import CacheKey


class UserCacheKeys:
    """Build cache keys for the user domain."""

    def namespace(self, user_id: UUID | str) -> str:
        # Coerced through UUID so every non-canonical spelling FastAPI accepts for a
        # `UUID` path param (uppercase, dash-less, braced, `urn:uuid:`) collapses to
        # the same namespace an invalidate() call addresses - the parse cannot raise
        # here because FastAPI has already validated the value as a UUID upstream.
        return f"user:{UUID(str(user_id))}"

    def summary(self, user_id: UUID | str) -> CacheKey:
        return CacheKey(namespace=self.namespace(user_id), suffix="summary")


user_cache_keys = UserCacheKeys()


def user_summary_route_key(request: Request, identity_id: str | None) -> CacheKey:
    return user_cache_keys.summary(request.path_params["user_id"])
