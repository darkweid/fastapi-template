from typing import Annotated, Any, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from src.core.utils.security import normalize_email
from src.core.validations import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    STRONG_PASSWORD_VALIDATOR,
)

T = TypeVar("T")


class Base(BaseModel):
    model_config = ConfigDict(
        from_attributes=True, use_enum_values=True, extra="forbid"
    )


class SuccessResponse(Base):
    success: bool


class TokenModel(Base):
    access_token: str
    # None when the refresh token was delivered as an httponly cookie instead.
    # Use cases always populate it; TokenCookieResponder strips it at the HTTP edge.
    refresh_token: str | None = None
    # Populated by TokenCookieResponder only under the cookie transport, so that a
    # cross-origin SPA - which cannot read the API-origin csrf_token cookie from JS -
    # still has a way to obtain the value it must echo in X-CSRF-Token. None under
    # the body transport, where no cookies are written and no CSRF check applies.
    csrf_token: str | None = None


class EmailNormalizationMixin(BaseModel):
    # The validator binds to these literal names. A model that calls its field
    # anything else inherits the mixin and gets no normalization at all, with
    # nothing to notice: the address reaches the database in whatever case it
    # arrived, so a uniqueness check and every later lookup miss each other.
    # Add the name here rather than writing a second validator downstream.
    @field_validator("email", "new_email", mode="before", check_fields=False)
    @classmethod
    def _normalize_email(cls, v: str | EmailStr) -> str:
        return normalize_email(str(v))


class StrongPasswordValidationMixin(BaseModel):
    @field_validator("password", check_fields=False)
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not STRONG_PASSWORD_VALIDATOR.match(value):
            raise ValueError(
                f"Password must be {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters long and contain at least one lowercase letter, one uppercase letter, one digit, and one non-alphanumeric non-space character. Printable ASCII characters are allowed."
            )
        return value


def _hide_null_from_schema(schema: dict[str, Any]) -> None:
    branches = schema.pop("anyOf", None)
    if branches is not None:
        kept = [branch for branch in branches if branch != {"type": "null"}]
        if len(kept) == 1:
            schema.update(kept[0])
        else:
            schema["anyOf"] = kept
    if "default" in schema and schema["default"] is None:
        del schema["default"]


PatchField = Annotated[T | None, Field(json_schema_extra=_hide_null_from_schema)]
"""A PATCH field that may be omitted but must not be null.

Declare it as `name: PatchField[X] = None`, with constraints inside:
`PatchField[Annotated[str, Field(min_length=2)]]`. The `None` default is what
makes the field omittable; the model still needs a validator that refuses an
explicit null, since this alias changes only the published schema.

Pydantic builds a nullable schema from `X | None`, so an error is reported
under the bare field name. `X | SkipJsonSchema[None]` hides null as well, but
it is a real union: every error is reported once per branch, with pydantic's
branch tag in the path (`first_name.constrained-str` next to a bogus
`first_name.none`). The alias owns the field's `json_schema_extra`; a field
that needs one of its own writes both steps into a single callable.
"""
