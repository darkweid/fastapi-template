from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.errors.exceptions import InstanceNotFoundException
from src.core.pagination import (
    PaginatedResponse,
    PaginationParams,
    make_paginated_response,
)
from src.note.dependencies import get_note_service
from src.note.schemas import NoteViewModel
from src.note.usecases.create_note import get_create_note_use_case
from src.note.usecases.delete_note import get_delete_note_use_case
from src.note.usecases.update_note import get_update_note_use_case
from src.user.auth.dependencies import get_current_user
from src.user.enums import UserRole
from tests.factories.note_factory import build_note
from tests.factories.user_factory import build_user
from tests.helpers.overrides import DependencyOverrides
from tests.helpers.providers import ProvideValue


class FakeCreateNoteUseCase:
    def __init__(self, result: NoteViewModel | None = None) -> None:
        self.execute = AsyncMock(return_value=result)


class FakeUpdateNoteUseCase:
    def __init__(
        self,
        result: NoteViewModel | None = None,
        error: Exception | None = None,
    ) -> None:
        self.execute = AsyncMock(return_value=result, side_effect=error)


class FakeDeleteNoteUseCase:
    def __init__(self, error: Exception | None = None) -> None:
        self.execute = AsyncMock(return_value=None, side_effect=error)


class FakeNoteService:
    def __init__(
        self,
        note=None,
        not_found: bool = False,
        paginated: PaginatedResponse[NoteViewModel] | None = None,
    ) -> None:
        self.get_single_or_404 = AsyncMock()
        if not_found:
            self.get_single_or_404.side_effect = InstanceNotFoundException(
                "Note not found"
            )
        else:
            self.get_single_or_404.return_value = note
        self.get_paginated_list = AsyncMock(return_value=paginated)


def note_view_from(note) -> NoteViewModel:
    return NoteViewModel.model_validate(note)


