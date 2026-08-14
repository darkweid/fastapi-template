from fastapi import Response
import pytest

from src.core.errors.exceptions import AccessForbiddenException, InfrastructureException
from src.core.schemas import TokenModel
from src.main.config import CookieConfig
from src.user.auth.cookies import (
    CSRF_COOKIE_NAME,
    CSRF_COOKIE_PATH,
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_PATH,
    TokenCookieResponder,
)
from src.user.auth.csrf import build_csrf_token
from src.user.auth.token_transport import TokenTransport

SECRET = "unit-test-csrf-secret-key-value-32"


@pytest.fixture
def responder() -> TokenCookieResponder:
    return TokenCookieResponder(
        cookie_config=CookieConfig(CSRF_SECRET_KEY=SECRET),
        refresh_token_expire_minutes=60,
    )


def _set_cookie_headers(response: Response) -> list[str]:
    return response.headers.getlist("set-cookie")


def _cookie_path(set_cookie_header: str) -> str:
    """Read the exact Path attribute.

    Substring matching would not do: `Path=/` is a prefix of `Path=/v1/users/...`,
    so an `in` check cannot tell the site-wide path from the refresh-scoped one.
    """
    for attribute in set_cookie_header.split(";"):
        name, separator, value = attribute.strip().partition("=")
        if separator and name.lower() == "path":
            return value

    raise AssertionError(f"No Path attribute in {set_cookie_header!r}")


def test_cookie_transport_moves_refresh_out_of_the_body(
    responder: TokenCookieResponder,
) -> None:
    response = Response()

    result = responder.apply(
        TokenModel(access_token="a", refresh_token="r"), response, TokenTransport.COOKIE
    )

    assert result.access_token == "a"
    assert result.refresh_token is None


def test_cookie_transport_without_a_refresh_token_raises_infrastructure_exception(
    responder: TokenCookieResponder,
) -> None:
    response = Response()
    # Simulates the invariant being violated upstream: a use case that returned no
    # refresh token at all. TokenModel.refresh_token is `str | None`, so this is a
    # plain construction - the model itself cannot catch the mistake.
    tokens = TokenModel(access_token="a")

    with pytest.raises(InfrastructureException):
        responder.apply(tokens, response, TokenTransport.COOKIE)

    assert _set_cookie_headers(response) == []


def test_cookie_transport_sets_both_cookies(
    responder: TokenCookieResponder,
) -> None:
    response = Response()

    responder.apply(
        TokenModel(access_token="a", refresh_token="r"), response, TokenTransport.COOKIE
    )

    headers = _set_cookie_headers(response)
    refresh_header = next(h for h in headers if h.startswith(f"{REFRESH_COOKIE_NAME}="))
    csrf_header = next(h for h in headers if h.startswith(f"{CSRF_COOKIE_NAME}="))

    assert "HttpOnly" in refresh_header
    assert "HttpOnly" not in csrf_header
    assert _cookie_path(refresh_header) == REFRESH_COOKIE_PATH
    assert _cookie_path(csrf_header) == CSRF_COOKIE_PATH
    assert "Max-Age=3600" in refresh_header
    assert "Secure" in refresh_header
    assert "SameSite=lax" in refresh_header


def test_csrf_cookie_is_not_scoped_to_the_refresh_path(
    responder: TokenCookieResponder,
) -> None:
    # A CSRF cookie scoped to the refresh route is unreadable from `document.cookie`
    # on any SPA served elsewhere (RFC 6265 path matching), which would make every
    # browser refresh a 403. The two paths must stay distinct.
    assert CSRF_COOKIE_PATH != REFRESH_COOKIE_PATH
    assert CSRF_COOKIE_PATH == "/"

    response = Response()
    responder.apply(
        TokenModel(access_token="a", refresh_token="r"), response, TokenTransport.COOKIE
    )

    headers = _set_cookie_headers(response)
    refresh_header = next(h for h in headers if h.startswith(f"{REFRESH_COOKIE_NAME}="))
    csrf_header = next(h for h in headers if h.startswith(f"{CSRF_COOKIE_NAME}="))

    assert _cookie_path(csrf_header) != _cookie_path(refresh_header)
    assert _cookie_path(csrf_header) == CSRF_COOKIE_PATH


def test_cookie_transport_returns_the_csrf_token_in_the_body(
    responder: TokenCookieResponder,
) -> None:
    # A cross-origin SPA cannot read the API-origin csrf_token cookie from JS at any
    # path, so the body is its only source for the value it must echo back.
    response = Response()

    result = responder.apply(
        TokenModel(access_token="a", refresh_token="r"), response, TokenTransport.COOKIE
    )

    assert result.csrf_token == build_csrf_token("r", SECRET)


def test_csrf_cookie_carries_the_signature_of_the_refresh_token(
    responder: TokenCookieResponder,
) -> None:
    response = Response()

    responder.apply(
        TokenModel(access_token="a", refresh_token="r"), response, TokenTransport.COOKIE
    )

    csrf_header = next(
        h for h in _set_cookie_headers(response) if h.startswith(f"{CSRF_COOKIE_NAME}=")
    )
    assert build_csrf_token("r", SECRET) in csrf_header


def test_body_transport_sets_no_cookies_and_keeps_the_token(
    responder: TokenCookieResponder,
) -> None:
    response = Response()

    result = responder.apply(
        TokenModel(access_token="a", refresh_token="r"), response, TokenTransport.BODY
    )

    assert result.refresh_token == "r"
    assert result.csrf_token is None
    assert _set_cookie_headers(response) == []


def test_clear_expires_both_cookies(responder: TokenCookieResponder) -> None:
    response = Response()

    responder.clear(response, TokenTransport.COOKIE)

    headers = _set_cookie_headers(response)
    assert len(headers) == 2
    assert all("Max-Age=0" in header for header in headers)

    refresh_header = next(h for h in headers if h.startswith(f"{REFRESH_COOKIE_NAME}="))
    csrf_header = next(h for h in headers if h.startswith(f"{CSRF_COOKIE_NAME}="))
    # A cookie is only expired by a Set-Cookie carrying the same path it was set with.
    assert _cookie_path(refresh_header) == REFRESH_COOKIE_PATH
    assert _cookie_path(csrf_header) == CSRF_COOKIE_PATH


def test_clear_is_a_no_op_for_body_transport(responder: TokenCookieResponder) -> None:
    response = Response()

    responder.clear(response, TokenTransport.BODY)

    assert _set_cookie_headers(response) == []


def test_verify_csrf_accepts_a_valid_pair(responder: TokenCookieResponder) -> None:
    responder.verify_csrf("r", build_csrf_token("r", SECRET))


def test_verify_csrf_rejects_a_missing_header(responder: TokenCookieResponder) -> None:
    with pytest.raises(AccessForbiddenException):
        responder.verify_csrf("r", None)


def test_verify_csrf_rejects_a_wrong_signature(
    responder: TokenCookieResponder,
) -> None:
    with pytest.raises(AccessForbiddenException):
        responder.verify_csrf("r", build_csrf_token("other", SECRET))
