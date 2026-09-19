"""Host-side portal package (presentation/control interface only).

Consumes C.O.R.E. authorities (DeviceRegistry, ResourceRegistry,
OrganizationEngine, Router, ServiceManager, AgentScheduler, HealthMonitor,
Runtime, RESCS adapter) through their public APIs. Never manipulates
internal dictionaries, registries, sockets, or persistence files.
"""

from .geo import (
    distance_between,
    format_distance,
    format_location,
    has_coordinates,
    haversine_meters,
    make_location,
    unknown_location,
)

__all__ = [
    "distance_between",
    "format_distance",
    "format_location",
    "has_coordinates",
    "haversine_meters",
    "make_location",
    "unknown_location",
]
