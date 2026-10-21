from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ServiceStatus(str, Enum):
    REGISTERED = "registered"
    INITIALIZING = "initializing"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass
class ServiceRequest:
    """A request to execute a service operation."""

    service_id: str
    operation: str
    payload: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None


@dataclass
class ServiceResponse:
    """The result of executing a service operation."""

    service_id: str
    operation: str
    payload: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    request_id: str | None = None
    error: str | None = None


@dataclass
class Service:
    service_id: str
    name: str
    version: str
    dependencies: list[str] = field(default_factory=list)
    startup_priority: int = 100
    configuration: dict[str, Any] = field(default_factory=dict)
    status: ServiceStatus = ServiceStatus.REGISTERED
    health: str = "unknown"

    def mark_healthy(self) -> None:
        self.health = "healthy"

    def mark_unhealthy(self) -> None:
        self.health = "unhealthy"
