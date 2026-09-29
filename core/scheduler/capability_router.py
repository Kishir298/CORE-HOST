from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import AgentProfile


@dataclass
class ModelProfile:
    """Describes a model profile with its requirements and capabilities."""
    profile_id: str
    name: str
    model_id: str
    size_gb: float
    min_ram_total_gb: float
    min_ram_available_gb: float
    min_cpu_cores: int = 2
    requires_gpu: bool = False
    gpu_type: str | None = None  # "cuda", "metal", "vulkan", "rocm"
    capabilities: list[str] = field(default_factory=list)
    supported_device_types: list[str] = field(default_factory=list)
    supported_platforms: list[str] = field(default_factory=list)
    max_concurrent: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise ValueError("profile_id cannot be empty.")
        if not self.model_id:
            raise ValueError("model_id cannot be empty.")
        self.capabilities = list(self.capabilities or [])
        self.supported_device_types = list(self.supported_device_types or [])
        self.supported_platforms = list(self.supported_platforms or [])

    def matches_capabilities(self, device_caps: dict[str, Any]) -> bool:
        """Check if device capabilities meet this model's requirements."""
        # Check RAM
        ram_total = device_caps.get("ram_total_gb", 0)
        ram_available = device_caps.get("ram_available_gb", 0)
        if ram_total < self.min_ram_total_gb:
            return False
        if ram_available < self.min_ram_available_gb:
            return False

        # Check CPU
        cpu_cores = device_caps.get("cpu_cores", 0)
        if cpu_cores < self.min_cpu_cores:
            return False

        # Check GPU if required
        if self.requires_gpu:
            gpu = device_caps.get("gpu", {})
            if not gpu or not gpu.get("available"):
                return False
            if self.gpu_type and gpu.get("type") != self.gpu_type:
                return False

        # Check device type support
        if self.supported_device_types:
            device_type = device_caps.get("device_type", "generic")
            if device_type not in self.supported_device_types:
                return False

        # Check platform support
        if self.supported_platforms:
            platform = device_caps.get("platform", "unknown")
            if platform not in self.supported_platforms:
                return False

        return True

    def score_for(self, device_caps: dict[str, Any]) -> int:
        """Score how well this model fits the device (higher = better fit)."""
        if not self.matches_capabilities(device_caps):
            return -1

        score = 0

        # Bonus for exact RAM match (closer to available is better)
        ram_available = device_caps.get("ram_available_gb", 0)
        ram_diff = abs(ram_available - self.min_ram_available_gb)
        score += max(0, 10 - int(ram_diff))

        # Bonus for GPU match
        if self.requires_gpu:
            gpu = device_caps.get("gpu", {})
            if gpu.get("available") and gpu.get("type") == self.gpu_type:
                score += 20

        # Bonus for exact device type match
        if self.supported_device_types:
            device_type = device_caps.get("device_type", "generic")
            if device_type in self.supported_device_types:
                score += 10

        # Bonus for exact platform match
        if self.supported_platforms:
            platform = device_caps.get("platform", "unknown")
            if platform in self.supported_platforms:
                score += 5

        # Prefer smaller models when multiple fit (efficiency)
        score += max(0, 20 - int(self.size_gb))

        return score

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "model_id": self.model_id,
            "size_gb": self.size_gb,
            "min_ram_total_gb": self.min_ram_total_gb,
            "min_ram_available_gb": self.min_ram_available_gb,
            "min_cpu_cores": self.min_cpu_cores,
            "requires_gpu": self.requires_gpu,
            "gpu_type": self.gpu_type,
            "capabilities": list(self.capabilities),
            "supported_device_types": list(self.supported_device_types),
            "supported_platforms": list(self.supported_platforms),
            "max_concurrent": self.max_concurrent,
            "metadata": dict(self.metadata),
        }


