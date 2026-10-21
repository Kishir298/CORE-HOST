from __future__ import annotations

import uuid
from threading import RLock
from typing import Any

from core.resources.models import Resource, create_agent_resource

from .models import AgentProfile, Assignment


class SchedulerError(Exception):
    """Base scheduler error."""


class ProfileAlreadyRegistered(SchedulerError):
    pass


class ProfileNotFound(SchedulerError):
    pass


class AssignmentNotFound(SchedulerError):
    pass


class NoSuitableAgent(SchedulerError):
    pass


class AgentScheduler:
    """
    Assigns devices to agents based on capabilities.

    The scheduler is the decision point for the flow:

        Device connects -> CORE identifies capabilities -> determines suitable agent
        -> agent assigned -> runs on windows-host -> result -> device

    It is capability-driven, not device-type hard-coded, and remains
    transport-agnostic. Thread-safe and fully testable without launching
    a local AI model.
    """

    # Default profiles that ship with C.O.R.E. for single-host deployment
    DEFAULT_PROFILES: list[AgentProfile] = []  # populated lazily

    def __init__(self) -> None:
        self._profiles: dict[str, AgentProfile] = {}
        self._assignments: dict[str, Assignment] = {}  # device_id -> Assignment
        self._agent_resources: dict[str, Resource] = {}  # agent_id -> Resource
        self._profile_assignment_counts: dict[str, int] = {}
        self._lock = RLock()
        self._register_default_profiles()

    def _register_default_profiles(self) -> None:
        defaults = [
            AgentProfile(
                profile_id="asis-local",
                name="A.S.I.S. Local",
                agent_type="local",
                version="0.2.1",
                host="windows-host",
                capabilities=["inference", "voice", "web", "hardware"],
                supported_device_types=[],
                supported_platforms=[],
                max_assignments=100,
                metadata={"description": "General local agent on Windows host"},
            ),
            AgentProfile(
                profile_id="asis-offload",
                name="A.S.I.S. Offload",
                agent_type="offload",
                version="0.2.1",
                host="windows-host",
                capabilities=["offload", "inference", "camera", "gps", "heart_rate"],
                supported_device_types=["phone", "watch", "tablet", "sensor", "rover"],
                supported_platforms=["ios", "watchos", "android", "unknown"],
                max_assignments=50,
                metadata={"description": "Offload for low-capability devices"},
            ),
            AgentProfile(
                profile_id="tiviss-compat",
                name="T.I.V.I.S.S. Compat",
                agent_type="tiviss",
                version="0.1.0",
                host="windows-host",
                capabilities=["inference", "voice"],
                supported_device_types=["generic"],
                supported_platforms=[],
                max_assignments=20,
                metadata={"description": "Compat profile for T.I.V.I.S.S.-style devices"},
            ),
        ]
        for p in defaults:
            self._profiles[p.profile_id] = p
            self._profile_assignment_counts[p.profile_id] = 0

    def register_profile(self, profile: AgentProfile) -> AgentProfile:
        with self._lock:
            if profile.profile_id in self._profiles:
                raise ProfileAlreadyRegistered(f"Profile already registered: {profile.profile_id}")
            self._profiles[profile.profile_id] = profile
            self._profile_assignment_counts[profile.profile_id] = 0
            return profile

    def unregister_profile(self, profile_id: str) -> AgentProfile:
        with self._lock:
            try:
                profile = self._profiles.pop(profile_id)
            except KeyError as exc:
                raise ProfileNotFound(f"Profile not found: {profile_id}") from exc
            self._profile_assignment_counts.pop(profile_id, None)
            return profile

    def get_profile(self, profile_id: str) -> AgentProfile:
        with self._lock:
            try:
                return self._profiles[profile_id]
            except KeyError as exc:
                raise ProfileNotFound(f"Profile not found: {profile_id}") from exc

    def list_profiles(self) -> list[AgentProfile]:
        with self._lock:
            return list(self._profiles.values())

    def count(self) -> int:
        with self._lock:
            return len(self._profiles)

    def find_suitable_agent(
        self,
        device: Resource | dict[str, Any],
        *,
        profile_id: str | None = None,
    ) -> AgentProfile | None:
        """
        Find the best profile for a device without creating an assignment.

        If profile_id is supplied, that profile is returned when compatible
        and not at capacity.
        """
        # Normalize device
        if isinstance(device, dict):
            device_type = device.get("device_type") or device.get("metadata", {}).get("device_type", "generic")
            platform = device.get("platform") or device.get("metadata", {}).get("platform", "unknown")
            capabilities = device.get("capabilities", [])
        else:
            device_type = device.metadata.get("device_type", "generic")
            platform = device.metadata.get("platform", "unknown")
            capabilities = list(device.capabilities)

        with self._lock:
            if profile_id is not None:
                profile = self._profiles.get(profile_id)
                if profile is None:
                    return None
                # Check capacity
                if self._profile_assignment_counts.get(profile_id, 0) >= profile.max_assignments:
                    return None
                # Basic support check (non-strict: score >0 allowed)
                if profile.score_for(capabilities, device_type, platform) <= 0:
                    return None
                return profile

            # Score all profiles that have capacity
            candidates: list[tuple[int, AgentProfile]] = []
            for pid, profile in self._profiles.items():
                if self._profile_assignment_counts.get(pid, 0) >= profile.max_assignments:
                    continue
                score = profile.score_for(capabilities, device_type, platform)
                # At least minimal support: score must be >0 or fallback to asis-local
                if score > 0:
                    candidates.append((score, profile))

            if not candidates:
                # Fallback: return asis-local if it exists and has capacity
                fallback = self._profiles.get("asis-local")
                if fallback and self._profile_assignment_counts.get("asis-local", 0) < fallback.max_assignments:
                    return fallback
                return None

            # Highest score wins; tie-break by profile_id
            candidates.sort(key=lambda x: (x[0], x[1].profile_id), reverse=True)
            return candidates[0][1]

    def assign(
        self,
        device: Resource,
        *,
        profile_id: str | None = None,
        agent_id: str | None = None,
    ) -> tuple[Resource, Assignment]:
        """
        Assign a device to an agent.

        Creates an agent Resource on the Windows host and records the
        assignment. Returns (agent_resource, assignment).
        """
        if not isinstance(device, Resource):
            raise TypeError("assign requires a Resource device")

        with self._lock:
            # Check idempotency: if already assigned, return existing
            existing = self._assignments.get(device.resource_id)
            if existing is not None:
                agent = self._agent_resources.get(existing.agent_id)
                if agent is not None:
                    return agent, existing
                # Stale assignment without resource — clear it
                self._assignments.pop(device.resource_id, None)

            profile = self.find_suitable_agent(device, profile_id=profile_id)
            if profile is None:
                raise NoSuitableAgent(f"No suitable agent for device {device.resource_id}")

            # Create agent resource
            aid = agent_id or f"agent-{device.resource_id}-{uuid.uuid4().hex[:8]}"
            agent = create_agent_resource(
                agent_id=aid,
                name=f"{profile.name} for {device.name}",
                agent_type=profile.agent_type,
                version=profile.version,
                host=profile.host,
                target_device=device.resource_id,
                status="running",
                capabilities=list(profile.capabilities),
                metadata={
                    **dict(profile.metadata),
                    "profile_id": profile.profile_id,
                    "device_type": device.metadata.get("device_type"),
                    "platform": device.metadata.get("platform"),
                },
            )

            assignment = Assignment(
                device_id=device.resource_id,
                agent_id=aid,
                profile_id=profile.profile_id,
                metadata={"host": profile.host, "agent_type": profile.agent_type},
            )

            self._agent_resources[aid] = agent
            self._assignments[device.resource_id] = assignment
            self._profile_assignment_counts[profile.profile_id] = (
                self._profile_assignment_counts.get(profile.profile_id, 0) + 1
            )

            return agent, assignment

    def get_assignment(self, device_id: str) -> Assignment:
        with self._lock:
            try:
                return self._assignments[device_id]
            except KeyError as exc:
                raise AssignmentNotFound(f"No assignment for device: {device_id}") from exc

    def get_agent(self, agent_id: str) -> Resource:
        with self._lock:
            try:
                return self._agent_resources[agent_id]
            except KeyError as exc:
                raise AssignmentNotFound(f"Agent resource not found: {agent_id}") from exc

    def list_assignments(self) -> list[Assignment]:
        with self._lock:
            return list(self._assignments.values())

    def list_agents(self) -> list[Resource]:
        with self._lock:
            return list(self._agent_resources.values())

    def release(self, device_id: str) -> Assignment:
        """Release the agent assigned to a device."""
        with self._lock:
            assignment = self._assignments.pop(device_id, None)
            if assignment is None:
                raise AssignmentNotFound(f"No assignment for device: {device_id}")
            # Decrement profile count
            pid = assignment.profile_id
            if pid in self._profile_assignment_counts:
                self._profile_assignment_counts[pid] = max(0, self._profile_assignment_counts[pid] - 1)
            # Remove agent resource
            self._agent_resources.pop(assignment.agent_id, None)
            return assignment

    def clear(self) -> None:
        with self._lock:
            self._assignments.clear()
            self._agent_resources.clear()
            for pid in self._profile_assignment_counts:
                self._profile_assignment_counts[pid] = 0

    def assignment_count(self) -> int:
        with self._lock:
            return len(self._assignments)
