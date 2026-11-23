from __future__ import annotations

from pydantic import ValidationError
import pytest

from src.note.schemas import NoteCreateModel, NoteUpdateModel


@pytest.mark.parametrize("field", ["title", "content"])
def test_note_update_rejects_explicit_null(field: str) -> None:
    # Both columns are non-nullable: with exclude_unset PATCH semantics an
    # explicit null must fail validation instead of reaching the database.
    with pytest.raises(ValidationError):
        NoteUpdateModel.model_validate({field: None})


def test_note_update_omitted_field_stays_unset() -> None:
    model = NoteUpdateModel.model_validate({"title": "New title"})
    assert model.model_dump(exclude_unset=True) == {"title": "New title"}


def test_note_update_empty_body_dumps_empty() -> None:
    model = NoteUpdateModel.model_validate({})
    assert model.model_dump(exclude_unset=True) == {}


def test_note_update_schema_does_not_advertise_null() -> None:
    # The validator answers 422 to an explicit null, so the OpenAPI contract
    # must not tell generated SDKs that null is acceptable.
    schema = NoteUpdateModel.model_json_schema()
    for field in ("title", "content"):
        field_schema = schema["properties"][field]
        assert "null" not in str(field_schema), field_schema


@pytest.mark.parametrize("title", ["", "   ", "\n", "nul\x00", "two\nlines"])
@pytest.mark.parametrize("model", [NoteCreateModel, NoteUpdateModel])
def test_a_blank_or_control_character_title_is_refused(
    model: type[NoteCreateModel | NoteUpdateModel], title: str
) -> None:
    with pytest.raises(ValidationError):
        model(title=title)


@pytest.mark.parametrize("model", [NoteCreateModel, NoteUpdateModel])
def test_a_title_is_stored_stripped(
    model: type[NoteCreateModel | NoteUpdateModel],
) -> None:
    assert model(title="  Shopping ").title == "Shopping"
    assert len(model(title=f" {'a' * 255} ").title or "") == 255
    with pytest.raises(ValidationError):
        model(title="a" * 256)


def test_content_is_kept_as_typed() -> None:
    content = "  indented\n\tlist\n"

    assert NoteCreateModel(title="Title", content=content).content == content
    assert NoteUpdateModel(content=content).content == content
    assert NoteUpdateModel(content="").content == ""


@pytest.mark.parametrize("model", [NoteCreateModel, NoteUpdateModel])
def test_nul_in_content_is_refused(
    model: type[NoteCreateModel | NoteUpdateModel],
) -> None:
    """PostgreSQL cannot hold it: the INSERT would answer 500."""
    with pytest.raises(ValidationError):
        model(title="Title", content="a\x00b")
