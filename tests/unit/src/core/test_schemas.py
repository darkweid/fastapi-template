from pydantic import EmailStr

from src.core.schemas import Base, EmailNormalizationMixin


class _EmailChangeModel(EmailNormalizationMixin, Base):
    new_email: EmailStr


class _LoginModel(EmailNormalizationMixin, Base):
    email: EmailStr


def test_the_mixin_normalizes_an_address_field_not_called_email() -> None:
    """`field_validator` binds to literal names, so a field the mixin does not
    list keeps whatever case it arrived in - and an address stored uppercase is
    unreachable through every lookup that normalizes first."""
    assert _EmailChangeModel(new_email=" User@Example.COM ").new_email == (
        "user@example.com"
    )


def test_the_mixin_still_normalizes_the_plain_email_field() -> None:
    assert _LoginModel(email=" User@Example.COM ").email == "user@example.com"
