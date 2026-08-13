from src.core.database.session import async_session
from src.core.outbox.dispatcher import TaskDispatcher


def get_task_dispatcher() -> TaskDispatcher:
    return TaskDispatcher(session_factory=async_session)
