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
