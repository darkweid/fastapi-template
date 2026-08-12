from pydantic import ValidationError
import pytest

from src.main.config import CookieConfig


def test_cookie_config_defaults_are_secure() -> None:
    cookie_config = CookieConfig(CSRF_SECRET_KEY="unit-test-secret")

    assert cookie_config.COOKIE_SECURE is True
    assert cookie_config.COOKIE_SAMESITE == "lax"
    assert cookie_config.COOKIE_DOMAIN is None


def test_cookie_config_requires_csrf_secret() -> None:
    with pytest.raises(ValidationError):
        CookieConfig()


def test_cookie_config_rejects_unknown_samesite() -> None:
    with pytest.raises(ValidationError):
        CookieConfig(CSRF_SECRET_KEY="unit-test-secret", COOKIE_SAMESITE="sometimes")


def test_cookie_config_rejects_samesite_none_without_secure() -> None:
    # Browsers drop a SameSite=None cookie that is not Secure, and they do it
    # silently. Failing at startup is the only way the operator ever finds out.
    with pytest.raises(ValidationError, match="COOKIE_SAMESITE=none requires"):
        CookieConfig(
            CSRF_SECRET_KEY="unit-test-secret",
            COOKIE_SAMESITE="none",
            COOKIE_SECURE=False,
        )


def test_cookie_config_allows_samesite_none_with_secure() -> None:
    cookie_config = CookieConfig(
        CSRF_SECRET_KEY="unit-test-secret",
        COOKIE_SAMESITE="none",
        COOKIE_SECURE=True,
    )

    assert cookie_config.COOKIE_SAMESITE == "none"
    assert cookie_config.COOKIE_SECURE is True


def test_cookie_config_allows_insecure_cookies_for_local_http() -> None:
    # .env.test relies on this: the ASGI client talks plain http, so COOKIE_SECURE
    # is false there. Only the samesite=none combination is forbidden.
    cookie_config = CookieConfig(
        CSRF_SECRET_KEY="unit-test-secret",
        COOKIE_SAMESITE="lax",
        COOKIE_SECURE=False,
    )

    assert cookie_config.COOKIE_SECURE is False


def test_cookie_config_normalizes_empty_domain_to_none() -> None:
    cookie_config = CookieConfig(CSRF_SECRET_KEY="unit-test-secret", COOKIE_DOMAIN="")

    assert cookie_config.COOKIE_DOMAIN is None
