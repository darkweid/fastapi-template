from typing import Self
from uuid import UUID

from pydantic import ConfigDict

from src.core.schemas import Base
from src.event_log.enums import ActorType


class Actor(Base):
    """Who performed the action, as the log stores it.

    Built only through the named constructors: they are what stops a caller
    from pairing one realm's type with another realm's id.

    `use_enum_values` is switched off against the project default: this object
    is read in code far more often than it is serialised, and the default would
    hand every reader a plain string where an `ActorType` is expected.
    """

    model_config = ConfigDict(frozen=True, use_enum_values=False)

    actor_type: ActorType
    actor_id: UUID | None = None
    # Only ever taken from `get_client_ip(request)`, never from a header a
    # caller controls. Which events carry it is the caller's choice: the column
    # is nullable, and an address is worth storing wherever the answer to "who
    # did this" may later be "not the account owner".
    ip: str | None = None

    @classmethod
    def user(cls, user_id: UUID, ip: str | None = None) -> Self:
        return cls(actor_type=ActorType.USER, actor_id=user_id, ip=ip)

    @classmethod
    def system(cls) -> Self:
        """A scheduled task or a migration: no account, no address."""
        return cls(actor_type=ActorType.SYSTEM)

    @classmethod
    def anonymous(cls, ip: str | None = None) -> Self:
        """A caller who is not signed in - a failed sign-in, a public form."""
        return cls(actor_type=ActorType.ANONYMOUS, ip=ip)
