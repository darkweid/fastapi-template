from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.session import get_session
from src.core.utils.datetime_utils import get_utc_now
from src.system.dependencies import get_health_service, get_readiness_service
from src.system.schemas import HealthCheckResponse, ProbeResponse, ServerTimeResponse
from src.system.services import HealthService, ReadinessService

router = APIRouter()


@router.get("/live/", response_model=ProbeResponse)
@router.head("/live/", response_model=ProbeResponse, include_in_schema=False)
async def check_liveness() -> ProbeResponse:
    """Liveness probe: answers as long as the process itself is running."""
    return ProbeResponse()


@router.get("/ready/", response_model=ProbeResponse)
@router.head("/ready/", response_model=ProbeResponse, include_in_schema=False)
async def check_readiness(
    readiness_service: Annotated[ReadinessService, Depends(get_readiness_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProbeResponse:
    """Readiness probe: reports whether the service can reach its database."""
    await readiness_service.ensure_ready(session=session)
    return ProbeResponse()


@router.get("/health/", response_model=HealthCheckResponse)
@router.head("/health/", response_model=HealthCheckResponse, include_in_schema=False)
async def check_health(
    health_service: Annotated[HealthService, Depends(get_health_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HealthCheckResponse:
    """Detailed health report with the status of every dependency."""
    return await health_service.get_status(session=session)


@router.get("/time/", response_model=ServerTimeResponse)
def get_utc_time() -> ServerTimeResponse:
    """Returns the current server time in UTC, ISO 8601 format."""
    now = get_utc_now()
    return ServerTimeResponse(time=now.replace(microsecond=0).isoformat())
