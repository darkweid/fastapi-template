from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.session import get_session
from src.core.utils.datetime_utils import get_utc_now
from src.system.dependencies import get_health_service
from src.system.schemas import HealthCheckResponse
from src.system.services import HealthService

router = APIRouter()


@router.get("/health/", response_model=HealthCheckResponse)
@router.head("/health/", response_model=HealthCheckResponse, include_in_schema=False)
async def check_health(
    health_service: HealthService = Depends(get_health_service),
    session: AsyncSession = Depends(get_session),
) -> HealthCheckResponse:
    """Health check endpoint that verifies the service and dependencies are running."""
    return await health_service.get_status(session=session)


@router.get("/time/", response_model=dict)
def get_utc_time() -> dict[str, str]:
    """Endpoint to get the current UTC time in ISO format."""
    now = get_utc_now()
    return {"time": now.replace(microsecond=0).isoformat()}
