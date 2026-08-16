from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.session import get_session
from src.note.repositories import NoteRepository
from src.note.services import NoteService


def get_note_repository() -> NoteRepository:
    return NoteRepository()


def get_note_service(
    repository: Annotated[NoteRepository, Depends(get_note_repository)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NoteService:
    return NoteService(repository=repository, session=session)
