from sqlalchemy.ext.asyncio import AsyncSession

from src.core.schemas import Base
from src.core.services import BaseService
from src.core.utils.security import hash_password
from src.user.auth.schemas import CreateUserModel
from src.user.models import User
from src.user.repositories import UserRepository
from src.user.schemas import UserSummaryViewModel


class UserService(
    BaseService[User, CreateUserModel, Base, UserRepository, UserSummaryViewModel]
):
    def __init__(
        self,
        repository: UserRepository,
    ):
        super().__init__(repository, response_schema=UserSummaryViewModel)

    async def create(self, session: AsyncSession, data: CreateUserModel) -> User:
        user_data = data.model_dump()
        raw_password = user_data.pop("password")
        user_data["password_hash"] = hash_password(raw_password)
        return await self.repository.create(
            session=session, data=user_data, commit=True
        )
