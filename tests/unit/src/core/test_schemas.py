from typing import Annotated

from pydantic import EmailStr, Field, ValidationError
import pytest

from src.core.schemas import (
    Base,
    EmailNormalizationMixin,
    PatchField,
    TrimmedStr,
    TrimmedText,
)


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


class _NamedModel(Base):
    name: TrimmedStr
    title: Annotated[TrimmedStr, Field(min_length=2, max_length=5)] = "title"


def test_trimmed_str_strips_surrounding_whitespace() -> None:
    assert _NamedModel(name="  Anne \n").name == "Anne"


@pytest.mark.parametrize("blank", ["", "   ", "\t\n", " "])
def test_trimmed_str_refuses_a_blank_value(blank: str) -> None:
    """A name of spaces passes a bare `min_length=1` and renders as nothing."""
    with pytest.raises(ValidationError):
        _NamedModel(name=blank)


@pytest.mark.parametrize("control", ["\x00", "\x1b", "\x7f", "\t", "\n"])
def test_trimmed_str_refuses_an_inner_control_character(control: str) -> None:
    """A NUL fails PostgreSQL's text input with a 500, and an escape sequence
    or a line break inside a name corrupts every log and table that prints it."""
    with pytest.raises(ValidationError):
        _NamedModel(name=f"An{control}ne")


def test_trimmed_str_measures_length_after_stripping() -> None:
    assert _NamedModel(name="a", title="  abcde  ").title == "abcde"

    with pytest.raises(ValidationError) as caught:
        _NamedModel(name="a", title=" abcdef ")

    assert caught.value.errors()[0]["type"] == "string_too_long"


def test_trimmed_str_publishes_its_rules_in_the_schema() -> None:
    title = _NamedModel.model_json_schema()["properties"]["title"]

    assert title["minLength"] == 2
    assert title["maxLength"] == 5
    assert "pattern" in title


class _TextModel(Base):
    text: Annotated[TrimmedText, Field(max_length=10)]


def test_trimmed_text_keeps_inner_line_breaks_and_tabs() -> None:
    assert _TextModel(text="  a\r\nb\tc \n").text == "a\r\nb\tc"


@pytest.mark.parametrize("blank", ["", "  ", "\n\t"])
def test_trimmed_text_refuses_a_blank_value(blank: str) -> None:
    with pytest.raises(ValidationError):
        _TextModel(text=blank)


@pytest.mark.parametrize("control", ["\x00", "\x1b", "\x7f", "\x0b", "\x0c"])
def test_trimmed_text_refuses_other_control_characters(control: str) -> None:
    with pytest.raises(ValidationError):
        _TextModel(text=f"a{control}b")
