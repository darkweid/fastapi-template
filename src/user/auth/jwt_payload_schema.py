from typing import Literal, NotRequired, TypedDict


class JWTPayload(TypedDict):
    """Type definition for JWT token payload"""

    sub: str  # User ID
    exp: int  # Expiration timestamp
    mode: Literal[
        "access_token", "refresh_token", "verification_token", "reset_password_token"
    ]
    jti: NotRequired[str]  # JWT ID for token tracking
    session_id: NotRequired[str]  # Session identifier
    family: NotRequired[str]  # Token family for rotation tracking
