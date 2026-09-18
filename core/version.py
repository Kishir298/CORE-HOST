"""
C.O.R.E. version and compatibility layer.

Preserves legacy versions while adding higher versions. Clients on
0.2.1 (plaintext, explicit assign, InMemory/File RESCS) continue to work;
0.3.0 adds TLS, auto-assign, Http fallback, and version negotiation.

Follow semver: MAJOR.MINOR.PATCH.
"""

from __future__ import annotations

from dataclasses import dataclass


__version__ = "0.3.0"

# Canonical alias: core.cli.main and docs reference CORE_VERSION.
CORE_VERSION = __version__

# All versions that have shipped or are supported for negotiation
SUPPORTED_VERSIONS = ["0.2.0", "0.2.1", "0.3.0"]
LEGACY_VERSIONS = ["0.1.0", "0.2.0", "0.2.1"]

# Minimum version that supports new features (TLS + auto-assign + Http)
MIN_VERSION_TLS = "0.3.0"
MIN_VERSION_AUTO_ASSIGN = "0.3.0"


@dataclass(frozen=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, version: str) -> "SemanticVersion":
        if not isinstance(version, str) or not version.strip():
            raise ValueError(f"Invalid version string: {version!r}")
        v = version.strip().lstrip("v")
        # Allow 0.2, 0.2.1, 0.3.0 etc.
        parts = v.split(".")
        if len(parts) == 2:
            parts.append("0")
        if len(parts) != 3:
            raise ValueError(f"Invalid version string: {version!r}")
        try:
            maj, min_, pat = (int(p) for p in parts)
        except ValueError as exc:
            raise ValueError(f"Invalid version string: {version!r}") from exc
        return cls(maj, min_, pat)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def __lt__(self, other: "SemanticVersion") -> bool:
        return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)

    def __le__(self, other: "SemanticVersion") -> bool:
        return (self.major, self.minor, self.patch) <= (other.major, other.minor, other.patch)

    def __gt__(self, other: "SemanticVersion") -> bool:
        return (self.major, self.minor, self.patch) > (other.major, other.minor, other.patch)

    def __ge__(self, other: "SemanticVersion") -> bool:
        return (self.major, self.minor, self.patch) >= (other.major, other.minor, other.patch)


def is_supported(version: str) -> bool:
    """Return whether a version is supported (including legacy)."""
    try:
        v = SemanticVersion.parse(version)
    except ValueError:
        return False
    return str(v) in SUPPORTED_VERSIONS or str(v) in LEGACY_VERSIONS


def is_legacy(version: str) -> bool:
    """Return whether a version is legacy (pre-0.3.0)."""
    try:
        v = SemanticVersion.parse(version)
    except ValueError:
        return True
    for legacy in LEGACY_VERSIONS:
        if str(v) == legacy:
            return True
    # Anything < 0.3.0 is legacy
    return v < SemanticVersion.parse("0.3.0")


def negotiate(client_version: str | None) -> str:
    """
    Negotiate the best version to use with a client.

    Legacy clients that send no version or an old version get 0.2.1
    semantics (plaintext, explicit assign). 0.3.0 clients get new features.
    Higher versions are clamped to the latest supported.
    """
    if not client_version:
        return "0.2.1"  # legacy default

    try:
        client_v = SemanticVersion.parse(client_version)
    except ValueError:
        return "0.2.1"

    # Clamp to latest supported
    latest = SemanticVersion.parse(SUPPORTED_VERSIONS[-1])
    if client_v > latest:
        return str(latest)
    # Client < 0.3.0 → legacy
    if client_v < SemanticVersion.parse("0.3.0"):
        # Return the largest legacy ≤ client, or 0.2.1 fallback
        for ver in reversed(LEGACY_VERSIONS):
            if SemanticVersion.parse(ver) <= client_v:
                return ver
        return "0.2.1"
    return str(client_v)


def supports_tls(version: str | None) -> bool:
    """Return whether a version supports TLS transport."""
    if not version:
        return False
    try:
        return SemanticVersion.parse(negotiate(version)) >= SemanticVersion.parse(MIN_VERSION_TLS)
    except Exception:
        return False


def supports_auto_assign(version: str | None) -> bool:
    """Return whether a version supports auto-assign."""
    if not version:
        return False
    try:
        return SemanticVersion.parse(negotiate(version)) >= SemanticVersion.parse(MIN_VERSION_AUTO_ASSIGN)
    except Exception:
        return False


def legacy_payload_adapter(payload: dict, target_version: str | None = None) -> dict:
    """
    Adapt a payload for legacy compatibility.

    Strips fields unknown to legacy versions while preserving higher-version
    fields when negotiating. This keeps old clients from breaking when a
    0.3.0 server adds new optional keys.
    """
    if not isinstance(payload, dict):
        return dict(payload) if payload else {}

    # If target is legacy, strip newer optional keys
    if is_legacy(target_version or "0.2.1"):
        # Strip 0.3.0-only keys that legacy handlers ignore anyway
        legacy = dict(payload)
        # These are already tolerated via **kwargs, but we strip to keep wire minimal
        # Keep core keys: operation, device_id, resource_id, name, resource_type, etc.
        for key in ("api_version", "client_version", "negotiated_version"):
            legacy.pop(key, None)
        return legacy

    # For 0.3.0, preserve all keys but ensure api_version is present
    adapted = dict(payload)
    if "api_version" not in adapted and target_version:
        adapted["api_version"] = target_version
    return adapted


__all__ = [
    "__version__",
    "CORE_VERSION",
    "SUPPORTED_VERSIONS",
    "LEGACY_VERSIONS",
    "MIN_VERSION_TLS",
    "MIN_VERSION_AUTO_ASSIGN",
    "SemanticVersion",
    "is_supported",
    "is_legacy",
    "negotiate",
    "supports_tls",
    "supports_auto_assign",
    "legacy_payload_adapter",
]
