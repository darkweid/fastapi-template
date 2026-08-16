from pydantic import ValidationError
import pytest

from src.user.auth.schemas import CreateUserModel, UserNewPassword
from src.user.schemas import UserProfileUpdateModel

VALID_USER_KWARGS = dict(
    email="user@example.com",
    username="john.doe",
    phone_number="+12025550179",
    password="Str0ng!Passw0rd",
)


@pytest.mark.parametrize("name", ["John", "Anne-Marie", "O'Brien", "John Smith"])
def test_create_user_accepts_real_names(name: str) -> None:
    model = CreateUserModel(first_name=name, last_name=name, **VALID_USER_KWARGS)
    assert model.first_name == name


@pytest.mark.parametrize(
    "name", ["Ivan42", "-John", "John--Smith", "John  Smith", "J", "John "]
)
def test_create_user_rejects_malformed_names(name: str) -> None:
    with pytest.raises(ValidationError):
        CreateUserModel(first_name=name, last_name="Doe", **VALID_USER_KWARGS)


@pytest.mark.parametrize("name", ["Anne-Marie", "O'Brien"])
def test_profile_update_accepts_real_names(name: str) -> None:
    assert UserProfileUpdateModel(first_name=name).first_name == name


@pytest.mark.parametrize("name", ["Ivan42", "John--Smith"])
def test_profile_update_rejects_malformed_names(name: str) -> None:
    with pytest.raises(ValidationError):
        UserProfileUpdateModel(first_name=name)


def test_user_new_password_allows_printable_ascii_symbols_outside_old_whitelist() -> (
    None
):
    password = "Strong1~ "

    model = UserNewPassword(current_password="OldPass1!", password=password)

    assert model.password == password


def test_user_new_password_allows_maximum_length_boundary() -> None:
    password = "Aa1!" + ("x" * 124)

    model = UserNewPassword(current_password="OldPass1!", password=password)

    assert len(model.password) == 128


def test_user_new_password_rejects_password_longer_than_128_characters() -> None:
    password = "Aa1!" + ("x" * 125)

    with pytest.raises(ValidationError) as exc_info:
        UserNewPassword(current_password="OldPass1!", password=password)

    error_message = exc_info.value.errors()[0]["msg"]
    assert (
        error_message
        == "Value error, Password must be 8-128 characters long and contain at least one lowercase letter, one uppercase letter, one digit, and one non-alphanumeric non-space character. Printable ASCII characters are allowed."
    )


def test_user_new_password_rejects_non_ascii_characters() -> None:
    with pytest.raises(ValidationError):
        UserNewPassword(current_password="OldPass1!", password="Strong1!пароль")
