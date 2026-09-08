from typing import NotRequired, TypedDict


class JWTPayload(TypedDict):
    """Type definition for JWT token payload"""

    sub: str  # User ID
    exp: int  # Expiration timestamp
    # Not a closed Literal: session tokens (access_token/refresh_token) are
    # core concepts and are checked against those literals at runtime where
    # it matters (src/core/auth/credentials.py, src/core/auth/dependencies.py).
    # Single-use purposes (verification_token, reset_password_token, and
    # whatever a new realm declares) are realm-defined strings core never
    # enumerates - see src/core/auth/one_time_tokens.py.
    mode: str
    jti: NotRequired[str]  # JWT ID for token tracking
    session_id: NotRequired[str]
