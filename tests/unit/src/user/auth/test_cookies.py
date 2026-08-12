from fastapi import Response
import pytest

from src.core.errors.exceptions import AccessForbiddenException
from src.core.schemas import TokenModel
from src.main.config import CookieConfig
from src.user.auth.cookies import (
    CSRF_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_PATH,
    TokenCookieResponder,
)
from src.user.auth.csrf import build_csrf_token
from src.user.auth.token_transport import TokenTransport

SECRET = "unit-test-csrf-secret"


@pytest.fixture
def responder() -> TokenCookieResponder:
    return TokenCookieResponder(
        cookie_config=CookieConfig(CSRF_SECRET_KEY=SECRET),
        refresh_token_expire_minutes=60,
    )


def _set_cookie_headers(response: Response) -> list[str]:
    return response.headers.getlist("set-cookie")


def test_cookie_transport_moves_refresh_out_of_the_body(
    responder: TokenCookieResponder,
) -> None:
    response = Response()

    result = responder.apply(
        TokenModel(access_token="a", refresh_token="r"), response, TokenTransport.COOKIE
    )

    assert result.access_token == "a"
    assert result.refresh_token is None


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
    assert f"Path={REFRESH_COOKIE_PATH}" in refresh_header
    assert f"Path={REFRESH_COOKIE_PATH}" in csrf_header
    assert "Max-Age=3600" in refresh_header
    assert "Secure" in refresh_header
    assert "SameSite=lax" in refresh_header


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
    assert _set_cookie_headers(response) == []


def test_clear_expires_both_cookies(responder: TokenCookieResponder) -> None:
    response = Response()

    responder.clear(response, TokenTransport.COOKIE)

    headers = _set_cookie_headers(response)
    assert len(headers) == 2
    assert all("Max-Age=0" in header for header in headers)


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
