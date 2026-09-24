from typing import Annotated

from pydantic import EmailStr, Field, ValidationError
import pytest

from src.core.schemas import Base, EmailNormalizationMixin, PatchField


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


class _PatchModel(Base):
    name: PatchField[Annotated[str, Field(min_length=2, max_length=30)]] = None
    count: PatchField[int] = None


def test_patch_field_hides_null_and_keeps_constraints_in_the_schema() -> None:
    """The field accepts no explicit null (the model's validator refuses it),
    so a generated SDK must not be told otherwise, while the length limits a
    form validates against must survive the null branch being dropped."""
    properties = _PatchModel.model_json_schema()["properties"]

    assert properties["name"] == {
        "maxLength": 30,
        "minLength": 2,
        "title": "Name",
        "type": "string",
    }
    assert properties["count"] == {"title": "Count", "type": "integer"}


def test_patch_field_reports_errors_under_the_bare_field_name() -> None:
    with pytest.raises(ValidationError) as caught:
        _PatchModel.model_validate({"name": "A", "count": "many"})

    assert [error["loc"] for error in caught.value.errors()] == [
        ("name",),
        ("count",),
    ]


def test_patch_field_leaves_an_omitted_field_unset() -> None:
    assert _PatchModel.model_validate({"count": 3}).model_dump(exclude_unset=True) == {
        "count": 3
    }
