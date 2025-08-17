from fastapi import APIRouter

router = APIRouter()


@router.get("/health/", response_model=dict)
@router.head("/health/", include_in_schema=False)
async def check_health() -> dict[str, str]:
    """Health check endpoint to verify the service is running."""
    return {"status": "ok"}
