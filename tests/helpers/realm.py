from src.core.auth.realm import AuthRealm

# Long enough to satisfy the config's secret length floor, and distinct per
# realm so a token minted for one can never verify against another.
_SECRET_TEMPLATE = "{name}-realm-test-secret-not-real-value"


def build_test_realm(name: str) -> AuthRealm:
    """A realm with no model, no router and no migration behind it.

    Isolation is a property of the realm plumbing, not of any one entity, so
    the tests that prove it should not need a second entity to exist.
    """
    return AuthRealm(
        name=name,
        session_secret=lambda: _SECRET_TEMPLATE.format(name=name),
        one_time_secrets={
            "verification": lambda: f"{name}-verification-secret-not-real-value",
        },
        refresh_cookie_path=f"/v1/{name}/auth/login/refresh",
    )
