from typing import Annotated, Any, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
)

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


TrimmedStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        pattern=r"^[^\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]*$",
    ),
]
"""Free text a person types: stripped, not blank, no control characters.

A NUL fails PostgreSQL's text input with a 500, and a line break or an escape
sequence inside a name corrupts whatever prints it. C1 controls (U+0080 to
U+009F) are refused for the same reason, and the bidi embedding, override and
isolate controls (U+202A to U+202E, U+2066 to U+2069) because they make a
name render in an order other than the one stored. Add limits outside it,
`Annotated[TrimmedStr, Field(max_length=100)]`: they merge into the same
string schema, so lengths are measured after stripping. An outer `pattern=` or
`min_length=0` replaces the built-in one and drops that guard with it.
"""


TrimmedText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        pattern=(
            r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]*$"
        ),
    ),
]
"""`TrimmedStr` for text that may span lines: a comment, a message, a
description. Tab, line feed and carriage return pass; every other control
character is refused for the reasons `TrimmedStr` gives. Limits compose the
same way."""
