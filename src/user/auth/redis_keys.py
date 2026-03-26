from typing import Literal

OneTimeTokenPurpose = Literal["verification", "reset_password"]


class AuthRedisKeyBuilder:
    """Build Redis keys and scan patterns for auth token storage."""

    def access(self, user_id: str, session_id: str) -> str:
        return f"access:{user_id}:{session_id}"

    def refresh(self, user_id: str, session_id: str) -> str:
        return f"refresh:{user_id}:{session_id}"

    def used(self, user_id: str, jti: str) -> str:
        return f"used:{user_id}:{jti}"

    def one_time_token(
        self,
        purpose: OneTimeTokenPurpose,
        normalized_email: str,
    ) -> str:
        return f"one-time:{purpose}:{normalized_email}"

    def access_pattern(self, user_id: str) -> str:
        return f"access:{user_id}:*"

    def refresh_pattern(self, user_id: str) -> str:
        return f"refresh:{user_id}:*"

    def used_pattern(self, user_id: str) -> str:
        return f"used:{user_id}:*"

    def all_user_patterns(self, user_id: str) -> tuple[str, str, str]:
        return (
            self.access_pattern(user_id),
            self.refresh_pattern(user_id),
            self.used_pattern(user_id),
        )

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
