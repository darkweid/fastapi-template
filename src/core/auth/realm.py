from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cached_property

from src.core.auth.redis_keys import AuthRedisKeyBuilder
from src.core.errors.exceptions import InfrastructureException


# eq=False keeps the dataclass hashable by identity: the generated __eq__ would
# force a __hash__ over the fields, and one of them is a mapping.
@dataclass(frozen=True, eq=False)
class AuthRealm:
    """Everything that distinguishes one authentication contour from another.

    A realm binds the signing secrets, the Redis key namespace and the cookie
    contract of one class of principals. Core auth code never names a concrete
    realm - it receives one.
    """

    name: str
    session_secret: Callable[[], str]
    refresh_cookie_path: str
    one_time_secrets: Mapping[str, Callable[[], str]] = field(default_factory=dict)
    refresh_cookie_name: str | None = None
    csrf_cookie_name: str | None = None

    @cached_property
    def keys(self) -> AuthRedisKeyBuilder:
        return AuthRedisKeyBuilder(self.name)

    @property
    def refresh_cookie(self) -> str:
        return self.refresh_cookie_name or f"{self.name}_refresh_token"

    @property
    def csrf_cookie(self) -> str:
        return self.csrf_cookie_name or f"{self.name}_csrf_token"

    @property
    def secret(self) -> str:
        # Read at call time, not at construction: tests monkeypatch config.jwt,
        # and an import-time capture would freeze the placeholder value.
        return self.session_secret()

    def one_time_secret(self, purpose: str) -> str:
        """The signing secret for one class of single-use token.

        An unregistered purpose is a realm misconfiguration, not user input:
        the caller asked for a channel this contour never declared.
        """
        getter = self.one_time_secrets.get(purpose)
        if getter is None:
            raise InfrastructureException(
                f"Realm '{self.name}' declares no secret for purpose '{purpose}'"
            )
        return getter()
