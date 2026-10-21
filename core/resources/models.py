from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Typed resource type constants for Windows co-hosted deployment
RESOURCE_TYPE_DEVICE = "device"
RESOURCE_TYPE_AGENT = "agent"
RESOURCE_TYPE_SERVICE = "service"
RESOURCE_TYPE_CONNECTION = "connection"


@dataclass
class Resource:
    """Represents a resource registered with C.O.R.E."""

    resource_id: str
    name: str
    resource_type: str
    status: str = "offline"
    owner: str | None = None
    source: str | None = None
    capabilities: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    connection_info: dict = field(default_factory=dict)
    last_seen: datetime | None = None
    registered_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def mark_seen(self) -> None:
        self.last_seen = datetime.now(timezone.utc)
        self.status = "online"

    def is_online(self) -> bool:
        return self.status == "online"

    def update_status(self, status: str) -> None:
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        """Return a serializable representation for cross-system use."""

        return {
            "resource_id": self.resource_id,
            "name": self.name,
            "resource_type": self.resource_type,
            "status": self.status,
            "owner": self.owner,
            "source": self.source,
            "capabilities": list(self.capabilities),
            "metadata": dict(self.metadata),
            "connection_info": dict(self.connection_info),
            "last_seen": (
                self.last_seen.isoformat()
                if self.last_seen is not None
                else None
            ),
            "registered_at": self.registered_at.isoformat(),
        }


def create_device_resource(
    device_id: str,
    name: str,
    device_type: str = "generic",
    platform: str = "unknown",
    capabilities: list[str] | None = None,
    status: str = "offline",
    assigned_agent: str | None = None,
    connection_info: dict | None = None,
    metadata: dict | None = None,
) -> Resource:
    """
    Create a typed device resource for the Windows host.

    Device resources model phones, watches, R.O.V.E.R.T., or any external
    hardware that may offload agent execution to the laptop host.
    """

    if not device_id:
        raise ValueError("device_id cannot be empty.")
    if not name:
        raise ValueError("Device name cannot be empty.")

    meta = dict(metadata or {})
    meta.setdefault("device_type", device_type)
    meta.setdefault("platform", platform)
    if assigned_agent is not None:
        meta["assigned_agent"] = assigned_agent
    # Track connection time for runtime history
    if "connection_time" not in meta:
        meta["connection_time"] = datetime.now(timezone.utc).isoformat()

    return Resource(
        resource_id=device_id,
        name=name,
        resource_type=RESOURCE_TYPE_DEVICE,
        status=status,
        capabilities=list(capabilities or []),
        connection_info=dict(connection_info or {}),
        metadata=meta,
    )


def create_agent_resource(
    agent_id: str,
    name: str,
    agent_type: str = "local",
    version: str = "0.1.0",
    host: str = "windows-host",
    target_device: str | None = None,
    status: str = "running",
    capabilities: list[str] | None = None,
    metadata: dict | None = None,
) -> Resource:
    """
    Create a typed agent resource for host-side execution.

    Agent resources model A.S.I.S. instances running on the Windows laptop
    (e.g., offloaded from low-capability devices).
    """

    if not agent_id:
        raise ValueError("agent_id cannot be empty.")
    if not name:
        raise ValueError("Agent name cannot be empty.")

    meta = dict(metadata or {})
    meta.setdefault("agent_type", agent_type)
    meta.setdefault("version", version)
    meta.setdefault("host", host)
    if target_device is not None:
        meta["target_device"] = target_device
    if "start_time" not in meta:
        meta["start_time"] = datetime.now(timezone.utc).isoformat()

    return Resource(
        resource_id=agent_id,
        name=name,
        resource_type=RESOURCE_TYPE_AGENT,
        status=status,
        capabilities=list(capabilities or []),
        metadata=meta,
    )
