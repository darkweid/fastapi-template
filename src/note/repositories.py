from src.core.database.repositories import SoftDeleteRepository
from src.note.models import Note


class NoteRepository(SoftDeleteRepository[Note]):
    model = Note
    searchable_fields = ("title", "content")
    sortable_fields = ("created_at", "title")
    default_order_by = "created_at"
