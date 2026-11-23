from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.repositories import SoftDeleteRepository
from src.core.utils.datetime_utils import get_utc_now
from src.user.dto import UserIdentity
from src.user.models import User


class UserRepository(SoftDeleteRepository[User]):

    model = User

    async def lock_unverified_ids(
        self,
        session: AsyncSession,
        *,
        created_before: datetime,
        after_id: UUID | None,
        limit: int,
    ) -> list[UUID]:
        """Ids of the next unverified users past `after_id`, ascending and locked.

        A row someone else holds is skipped, so an empty list ends a keyset
        walk without proving that none is left.
        """
        query = (
            select(self.model.id)
            .where(
                self.model.is_deleted.is_(False),
                self.model.is_verified.is_(False),
                self.model.created_at < created_before,
            )
            .order_by(self.model.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        if after_id is not None:
            query = query.where(self.model.id > after_id)
        return list((await session.execute(query)).scalars().all())

    async def soft_delete_unverified(
        self, session: AsyncSession, ids: Sequence[UUID], *, created_before: datetime
    ) -> list[UserIdentity]:
        """Soft delete those of `ids` that are still unverified and old enough."""
        now = get_utc_now()
        result = await session.execute(
            update(self.model)
            .where(
                self.model.id.in_(ids),
                self.model.is_deleted.is_(False),
                self.model.is_verified.is_(False),
                self.model.created_at < created_before,
            )
            .values(is_deleted=True, deleted_at=now)
            .returning(self.model.id, self.model.email)
            .execution_options(synchronize_session=False)
        )
        return [UserIdentity(id=row.id, email=row.email) for row in result]
