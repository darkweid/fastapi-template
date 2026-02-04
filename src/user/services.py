from src.core.schemas import Base
from src.core.services import BaseService
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