# Default model profiles that ship with C.O.R.E.
DEFAULT_MODEL_PROFILES: list[ModelProfile] = [
    ModelProfile(
        profile_id="asis-local-heavy",
        name="A.S.I.S. Local Heavy",
        model_id="qwen3-coder:30b",
        size_gb=18.0,
        min_ram_total_gb=24.0,
        min_ram_available_gb=8.0,
        min_cpu_cores=8,
        requires_gpu=True,
        gpu_type="cuda",
        capabilities=["inference", "coding", "reasoning", "voice"],
        supported_device_types=["desktop", "server"],
        supported_platforms=["windows", "linux"],
        max_concurrent=1,
        metadata={"description": "Heavy local coding agent for powerful workstations"},
    ),
    ModelProfile(
        profile_id="asis-local-medium",
        name="A.S.I.S. Local Medium",
        model_id="qwen2.5-coder:14b",
        size_gb=9.0,
        min_ram_total_gb=12.0,
        min_ram_available_gb=4.0,
        min_cpu_cores=4,
        requires_gpu=False,
        capabilities=["inference", "coding", "reasoning"],
        supported_device_types=["desktop", "laptop"],
        supported_platforms=["windows", "linux", "macos"],
        max_concurrent=2,
        metadata={"description": "Medium local coding agent for standard workstations"},
    ),
    ModelProfile(
        profile_id="asis-local-light",
        name="A.S.I.S. Local Light",
        model_id="qwen3:14b",
        size_gb=9.0,
        min_ram_total_gb=8.0,
        min_ram_available_gb=2.0,
        min_cpu_cores=2,
        requires_gpu=False,
        capabilities=["inference", "chat", "web"],
        supported_device_types=["desktop", "laptop"],
        supported_platforms=["windows", "linux", "macos"],
        max_concurrent=3,
        metadata={"description": "Light local agent for basic tasks"},
    ),
    ModelProfile(
        profile_id="asis-offload-phone",
        name="A.S.I.S. Phone Offload",
        model_id="qwen3-coder:30b",
        size_gb=18.0,
        min_ram_total_gb=24.0,
        min_ram_available_gb=8.0,
        min_cpu_cores=8,
        requires_gpu=True,
        gpu_type="cuda",
        capabilities=["offload", "inference", "camera", "gps", "heart_rate"],
        supported_device_types=["phone", "tablet"],
        supported_platforms=["ios", "android"],
        max_concurrent=1,
        metadata={"description": "Offload for mobile devices - runs on host"},
    ),
    ModelProfile(
        profile_id="asis-offload-watch",
        name="A.S.I.S. Watch Offload",
        model_id="qwen2.5-coder:14b",
        size_gb=9.0,
        min_ram_total_gb=12.0,
        min_ram_available_gb=4.0,
        min_cpu_cores=4,
        requires_gpu=False,
        capabilities=["offload", "inference", "voice"],
        supported_device_types=["watch", "sensor"],
        supported_platforms=["watchos", "unknown"],
        max_concurrent=2,
        metadata={"description": "Offload for wearables and sensors"},
    ),
]


class CapabilityRouter:
    """
    Routes device connections to appropriate model profiles based on capabilities.

    This extends the AgentScheduler with model-aware routing. Devices report
    their capabilities (RAM, CPU, GPU, platform) and the router selects
    the best model profile for either local execution or host offload.
    """

    def __init__(self) -> None:
        self._model_profiles: dict[str, ModelProfile] = {}
        self._profile_usage: dict[str, int] = {}
        self._register_default_model_profiles()

    def _register_default_model_profiles(self) -> None:
        for p in DEFAULT_MODEL_PROFILES:
            self._model_profiles[p.profile_id] = p
            self._profile_usage[p.profile_id] = 0

    def register_model_profile(self, profile: ModelProfile) -> ModelProfile:
        if profile.profile_id in self._model_profiles:
            raise ValueError(f"Model profile already registered: {profile.profile_id}")
        self._model_profiles[profile.profile_id] = profile
        self._profile_usage[profile.profile_id] = 0
        return profile

    def get_model_profile(self, profile_id: str) -> ModelProfile | None:
        return self._model_profiles.get(profile_id)

    def list_model_profiles(self) -> list[ModelProfile]:
        return list(self._model_profiles.values())

    def select_model_profile(
        self,
        device_caps: dict[str, Any],
        preferred_profile_id: str | None = None,
    ) -> ModelProfile | None:
        """
        Select the best model profile for a device based on capabilities.

        Args:
            device_caps: Dict with keys like ram_total_gb, ram_available_gb,
                        cpu_cores, gpu (dict), device_type, platform
            preferred_profile_id: Optional preferred profile to check first

        Returns:
            Best matching ModelProfile or None if no match
        """
        # If preferred profile specified, check it first
        if preferred_profile_id:
            profile = self._model_profiles.get(preferred_profile_id)
            if profile and profile.matches_capabilities(device_caps):
                if self._profile_usage.get(profile_id, 0) < profile.max_concurrent:
                    return profile

        # Score all compatible profiles
        candidates: list[tuple[int, ModelProfile]] = []
        for profile in self._model_profiles.values():
            if self._profile_usage.get(profile.profile_id, 0) >= profile.max_concurrent:
                continue
            score = profile.score_for(device_caps)
            if score > 0:
                candidates.append((score, profile))

        if not candidates:
            return None

        # Highest score wins
        candidates.sort(key=lambda x: (x[0], x[1].profile_id), reverse=True)
        return candidates[0][1]

    def record_usage(self, profile_id: str) -> None:
        """Record that a profile is being used."""
        if profile_id in self._profile_usage:
            self._profile_usage[profile_id] += 1

    def release_usage(self, profile_id: str) -> None:
        """Release usage of a profile."""
        if profile_id in self._profile_usage:
            self._profile_usage[profile_id] = max(0, self._profile_usage[profile_id] - 1)

    def get_usage(self, profile_id: str) -> int:
        return self._profile_usage.get(profile_id, 0)