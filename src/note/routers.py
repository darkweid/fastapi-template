from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from src.core.database.filters import FilterCondition
from src.core.pagination import ListQueryParams, PaginatedResponse
from src.note.dependencies import get_note_service
from src.note.policies import ensure_note_view_access
from src.note.schemas import NoteCreateModel, NoteUpdateModel, NoteViewModel
from src.note.services import NoteService
from src.note.usecases.create_note import CreateNoteUseCase, get_create_note_use_case
from src.note.usecases.delete_note import DeleteNoteUseCase, get_delete_note_use_case
from src.note.usecases.update_note import UpdateNoteUseCase, get_update_note_use_case
from src.user.auth.dependencies import get_current_user
from src.user.models import User

router = APIRouter()


@router.post("/", response_model=NoteViewModel, status_code=201)
async def create_note(
    note_form_data: NoteCreateModel,
    current_user: Annotated[User, Depends(get_current_user)],
    use_case: Annotated[CreateNoteUseCase, Depends(get_create_note_use_case)],
) -> NoteViewModel:
    """
    Creates a note owned by the current user.
    """
    return await use_case.execute(data=note_form_data, owner_id=current_user.id)


@router.get("/", response_model=PaginatedResponse[NoteViewModel])
async def list_notes(
    params: Annotated[ListQueryParams, Query()],
    current_user: Annotated[User, Depends(get_current_user)],
    note_service: Annotated[NoteService, Depends(get_note_service)],
) -> PaginatedResponse[NoteViewModel]:
    """
    Returns a paginated list of the current user's notes. Supports free-text
    search, sorting and a created-date range.
    """
    list_query = params.to_list_query(
        conditions=FilterCondition(eq={"owner_id": current_user.id})
    )
    return await note_service.get_paginated_list(pagination=params, query=list_query)


@router.get("/{note_id}", response_model=NoteViewModel)
async def get_note(
    note_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    note_service: Annotated[NoteService, Depends(get_note_service)],
) -> NoteViewModel:
    """
    Returns a single note by its identifier.
    """
    note = await note_service.get_single_or_404(id=note_id)
    ensure_note_view_access(note, current_user.id, current_user.role)
    return NoteViewModel.model_validate(note)


@router.patch("/{note_id}", response_model=NoteViewModel)
async def update_note(
    note_id: UUID,
    note_form_data: NoteUpdateModel,
    current_user: Annotated[User, Depends(get_current_user)],
    use_case: Annotated[UpdateNoteUseCase, Depends(get_update_note_use_case)],
) -> NoteViewModel:
    """
    Updates a note's title and/or content.
    """
    return await use_case.execute(
        note_id=note_id, data=note_form_data, current_user=current_user
    )


@router.delete("/{note_id}", status_code=204)
async def delete_note(
    note_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    use_case: Annotated[DeleteNoteUseCase, Depends(get_delete_note_use_case)],
) -> None:
    """
    Deletes a note.
    """
    await use_case.execute(note_id=note_id, current_user=current_user)
