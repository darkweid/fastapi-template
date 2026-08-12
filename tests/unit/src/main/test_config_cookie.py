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


def test_cookie_config_normalizes_empty_domain_to_none() -> None:
    cookie_config = CookieConfig(CSRF_SECRET_KEY="unit-test-secret", COOKIE_DOMAIN="")

    assert cookie_config.COOKIE_DOMAIN is None
