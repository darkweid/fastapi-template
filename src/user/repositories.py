from loggers import get_logger
from src.core.database.repositories import SoftDeleteRepository
from src.user.models import User

logger = get_logger(__name__)


class UserRepository(SoftDeleteRepository[User]):

    model = User
