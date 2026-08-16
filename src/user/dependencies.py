from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.session import get_session
from src.user.repositories import UserRepository
from src.user.services import UserService


def get_user_repository() -> UserRepository:
    return UserRepository()


def get_user_service(
    repository: Annotated[UserRepository, Depends(get_user_repository)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserService:
    return UserService(repository=repository, session=session)
