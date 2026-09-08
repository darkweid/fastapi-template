from typing import Final

from src.core.auth.realm import AuthRealm
from src.main.config import config

VERIFICATION_PURPOSE: Final[str] = "verification"
RESET_PASSWORD_PURPOSE: Final[str] = "reset_password"

USER_AUTH_REALM = AuthRealm(
    name="user",
    session_secret=lambda: config.jwt.JWT_USER_SECRET_KEY,
    one_time_secrets={
        VERIFICATION_PURPOSE: lambda: config.jwt.JWT_USER_VERIFY_SECRET_KEY,
        RESET_PASSWORD_PURPOSE: lambda: config.jwt.JWT_USER_RESET_PASSWORD_SECRET_KEY,
    },
    # Must stay in sync with where the auth router is mounted: a mismatch breaks
    # browser refresh silently, because the cookie is simply not sent. Pinned by
    # a test in tests/unit/src/user/auth/test_token_transport.py.
    refresh_cookie_path="/v1/users/auth/login/refresh",
)
