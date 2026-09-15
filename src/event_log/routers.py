from typing import Annotated

from fastapi import APIRouter, Depends, Query

from src.core.pagination import PaginatedResponse
from src.event_log.dependencies import get_event_log_service
from src.event_log.schemas import EventLogListParams, EventLogViewModel
from src.event_log.services import EventLogService
from src.user.auth.permissions.checker import require_permission
from src.user.auth.permissions.enum import Permission
from src.user.models import User

router = APIRouter()


@router.get("/", response_model=PaginatedResponse[EventLogViewModel])
async def list_event_logs(
    params: Annotated[EventLogListParams, Query()],
    current_user: Annotated[User, Depends(require_permission(Permission.VIEW_LOGS))],
    event_log_service: Annotated[EventLogService, Depends(get_event_log_service)],
) -> PaginatedResponse[EventLogViewModel]:
    """
    Returns a paginated event log, newest first. Filter by actor, object,
    event type and a created-date range.
    """
    return await event_log_service.get_paginated_list(
        pagination=params,
        query=params.to_list_query(),
        **params.to_filters(),
    )
