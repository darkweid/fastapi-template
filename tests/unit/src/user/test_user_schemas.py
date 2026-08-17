from __future__ import annotations

from pydantic import ValidationError
import pytest

from src.user.schemas import UserProfileUpdateModel


@pytest.mark.parametrize("field", ["first_name", "last_name", "username"])
def test_profile_update_rejects_explicit_null(field: str) -> None:
    # All three columns are non-nullable: with exclude_unset PATCH semantics an
    # explicit null must fail validation instead of reaching the database.
    with pytest.raises(ValidationError):
        UserProfileUpdateModel.model_validate({field: None})


def test_profile_update_omitted_field_stays_unset() -> None:
    model = UserProfileUpdateModel.model_validate({"username": "new-name"})
    assert model.model_dump(exclude_unset=True) == {"username": "new-name"}


def test_profile_update_schema_does_not_advertise_null() -> None:
    # The validator answers 422 to an explicit null, so the OpenAPI contract
    # must not tell generated SDKs that null is acceptable.
    schema = UserProfileUpdateModel.model_json_schema()
    for field in ("first_name", "last_name", "username"):
        field_schema = schema["properties"][field]
        assert "null" not in str(field_schema), field_schema
