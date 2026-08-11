from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class SystemRepository:
    """
    Infrastructure-level repository that owns the system module's SQL.

    Deliberately not a BaseRepository subclass: it is not bound to a domain
    model, so the generic CRUD surface does not apply. It exists so that the
    connectivity probe lives in the persistence layer like every other query.
    """

    async def ping(self, session: AsyncSession) -> None:
        """Run a trivial query to confirm the database answers. Raises on failure."""
        await session.execute(text("SELECT 1"))
