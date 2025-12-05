from src.core.redis.client import redis_client
from src.system.services import HealthService


def get_health_service() -> HealthService:
    return HealthService(redis_client=redis_client)
