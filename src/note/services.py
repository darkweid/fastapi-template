from sqlalchemy.ext.asyncio import AsyncSession

from src.core.services import BaseService
from src.note.models import Note
from src.note.repositories import NoteRepository
from src.note.schemas import NoteCreateModel, NoteViewModel


class NoteService(BaseService[Note, NoteCreateModel, NoteRepository, NoteViewModel]):
    def __init__(
        self,
        repository: NoteRepository,
        session: AsyncSession,
    ):
        super().__init__(repository, session, response_schema=NoteViewModel)
