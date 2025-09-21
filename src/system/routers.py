from fastapi import APIRouter

from src.core.utils.datetime_utils import get_utc_now

router = APIRouter()


@router.get("/health/", response_model=dict)
@router.head("/health/")
def check_health() -> dict[str, str]:
    """Health check endpoint to verify the service is running."""
    return {"status": "ok"}


@router.get("/time/", response_model=dict)
def get_utc_time() -> dict[str, str]:
    """Endpoint to get the current UTC time in ISO format."""
    now = get_utc_now()
    return {"time": now.replace(microsecond=0).isoformat()}
