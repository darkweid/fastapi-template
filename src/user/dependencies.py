from src.user.repositories import UserRepository
from src.user.services import UserService


def get_user_service() -> UserService:
    user_repo = UserRepository()
    return UserService(
        repository=user_repo,
    )