@pytest.mark.asyncio
async def test_create_note_returns_created_note(
    async_client_with_fakes, dependency_overrides: DependencyOverrides
) -> None:
    current_user = build_user(role=UserRole.VIEWER)
    created = build_note(owner_id=current_user.id, title="My note", content="Body")
    dependency_overrides.set(get_current_user, ProvideValue(current_user))
    dependency_overrides.set(
        get_create_note_use_case,
        ProvideValue(FakeCreateNoteUseCase(result=note_view_from(created))),
    )

    response = await async_client_with_fakes.post(
        "/v1/notes/", json={"title": "My note", "content": "Body"}
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["title"] == "My note"
    assert payload["owner_id"] == str(current_user.id)


@pytest.mark.asyncio
async def test_create_note_rejects_invalid_payload(
    async_client_with_fakes, dependency_overrides: DependencyOverrides
) -> None:
    current_user = build_user(role=UserRole.VIEWER)
    dependency_overrides.set(get_current_user, ProvideValue(current_user))
    use_case = FakeCreateNoteUseCase()
    dependency_overrides.set(get_create_note_use_case, ProvideValue(use_case))

    response = await async_client_with_fakes.post("/v1/notes/", json={"title": ""})

    assert response.status_code == 422
    use_case.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_own_note_succeeds(
    async_client_with_fakes, dependency_overrides: DependencyOverrides
) -> None:
    current_user = build_user(role=UserRole.VIEWER)
    note = build_note(owner_id=current_user.id, title="Mine")
    dependency_overrides.set(get_current_user, ProvideValue(current_user))
    dependency_overrides.set(get_note_service, ProvideValue(FakeNoteService(note=note)))

    response = await async_client_with_fakes.get(f"/v1/notes/{note.id}")

    assert response.status_code == 200
    assert response.json()["title"] == "Mine"


@pytest.mark.asyncio
async def test_get_foreign_note_without_permission_returns_404(
    async_client_with_fakes, dependency_overrides: DependencyOverrides
) -> None:
    """Foreign note must answer 404, never 403 (anti-enumeration)."""
    current_user = build_user(role=UserRole.VIEWER)
    note = build_note(owner_id=uuid4(), title="Not mine")
    dependency_overrides.set(get_current_user, ProvideValue(current_user))
    dependency_overrides.set(get_note_service, ProvideValue(FakeNoteService(note=note)))

    response = await async_client_with_fakes.get(f"/v1/notes/{note.id}")

    assert response.status_code == 404
    assert response.json() == {"code": "not_found", "message": "Note not found."}


@pytest.mark.asyncio
async def test_get_foreign_note_with_view_permission_succeeds(
    async_client_with_fakes, dependency_overrides: DependencyOverrides
) -> None:
    admin_user = build_user(role=UserRole.ADMIN)
    note = build_note(owner_id=uuid4(), title="Not mine")
    dependency_overrides.set(get_current_user, ProvideValue(admin_user))
    dependency_overrides.set(get_note_service, ProvideValue(FakeNoteService(note=note)))

    response = await async_client_with_fakes.get(f"/v1/notes/{note.id}")

    assert response.status_code == 200
    assert response.json()["title"] == "Not mine"


@pytest.mark.asyncio
async def test_get_missing_note_returns_404(
    async_client_with_fakes, dependency_overrides: DependencyOverrides
) -> None:
    current_user = build_user(role=UserRole.VIEWER)
    dependency_overrides.set(get_current_user, ProvideValue(current_user))
    dependency_overrides.set(
        get_note_service, ProvideValue(FakeNoteService(not_found=True))
    )

    response = await async_client_with_fakes.get(f"/v1/notes/{uuid4()}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_notes_scopes_query_to_the_current_user(
    async_client_with_fakes, dependency_overrides: DependencyOverrides
) -> None:
    current_user = build_user(role=UserRole.VIEWER)
    note = build_note(owner_id=current_user.id, title="Mine")
    paginated = make_paginated_response(
        items=[note],
        total=1,
        pagination=PaginationParams(page=1, size=50),
        schema=NoteViewModel,
    )
    fake_service = FakeNoteService(paginated=paginated)
    dependency_overrides.set(get_current_user, ProvideValue(current_user))
    dependency_overrides.set(get_note_service, ProvideValue(fake_service))

    response = await async_client_with_fakes.get("/v1/notes/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["owner_id"] == str(current_user.id)
    list_query = fake_service.get_paginated_list.await_args.kwargs["query"]
    assert list_query.conditions.eq == {"owner_id": current_user.id}


@pytest.mark.asyncio
async def test_list_notes_rejects_unsupported_order_by(
    async_client_with_fakes, dependency_overrides: DependencyOverrides
) -> None:
    """The real NoteService/NoteRepository run here (not faked): an invalid
    order_by is rejected before any query executes, so no DB is needed."""
    current_user = build_user(role=UserRole.VIEWER)
    dependency_overrides.set(get_current_user, ProvideValue(current_user))

    response = await async_client_with_fakes.get(
        "/v1/notes/", params={"order_by": "owner_id"}
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_query"


@pytest.mark.asyncio
async def test_update_note_returns_updated_note(
    async_client_with_fakes, dependency_overrides: DependencyOverrides
) -> None:
    current_user = build_user(role=UserRole.VIEWER)
    updated = build_note(owner_id=current_user.id, title="Updated")
    dependency_overrides.set(get_current_user, ProvideValue(current_user))
    dependency_overrides.set(
        get_update_note_use_case,
        ProvideValue(FakeUpdateNoteUseCase(result=note_view_from(updated))),
    )

    response = await async_client_with_fakes.patch(
        f"/v1/notes/{updated.id}", json={"title": "Updated"}
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Updated"


@pytest.mark.asyncio
async def test_update_foreign_note_returns_404(
    async_client_with_fakes, dependency_overrides: DependencyOverrides
) -> None:
    current_user = build_user(role=UserRole.VIEWER)
    dependency_overrides.set(get_current_user, ProvideValue(current_user))
    dependency_overrides.set(
        get_update_note_use_case,
        ProvideValue(
            FakeUpdateNoteUseCase(error=InstanceNotFoundException("Note not found."))
        ),
    )

    response = await async_client_with_fakes.patch(
        f"/v1/notes/{uuid4()}", json={"title": "Updated"}
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_note_returns_no_content(
    async_client_with_fakes, dependency_overrides: DependencyOverrides
) -> None:
    current_user = build_user(role=UserRole.VIEWER)
    dependency_overrides.set(get_current_user, ProvideValue(current_user))
    dependency_overrides.set(
        get_delete_note_use_case, ProvideValue(FakeDeleteNoteUseCase())
    )

    response = await async_client_with_fakes.delete(f"/v1/notes/{uuid4()}")

    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.asyncio
async def test_delete_foreign_note_returns_404(
    async_client_with_fakes, dependency_overrides: DependencyOverrides
) -> None:
    current_user = build_user(role=UserRole.VIEWER)
    dependency_overrides.set(get_current_user, ProvideValue(current_user))
    dependency_overrides.set(
        get_delete_note_use_case,
        ProvideValue(
            FakeDeleteNoteUseCase(error=InstanceNotFoundException("Note not found."))
        ),
    )

    response = await async_client_with_fakes.delete(f"/v1/notes/{uuid4()}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_notes_endpoints_require_authentication(async_client_with_fakes) -> None:
    response = await async_client_with_fakes.get("/v1/notes/")

    assert response.status_code == 401
