from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.session import get_session
from src.core.utils.datetime_utils import get_utc_now
from src.system.dependencies import get_health_service
from src.system.schemas import HealthCheckResponse, ServerTimeResponse
from src.system.services import HealthService

router = APIRouter()


@router.get("/health/", response_model=HealthCheckResponse)
@router.head("/health/", response_model=HealthCheckResponse, include_in_schema=False)
async def check_health(
    health_service: Annotated[HealthService, Depends(get_health_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HealthCheckResponse:
    """Health check endpoint that verifies the service and dependencies are running."""
    return await health_service.get_status(session=session)


@router.get("/time/", response_model=ServerTimeResponse)
def get_utc_time() -> ServerTimeResponse:
    """Returns the current server time in UTC, ISO 8601 format."""
    now = get_utc_now()
    return ServerTimeResponse(time=now.replace(microsecond=0).isoformat())
