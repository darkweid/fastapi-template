from pydantic import EmailStr, Field, field_validator

from src.core.schemas import (
    Base,
    EmailNormalizationMixin,
    OneTimeToken,
    Password,
    StrongPasswordValidationMixin,
)
from src.core.validations import (
    FULL_NAME_PATTERN,
    PHONE_NUMBER_PATTERN,
    USERNAME_VALIDATOR,
)


class CreateUserModel(StrongPasswordValidationMixin, EmailNormalizationMixin, Base):
    first_name: str = Field(min_length=2, max_length=30)
    last_name: str = Field(min_length=2, max_length=30)
    email: EmailStr
    # The validators below hold the real shape; the lengths publish the
    # ceiling and refuse an oversized value before any regex runs.
    username: str = Field(max_length=60)
    phone_number: str = Field(max_length=16)
    password: Password

    @field_validator("first_name")
    @classmethod
    def validate_first_name(cls, value: str) -> str:
        if not FULL_NAME_PATTERN.match(value):
            raise ValueError(
                "First name must contain latin letters, with single spaces, "
                "hyphens or apostrophes between parts"
            )
        return value

    @field_validator("last_name")
    @classmethod
    def validate_last_name(cls, value: str) -> str:
        if not FULL_NAME_PATTERN.match(value):
            raise ValueError(
                "Last name must contain latin letters, with single spaces, "
                "hyphens or apostrophes between parts"
            )
        return value

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, value: str) -> str:
        if not PHONE_NUMBER_PATTERN.match(value):
            raise ValueError(
                "Phone number must be in E.164 format: '+' followed by 2-15 digits"
            )
        return value

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        if not USERNAME_VALIDATOR.match(value):
            raise ValueError(
                "Username must be from 4 to 60 symbols and contain alphanumeric characters, underscore, dash, and dot"
            )
        return value


class ResendVerificationModel(EmailNormalizationMixin, Base):
    email: EmailStr


class VerifyEmailRequestModel(Base):
    token: OneTimeToken


class LoginUserModel(EmailNormalizationMixin, Base):
    email: EmailStr
    password: Password


class SendResetPasswordRequestModel(EmailNormalizationMixin, Base):
    email: EmailStr


class LogoutRequestModel(Base):
    terminate_all_sessions: bool = False


class ResetPasswordModel(StrongPasswordValidationMixin, Base):
    token: OneTimeToken
    password: Password


class UserNewPassword(StrongPasswordValidationMixin, Base):
    current_password: Password
    password: Password
