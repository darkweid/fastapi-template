from typing import Annotated, Final

from fastapi import Depends, Response

from loggers import get_logger
from src.core.errors.exceptions import AccessForbiddenException, InfrastructureException
from src.core.schemas import TokenModel
from src.main.config import Config, CookieConfig, get_settings
from src.user.auth.csrf import build_csrf_token, verify_csrf_token
from src.user.auth.token_transport import TokenTransport

logger = get_logger(__name__)

REFRESH_COOKIE_NAME: Final[str] = "refresh_token"
CSRF_COOKIE_NAME: Final[str] = "csrf_token"
CSRF_HEADER_NAME: Final[str] = "X-CSRF-Token"
# Scope the refresh cookie to the refresh endpoint only, so the browser never attaches
# it to ordinary API calls. Must stay in sync with where the auth router is mounted;
# a test pins this constant to the mounted route, because a mismatch breaks refresh
# silently - the browser simply does not send the cookie.
REFRESH_COOKIE_PATH: Final[str] = "/v1/users/auth/login/refresh"
# The CSRF cookie is deliberately site-wide. Per RFC 6265 path matching,
# `document.cookie` only exposes cookies whose Path is a prefix of the current
# document's path, so a same-origin SPA served at "/" could never read a cookie
# scoped to the refresh route - and an unreadable double-submit token turns every
# refresh into a 403. The value is a signature that is worthless without the
# httponly refresh cookie, so a wider path costs nothing. A test asserts the two
# paths differ, so this cannot silently collapse back.
CSRF_COOKIE_PATH: Final[str] = "/"

CSRF_FAILURE_MESSAGE: Final[str] = "CSRF validation failed"


class TokenCookieResponder:
    """
    Owns the auth cookie policy and the CSRF check bound to it.

    Holds the cookie names, path, lifetime and browser flags, so every place that
    writes or clears an auth cookie goes through one object with one policy.
    """

    def __init__(
        self,
        cookie_config: CookieConfig,
        refresh_token_expire_minutes: int,
    ) -> None:
        self._config = cookie_config
        self._max_age = refresh_token_expire_minutes * 60

    def apply(
        self,
        tokens: TokenModel,
        response: Response,
        transport: TokenTransport,
    ) -> TokenModel:
        """
        Shape the token response for the requested transport.

        For COOKIE the refresh token is written to an httponly cookie, its CSRF
        signature to a readable one and to the response body, and the refresh token
        is stripped from the body so that httponly actually means something. The CSRF
        token is echoed in the body because a cross-origin SPA cannot read an
        API-origin cookie from JS at any path. For BODY nothing is written to the
        response headers at all: a native client should never receive a cookie, and
        it needs no CSRF token because it authenticates with a header.
        """
        if transport is TokenTransport.BODY:
            return tokens

        refresh_token = tokens.refresh_token
        if refresh_token is None:
            raise InfrastructureException(
                "Cannot apply cookie transport without a refresh token"
            )

        csrf_token = build_csrf_token(refresh_token, self._config.CSRF_SECRET_KEY)
        self._set_cookie(
            response,
            REFRESH_COOKIE_NAME,
            refresh_token,
            path=REFRESH_COOKIE_PATH,
            httponly=True,
        )
        self._set_cookie(
            response,
            CSRF_COOKIE_NAME,
            csrf_token,
            path=CSRF_COOKIE_PATH,
            httponly=False,
        )
        return tokens.model_copy(
            update={"refresh_token": None, "csrf_token": csrf_token}
        )

    def clear(self, response: Response, transport: TokenTransport) -> None:
        """Expire both auth cookies. A no-op for clients that never received them."""
        if transport is TokenTransport.BODY:
            return

        self._set_cookie(
            response,
            REFRESH_COOKIE_NAME,
            "",
            path=REFRESH_COOKIE_PATH,
            httponly=True,
            max_age=0,
        )
        self._set_cookie(
            response,
            CSRF_COOKIE_NAME,
            "",
            path=CSRF_COOKIE_PATH,
            httponly=False,
            max_age=0,
        )

    def verify_csrf(self, refresh_token: str, provided: str | None) -> None:
        """
        Reject a cookie-borne refresh whose CSRF token is missing or wrong.

        Both failure modes return the same message: telling a caller which half of
        the pair was wrong only helps an attacker.
        """
        if verify_csrf_token(refresh_token, provided, self._config.CSRF_SECRET_KEY):
            return

        if provided is None:
            logger.debug("[CSRF] Refresh request from cookie carried no CSRF header.")
        else:
            logger.warning("[CSRF] Refresh request carried a mismatched CSRF token.")

        raise AccessForbiddenException(CSRF_FAILURE_MESSAGE)

    def _set_cookie(
        self,
        response: Response,
        key: str,
        value: str,
        *,
        path: str,
        httponly: bool,
        max_age: int | None = None,
    ) -> None:
        response.set_cookie(
            key=key,
            value=value,
            max_age=self._max_age if max_age is None else max_age,
            path=path,
            domain=self._config.COOKIE_DOMAIN,
            secure=self._config.COOKIE_SECURE,
            httponly=httponly,
            samesite=self._config.COOKIE_SAMESITE,
        )


def get_token_cookie_responder(
    settings: Annotated[Config, Depends(get_settings)],
) -> TokenCookieResponder:
    return TokenCookieResponder(
        cookie_config=settings.cookie,
        refresh_token_expire_minutes=settings.jwt.REFRESH_TOKEN_EXPIRE_MINUTES,
    )
