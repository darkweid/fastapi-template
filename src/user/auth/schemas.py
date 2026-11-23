from pydantic import EmailStr, Field, field_validator

from src.core.schemas import (
    Base,
    EmailNormalizationMixin,
    OneTimeToken,
    Password,
    StrongPasswordValidationMixin,
)
from src.core.validations import PHONE_NUMBER_PATTERN
from src.user.schemas import PersonName, Username


class CreateUserModel(StrongPasswordValidationMixin, EmailNormalizationMixin, Base):
    first_name: PersonName
    last_name: PersonName
    email: EmailStr
    username: Username
    # As with `Username`: the validator below holds the real shape.
    phone_number: str = Field(max_length=16)
    password: Password

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, value: str) -> str:
        if not PHONE_NUMBER_PATTERN.match(value):
            raise ValueError(
                "Phone number must be in E.164 format: '+' followed by 2-15 digits"
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
