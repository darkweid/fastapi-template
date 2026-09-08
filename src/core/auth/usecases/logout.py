from redis.asyncio import Redis

from loggers import get_logger
from src.core.auth.realm import AuthRealm
from src.core.auth.token_helpers import invalidate_all_sessions, invalidate_session
from src.core.schemas import SuccessResponse

logger = get_logger(__name__)


class LogoutUseCase:
    """
    Invalidate one realm's current session, or every session of one subject.

    `terminate_all_sessions` picks between the two. Both go through the realm's
    key builder, so nothing here names a concrete realm.
    """

    def __init__(self, redis_client: Redis, realm: AuthRealm) -> None:
        self.redis_client = redis_client
        self.realm = realm

    async def execute(
        self,
        *,
        subject_id: str,
        session_id: str,
        terminate_all_sessions: bool = False,
    ) -> SuccessResponse:
        if terminate_all_sessions:
            await invalidate_all_sessions(
                subject_id, self.redis_client, keys=self.realm.keys
            )
            logger.debug(
                "[Logout] Invalidated all sessions for subject '%s'.", subject_id
            )
        else:
            await invalidate_session(
                subject_id, session_id, self.redis_client, keys=self.realm.keys
            )
            logger.debug(
                "[Logout] Invalidated session '%s' for subject '%s'.",
                session_id,
                subject_id,
            )

        return SuccessResponse(success=True)
