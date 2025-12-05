from typing import Literal

from src.core.schemas import Base


class HealthCheckResponse(Base):
    status: Literal["ok"] = "ok"
