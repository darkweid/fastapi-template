from src.core.utils.security import build_throttle_key


class AuthRedisKeyBuilder:
    """Build Redis keys for one auth realm's token storage.

    Every key carries the realm prefix, so two realms can never collide - and
    one realm's session wipe can never reach another realm's keys.
    """

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix

    def access(self, subject_id: str, session_id: str) -> str:
        return f"{self._prefix}:access:{subject_id}:{session_id}"

    def refresh(self, subject_id: str, session_id: str) -> str:
        return f"{self._prefix}:refresh:{subject_id}:{session_id}"

    def used(self, subject_id: str, jti: str) -> str:
        return f"{self._prefix}:used:{subject_id}:{jti}"

    def sessions(self, subject_id: str) -> str:
        """ZSET of the subject's session ids, scored by refresh-lifetime expiry -
        the index a wipe walks instead of a keyspace SCAN. A superset of live
        sessions: stale members are pruned on the next token issuance."""
        return f"{self._prefix}:sessions:{subject_id}"

    def login_failures(self, identifier: str) -> str:
        """Window-scoped counter of failed logins for one login identifier."""
        return f"{self._prefix}:{build_throttle_key('login-fail', identifier)}"

    def one_time(self, purpose: str, identifier: str) -> str:
        """The single active challenge for one purpose and one identifier."""
        return f"{self._prefix}:{build_throttle_key(f'one-time:{purpose}', identifier)}"

    def session_key(self, mode: str, subject_id: str, session_id: str) -> str:
        """Dispatch to the access or refresh key for one session.

        `mode` is a session-token mode ("access_token"/"refresh_token"), not
        the wider `JWTPayload.mode`; the caller (`credentials.verify_jti`)
        already rejects any other value before reaching here.
        """
        if mode == "access_token":
            return self.access(subject_id, session_id)
        return self.refresh(subject_id, session_id)
