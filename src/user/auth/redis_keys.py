from typing import Literal

from src.core.utils.security import build_email_throttle_key

OneTimeTokenPurpose = Literal["verification", "reset_password"]


class AuthRedisKeyBuilder:
    """Build Redis keys and scan patterns for auth token storage."""

    def access(self, user_id: str, session_id: str) -> str:
        return f"access:{user_id}:{session_id}"

    def refresh(self, user_id: str, session_id: str) -> str:
        return f"refresh:{user_id}:{session_id}"

    def used(self, user_id: str, jti: str) -> str:
        return f"used:{user_id}:{jti}"

    def sessions(self, user_id: str) -> str:
        """ZSET of the user's session ids, scored by refresh-lifetime expiry -
        the index a wipe walks instead of a keyspace SCAN. A superset of live
        sessions: stale members are pruned on the next token issuance."""
        return f"sessions:{user_id}"

    def login_failures(self, normalized_email: str) -> str:
        """Window-scoped counter of failed logins for one email. Keyed by the
        address's hash so SCAN/MONITOR/RDB dumps never expose raw emails."""
        return build_email_throttle_key("login-fail", normalized_email)

    def one_time_token(
        self,
        purpose: OneTimeTokenPurpose,
        normalized_email: str,
    ) -> str:
        return f"one-time:{purpose}:{normalized_email}"

    def session_key(
        self,
        mode: Literal["access_token", "refresh_token"],
        user_id: str,
        session_id: str,
    ) -> str:
        if mode == "access_token":
            return self.access(user_id, session_id)
        return self.refresh(user_id, session_id)


auth_redis_keys = AuthRedisKeyBuilder()
