from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

from src.core.utils.security import normalize_email
from src.core.validations import STRONG_PASSWORD_VALIDATOR


class Base(BaseModel):
    model_config = ConfigDict(
        from_attributes=True, use_enum_values=True, extra="forbid"
    )


class SuccessResponse(Base):
    success: bool


class TokenModel(Base):
    access_token: str
    refresh_token: str


class EmailNormalizationMixin(BaseModel):
    @field_validator("email", mode="before", check_fields=False)
    @classmethod
    def _normalize_email(cls, v: str | EmailStr) -> str:
        return normalize_email(str(v))


class StrongPasswordValidationMixin(BaseModel):
    @field_validator("password", check_fields=False)
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not STRONG_PASSWORD_VALIDATOR.match(value):
            raise ValueError(
                "Password must contain at least one lowercase letter, one uppercase letter, one digit, one special character. Minimum length is 8 characters"
            )
        return value
