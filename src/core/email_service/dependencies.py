from typing import Annotated

from fastapi import Depends

from src.core.email_service.service import EmailService
from src.core.email_service.tasks import get_mailer
from src.core.outbox.dependencies import get_task_dispatcher
from src.core.outbox.dispatcher import TaskDispatcher


def get_email_service(
    dispatcher: Annotated[TaskDispatcher, Depends(get_task_dispatcher)],
) -> EmailService:
    return EmailService(get_mailer(), dispatcher)
