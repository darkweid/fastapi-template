from typing import TypeVar, Generic

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator, Field

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


class TokenRefreshModel(Base):
    access_token: str


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


class PaginationParams(Base):
    """Pagination request parameters.

    - page: page number starting from 1
    - size: page size from 1 to 100
    """

    page: int = Field(..., ge=1)
    size: int = Field(..., ge=1, le=100)


T = TypeVar("T")


class PaginatedResponse(Base, Generic[T]):
    """Generic paginated response container.

    The `items` list can contain any Pydantic model defined by the API.
    """

    items: list[T]
    total: int
    page: int
    size: int
    pages: int
