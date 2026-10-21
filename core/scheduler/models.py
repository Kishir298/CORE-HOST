from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class AgentProfile:
    """
    Describes an agent that can be assigned to a device.

    A profile is not a running resource — it is a capability declaration
    for an agent type that C.O.R.E. may instantiate on the Windows host
    on behalf of a low-capability device (phone, watch, R.O.V.E.R.T.).
    """

    profile_id: str
    name: str
    agent_type: str = "local"
    version: str = "0.1.0"
    host: str = "windows-host"
    capabilities: list[str] = field(default_factory=list)
    supported_device_types: list[str] = field(default_factory=list)
    supported_platforms: list[str] = field(default_factory=list)
    max_assignments: int = 100
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise ValueError("profile_id cannot be empty.")
        if not self.name:
            raise ValueError("Agent profile name cannot be empty.")
        # Normalize lists
        self.capabilities = list(self.capabilities or [])
        self.supported_device_types = list(self.supported_device_types or [])
        self.supported_platforms = list(self.supported_platforms or [])

    def supports_device(self, device_type: str, platform: str) -> bool:
        """Return whether this profile explicitly supports a device type/platform."""
        if self.supported_device_types and device_type not in self.supported_device_types:
            return False
        if self.supported_platforms and platform not in self.supported_platforms:
            return False
        return True

    def score_for(self, device_capabilities: list[str], device_type: str, platform: str) -> int:
        """
        Score this profile for a device.

        Higher score indicates a better fit. Scoring:
          +10 per shared capability
          +5 if device_type supported (or profile supports all)
          +5 if platform supported
          +2 bonus for host == windows-host (offload)
        """
        shared = len(set(self.capabilities).intersection(set(device_capabilities or [])))
        score = shared * 10
        if not self.supported_device_types or device_type in self.supported_device_types:
            score += 5
        if not self.supported_platforms or platform in self.supported_platforms:
            score += 5
        if self.host == "windows-host":
            score += 2
        return score

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "agent_type": self.agent_type,
            "version": self.version,
            "host": self.host,
            "capabilities": list(self.capabilities),
            "supported_device_types": list(self.supported_device_types),
            "supported_platforms": list(self.supported_platforms),
            "max_assignments": self.max_assignments,
            "metadata": dict(self.metadata),
        }


@dataclass
class Assignment:
    """Records an agent assignment for a device."""

    device_id: str
    agent_id: str
    profile_id: str
    assigned_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "agent_id": self.agent_id,
            "profile_id": self.profile_id,
            "assigned_at": self.assigned_at.isoformat(),
            "metadata": dict(self.metadata),
        }
