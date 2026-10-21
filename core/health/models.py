from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class HealthStatus(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class HealthResult:
    component_id: str
    status: HealthStatus
    message: str = ""
    checked_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
