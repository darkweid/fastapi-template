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
    digits = iter(["0", "0", "1", "2", "3"])
    monkeypatch.setattr(security.secrets, "choice", lambda _: next(digits))

    otp = security.generate_otp(5)

    assert otp == "00123"
    assert len(otp) == 5


def test_generate_otp_invalid_length() -> None:
    with pytest.raises(ValueError, match="OTP length must be greater than 0, given 0."):
        security.generate_otp(0)


def test_is_password_hash_recognizes_hash() -> None:
    hashed = security.hash_password("strong-pass")

    assert security.is_password_hash(hashed) is True
    assert security.is_password_hash("plain-password") is False


def test_needs_password_rehash_false_for_fresh_hash() -> None:
    hashed = security.hash_password("strong-pass")

    assert security.needs_password_rehash(hashed) is False


def test_needs_password_rehash_true_when_context_requires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(security.pwd_context, "needs_update", lambda _: True)

    assert security.needs_password_rehash("any-hash") is True


def test_build_email_throttle_key_and_normalize() -> None:
    normalized = security.normalize_email("  USER@Example.COM  ")
    key = security.build_email_throttle_key("prefix", normalized)

    assert normalized == "user@example.com"
    assert key.startswith("prefix:")
    assert len(key.split(":", 1)[1]) == 64
