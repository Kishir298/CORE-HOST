"""Capability evaluation for reported device profiles (host side).

The host never probes devices itself; clients report their deterministic
capability profile (see ``client/capabilities.py`` in C.O.R.E.-CLIENT) and
the host evaluates it with the same thresholds for offload decisions.
Host-local system facts use stdlib only (no psutil dependency).
"""

from __future__ import annotations

import os
import platform
import shutil
from typing import Any

CAPABILITY_HIGH = "HIGH"
CAPABILITY_MEDIUM = "MEDIUM"
CAPABILITY_LOW = "LOW"
CAPABILITY_UNKNOWN = "UNKNOWN"

DEFAULT_POLICY = {
    "high_ram_total_gb": 24.0,
    "high_ram_available_gb": 8.0,
    "high_cpu_cores": 8,
    "medium_ram_total_gb": 8.0,
    "medium_ram_available_gb": 2.0,
    "medium_cpu_cores": 4,
}


def _gb(value: Any) -> float | None:
    return (value / (1024 ** 3)) if isinstance(value, int) and value >= 0 else None


def evaluate_class(
    profile: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
) -> str:
    """Deterministically classify a reported profile (mirrors the client)."""
    rules = dict(DEFAULT_POLICY)
    if policy:
        rules.update(policy)
    total = _gb(profile.get("ram_total_bytes"))
    available = _gb(profile.get("ram_available_bytes"))
    cores = profile.get("cpu_cores")
    if total is None or not isinstance(cores, int):
        return CAPABILITY_UNKNOWN
    if (
        total >= rules["high_ram_total_gb"]
        and cores >= rules["high_cpu_cores"]
        and (available is None or available >= rules["high_ram_available_gb"])
    ):
        return CAPABILITY_HIGH
    if (
        total >= rules["medium_ram_total_gb"]
        and cores >= rules["medium_cpu_cores"]
        and (available is None or available >= rules["medium_ram_available_gb"])
    ):
        return CAPABILITY_MEDIUM
    return CAPABILITY_LOW


def host_facts() -> dict[str, Any]:
    """Local host facts via stdlib only (unknowns stay None)."""
    disk_total = disk_free = None
    try:
        usage = shutil.disk_usage(os.path.abspath(os.sep))
        disk_total, disk_free = usage.total, usage.free
    except Exception:
        pass
    return {
        "hostname": platform.node() or None,
        "os": platform.system() or None,
        "os_release": platform.release() or None,
        "architecture": platform.machine() or None,
        "cpu_cores": os.cpu_count(),
        "python_version": platform.python_version(),
        "disk_total_bytes": disk_total,
        "disk_free_bytes": disk_free,
    }
