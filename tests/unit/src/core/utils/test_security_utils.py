import pytest

from src.core.utils import security
from src.core.utils.security import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    is_password_hash,
    needs_password_rehash,
    verify_password,
)


async def test_hash_and_verify_roundtrip() -> None:
    hashed = await hash_password("S3cret!pass")
    assert hashed.startswith("$argon2")
    assert await verify_password("S3cret!pass", hashed) is True
    assert await verify_password("wrong", hashed) is False


async def test_verify_garbage_hash_returns_false() -> None:
    assert await verify_password("whatever", "not-a-hash") is False


async def test_is_password_hash() -> None:
    assert is_password_hash(await hash_password("S3cret!pass")) is True
    assert is_password_hash("plaintext") is False
    assert is_password_hash("$argon2garbage") is False


async def test_needs_rehash_on_weaker_parameters() -> None:
    from argon2 import PasswordHasher

    weak = PasswordHasher(memory_cost=8, time_cost=1, parallelism=1).hash("x")
    assert needs_password_rehash(weak) is True
    assert needs_password_rehash(await hash_password("x")) is False


def test_dummy_password_hash_is_real_argon2() -> None:
    assert DUMMY_PASSWORD_HASH.startswith("$argon2")


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


def test_build_email_throttle_key_and_normalize() -> None:
    normalized = security.normalize_email("  USER@Example.COM  ")
    key = security.build_email_throttle_key("prefix", normalized)

    assert normalized == "user@example.com"
    assert key.startswith("prefix:")
    assert len(key.split(":", 1)[1]) == 64
