import hashlib
import hmac


def build_csrf_token(refresh_token: str, secret: str) -> str:
    """
    Derive the CSRF token bound to a specific refresh token.

    The value is a keyed digest, so it cannot be forged without the server secret,
    and it is bound to one refresh token, so rotation automatically retires it.
    No server-side state is involved.
    """
    return hmac.new(
        secret.encode("utf-8"),
        refresh_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_csrf_token(refresh_token: str, provided: str | None, secret: str) -> bool:
    """
    Check a client-supplied CSRF token against the expected signature.

    Returns False for missing or empty input rather than raising, so that callers
    can map every failure mode onto a single opaque response.
    """
    if not provided:
        return False

    expected = build_csrf_token(refresh_token, secret)
    return hmac.compare_digest(expected, provided)
