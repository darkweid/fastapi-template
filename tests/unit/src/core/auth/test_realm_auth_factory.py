import pytest
from sqlalchemy.orm import Session, make_transient_to_detached

from src.core.auth.dependencies import build_realm_auth, load_principal
from src.core.auth.realm import AuthRealm
from src.core.auth.tokens import create_access_token
from src.core.errors.exceptions import UnauthorizedException
from src.user.models import User
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession


class FakePrincipalRepository:
    def __init__(self, principal: User | None = None) -> None:
        self._principal = principal

    async def get_single(self, session: object, **filters: object) -> User | None:
        return self._principal


def attach(principal: User) -> Session:
    """Make the principal persistent, the state a loaded row is in, without a
    database: a detached instance added to a session is persistent at once.
    The caller holds the returned session open around the call under test,
    since an instance whose session is closed is no longer persistent."""
    make_transient_to_detached(principal)
    holder = Session()
    holder.add(principal)
    return holder


FIRST_REALM = AuthRealm(
    name="first",
    session_secret=lambda: "first-realm-secret-not-real-32-chars",
    refresh_cookie_path="/v1/first/auth/login/refresh",
)
SECOND_REALM = AuthRealm(
    name="second",
    session_secret=lambda: "second-realm-secret-not-real-32-char",
    refresh_cookie_path="/v1/second/auth/login/refresh",
)


async def test_a_token_minted_for_one_realm_is_rejected_by_another(
    fake_redis: object,
) -> None:
    principal = build_user()
    second_auth = build_realm_auth(
        realm=SECOND_REALM,
        principal_type=User,
        repository_factory=lambda: FakePrincipalRepository(principal),
        admission=lambda _principal: None,
    )
    token = await create_access_token(
        {"sub": str(principal.id)}, fake_redis, realm=FIRST_REALM, session_id="s1"
    )

    with pytest.raises(UnauthorizedException):
        await second_auth.authenticate(
            token=token, session=FakeAsyncSession(), redis_client=fake_redis
        )


async def test_a_token_minted_for_one_realm_is_accepted_by_that_same_realm(
    fake_redis: object,
) -> None:
    """Pins the positive half of the cross-realm check.

    Without this, a `verify_jti` regression that ignored `realm` entirely and
    read a hardcoded secret would still pass the rejection test above - the
    token would simply be signed with neither realm's secret. Only a case
    that must succeed against its own realm can prove the realm's secret is
    the one actually used.
    """
    principal = build_user()
    first_auth = build_realm_auth(
        realm=FIRST_REALM,
        principal_type=User,
        repository_factory=lambda: FakePrincipalRepository(principal),
        admission=lambda _principal: None,
    )
    token = await create_access_token(
        {"sub": str(principal.id)}, fake_redis, realm=FIRST_REALM, session_id="s1"
    )

    authenticated = await first_auth.authenticate(
        token=token, session=FakeAsyncSession(), redis_client=fake_redis
    )

    assert authenticated.principal is principal
    assert authenticated.session_id == "s1"


async def test_load_principal_ends_the_read_transaction_it_opened() -> None:
    """Left open, that transaction pins a pooled connection idle in transaction
    for the rest of the request, however long the handler then waits."""
    principal = build_user()
    session = FakeAsyncSession()

    with attach(principal):
        loaded = await load_principal(session, FakePrincipalRepository(principal), "42")

    assert loaded is principal
    session.expunge.assert_called_once_with(principal)
    session.rollback.assert_awaited_once()


async def test_load_principal_leaves_a_transaction_it_did_not_open() -> None:
    """Rolling back someone else's transaction would discard their work."""
    principal = build_user()
    session = FakeAsyncSession(in_transaction=True)

    with attach(principal):
        await load_principal(session, FakePrincipalRepository(principal), "42")

    session.expunge.assert_called_once_with(principal)
    session.rollback.assert_not_awaited()


async def test_load_principal_releases_the_connection_for_an_unknown_subject() -> None:
    session = FakeAsyncSession()

    loaded = await load_principal(session, FakePrincipalRepository(None), "42")

    assert loaded is None
    session.expunge.assert_not_called()
    session.rollback.assert_awaited_once()
