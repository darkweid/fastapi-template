from src.user.repositories import UserRepository
from src.user.services import UserService


def get_user_repository() -> UserRepository:
    return UserRepository()


def get_user_service() -> UserService:
    return UserService(
        repository=get_user_repository(),
    )
