from src.event_log.enums import ObjectType
from src.event_log.events import DomainEvent


class NoteCreated(DomainEvent):
    code = "note.created"
    object_type = ObjectType.NOTE


class NoteUpdated(DomainEvent):
    """Field names, not values: the note's text is already in `notes`, and an
    audit log that copies it becomes a version history nothing prunes. A
    project that does want the old value puts the pair from `changed_fields`
    here instead."""

    code = "note.updated"
    object_type = ObjectType.NOTE

    fields: list[str]


class NoteDeleted(DomainEvent):
    code = "note.deleted"
    object_type = ObjectType.NOTE
