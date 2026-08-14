from uuid import UUID

from fastapi import Request

from src.core.cache.interface import CacheKey

# Every user entry carries this tag, so a write that touches many users at once -
# a bulk import, a role migration - flushes all of them with a single
# `cache.invalidate_tags(USER_CACHE_TAG)` instead of one call per namespace.
USER_CACHE_TAG = "users"


class UserCacheKeys:
    """Build cache keys for the user domain."""

    def namespace(self, user_id: UUID | str) -> str:
        # Coerced through UUID so every non-canonical spelling FastAPI accepts for a
        # `UUID` path param (uppercase, dash-less, braced, `urn:uuid:`) collapses to
        # the same namespace an invalidate() call addresses - the parse cannot raise
        # here because FastAPI has already validated the value as a UUID upstream.
        return f"user:{UUID(str(user_id))}"

    def summary(self, user_id: UUID | str) -> CacheKey:
        return CacheKey(
            namespace=self.namespace(user_id),
            suffix="summary",
            tags=(USER_CACHE_TAG,),
        )


user_cache_keys = UserCacheKeys()


def user_summary_route_key(request: Request) -> CacheKey:
    return user_cache_keys.summary(request.path_params["user_id"])
