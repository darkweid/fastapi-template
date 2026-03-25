from pydantic import ValidationError
import pytest

from src.user.auth.schemas import UserNewPassword


def test_user_new_password_allows_printable_ascii_symbols_outside_old_whitelist() -> (
    None
):
    password = "Strong1~ "

    model = UserNewPassword(password=password)

    assert model.password == password


def test_user_new_password_allows_maximum_length_boundary() -> None:
    password = "Aa1!" + ("x" * 124)

    model = UserNewPassword(password=password)

    assert len(model.password) == 128


def test_user_new_password_rejects_password_longer_than_128_characters() -> None:
    password = "Aa1!" + ("x" * 125)

    with pytest.raises(ValidationError) as exc_info:
        UserNewPassword(password=password)

    error_message = exc_info.value.errors()[0]["msg"]
    assert (
        error_message
        == "Value error, Password must be 8-128 characters long and contain at least one lowercase letter, one uppercase letter, one digit, and one non-alphanumeric non-space character. Printable ASCII characters are allowed."
    )


def test_user_new_password_rejects_non_ascii_characters() -> None:
    with pytest.raises(ValidationError):
        UserNewPassword(password="Strong1!пароль")
