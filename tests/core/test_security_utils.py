import pytest

from src.core.utils import security


@pytest.mark.asyncio
async def test_hash_and_verify_password_success() -> None:
    hashed = security.hash_password("strong-pass")

    assert await security.verify_password("strong-pass", hashed) is True


@pytest.mark.asyncio
async def test_verify_password_fail() -> None:
    hashed = security.hash_password("original")

    assert await security.verify_password("other", hashed) is False


def test_mask_email_valid_and_invalid() -> None:
    assert security.mask_email("user@example.com") == "us***@ex***"
    assert security.mask_email("bad-email") == "***"


def test_generate_otp_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security.random, "randint", lambda a, b: 12345)

    otp = security.generate_otp()

    assert otp == "12345"
    assert len(otp) == 5


def test_build_email_throttle_key_and_normalize() -> None:
    normalized = security.normalize_email("  USER@Example.COM  ")
    key = security.build_email_throttle_key("prefix", normalized)

    assert normalized == "user@example.com"
    assert key.startswith("prefix:")
    assert len(key.split(":", 1)[1]) == 64
