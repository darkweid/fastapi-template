from typing import NamedTuple
from uuid import UUID


class UserIdentity(NamedTuple):
    id: UUID
    email: str
