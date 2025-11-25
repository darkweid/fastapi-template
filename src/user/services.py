from typing import Any

from src.core.services import BaseService
from src.user.auth.schemas import CreateUserModel
from src.user.models import User
from src.user.repositories import UserRepository


class UserService(BaseService[User, CreateUserModel, Any, UserRepository]):
    def __init__(
        self,
        repository: UserRepository,
    ):
        super().__init__(repository)
