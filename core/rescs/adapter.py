from __future__ import annotations

import copy
import json
import time
import warnings
from abc import ABC, abstractmethod
from pathlib import Path
from threading import RLock
from typing import Any

from core.resources.models import Resource
from core.runtime.history import RuntimeRecord


class RescsAdapter(ABC):
    """
    Abstract persistence adapter for the Windows co-hosted R.E.S.C.S.

    The adapter is intentionally transport-agnostic and never imports
    R.E.S.C.S. internals. The default is in-memory; file and HTTP
    variants provide durability or cross-process sharing on the same
    32GB/1TB laptop.
    """

    @abstractmethod
    def persist_resource(self, resource: Resource) -> None:
        """Persist a resource snapshot."""

    @abstractmethod
    def fetch_resource(self, resource_id: str) -> Resource | None:
        """Fetch a persisted resource."""

    @abstractmethod
    def delete_resource(self, resource_id: str) -> None:
        """Delete a persisted resource."""

    @abstractmethod
    def list_resources(self) -> list[Resource]:
        """List all persisted resources."""

    @abstractmethod
    def persist_runtime(self, record: RuntimeRecord) -> None:
        """Persist a runtime history record."""

    @abstractmethod
    def list_runtimes(self) -> list[RuntimeRecord]:
        """List persisted runtime records."""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Return adapter health information."""

    @abstractmethod
    def clear(self) -> None:
        """Clear all persisted state."""

    # -- registered device identities --------------------------------------
    # Dedicated device records (plain JSON dicts, keyed by device_id).
    # ResourceRegistry remains a runtime mirror only, never the device
    # persistence authority. Default implementations raise so older
    # third-party adapters stay import-compatible until they opt in.

    def persist_device(self, device: dict[str, Any]) -> None:
        """Persist one sanitized device identity snapshot."""
        raise NotImplementedError("Device persistence is not supported.")

    def fetch_device(self, device_id: str) -> dict[str, Any] | None:
        """Fetch one persisted device identity, or None."""
        raise NotImplementedError("Device persistence is not supported.")

    def delete_device(self, device_id: str) -> None:
        """Delete one persisted device identity."""
        raise NotImplementedError("Device persistence is not supported.")

    def list_devices(self) -> list[dict[str, Any]]:
        """List all persisted device identities."""
        raise NotImplementedError("Device persistence is not supported.")


def _validate_device_dict(device: dict[str, Any]) -> dict[str, Any]:
    """Validate + snapshot a device identity dict for storage."""
    if not isinstance(device, dict):
        raise ValueError("Device identity must be a JSON object.")
    device_id = device.get("device_id")
    if not isinstance(device_id, str) or not device_id:
        raise ValueError("Device identity requires a non-empty device_id.")
    return json.loads(json.dumps(device))


class InMemoryRescsAdapter(RescsAdapter):
    """In-memory implementation (default, deterministic, no I/O)."""

    def __init__(self) -> None:
        self._resources: dict[str, Resource] = {}
        self._runtimes: dict[str, RuntimeRecord] = {}
        self._devices: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def persist_resource(self, resource: Resource) -> None:
        with self._lock:
            # Deep copy so later caller-side mutation cannot alias the
            # stored record (and vice versa).
            self._resources[resource.resource_id] = copy.deepcopy(resource)

    def fetch_resource(self, resource_id: str) -> Resource | None:
        with self._lock:
            found = self._resources.get(resource_id)
            # Deep copy: callers must persist explicitly; mutating a
            # fetched record never alters the store (symmetric with persist).
            return copy.deepcopy(found) if found is not None else None

    def delete_resource(self, resource_id: str) -> None:
        with self._lock:
            self._resources.pop(resource_id, None)

    def list_resources(self) -> list[Resource]:
        with self._lock:
            return copy.deepcopy(list(self._resources.values()))

    def persist_runtime(self, record: RuntimeRecord) -> None:
        with self._lock:
            self._runtimes[record.entity_id] = copy.deepcopy(record)

    def list_runtimes(self) -> list[RuntimeRecord]:
        with self._lock:
            return copy.deepcopy(list(self._runtimes.values()))

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "adapter": "memory",
                "resources": len(self._resources),
                "runtimes": len(self._runtimes),
                "devices": len(self._devices),
                "healthy": True,
            }

    def clear(self) -> None:
        with self._lock:
            self._resources.clear()
            self._runtimes.clear()
            self._devices.clear()

    def persist_device(self, device: dict[str, Any]) -> None:
        snapshot = _validate_device_dict(device)
        with self._lock:
            self._devices[snapshot["device_id"]] = snapshot

    def fetch_device(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._devices.get(device_id)
            return json.loads(json.dumps(found)) if found is not None else None

    def delete_device(self, device_id: str) -> None:
        with self._lock:
            self._devices.pop(device_id, None)

    def list_devices(self) -> list[dict[str, Any]]:
        with self._lock:
            return [json.loads(json.dumps(d)) for d in self._devices.values()]


class FileRescsAdapter(RescsAdapter):
    """
    File-backed adapter for Windows host persistence across restarts.

    Stores JSON at ``var/rescs.json`` (or custom ``path``) using
    pathlib for Windows compatibility. File writes are atomic via
    temporary file + replace. Failures are surfaced as exceptions for
    the caller to handle as degraded health.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        default = Path("var") / "rescs.json"
        self._path = Path(path) if path else default
        self._lock = RLock()
        self._memory = InMemoryRescsAdapter()
        # Ensure parent directory exists
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            # Never silently lose data: preserve the corrupt file for
            # forensics as <name>.corrupt.<ts> and start empty.
            try:
                ts = int(time.time())
                corrupt = self._path.with_name(
                    f"{self._path.name}.corrupt.{ts}"
                )
                self._path.replace(corrupt)
                warnings.warn(
                    f"corrupt RESCS file {self._path} preserved as {corrupt}: {exc}",
                    UserWarning,
                    stacklevel=2,
                )
            except Exception:
                pass
            return
        if not isinstance(data, dict):
            try:
                ts = int(time.time())
                corrupt = self._path.with_name(
                    f"{self._path.name}.corrupt.{ts}"
                )
                self._path.replace(corrupt)
                warnings.warn(
                    f"corrupt RESCS file {self._path} (not an object) "
                    f"preserved as {corrupt}",
                    UserWarning,
                    stacklevel=2,
                )
            except Exception:
                pass
            return

        # Hydrate resources
        for item in data.get("resources", []):
            try:
                # Reconstruct Resource from dict
                resource = Resource(
                    resource_id=item["resource_id"],
                    name=item["name"],
                    resource_type=item["resource_type"],
                    status=item.get("status", "offline"),
                    owner=item.get("owner"),
                    source=item.get("source"),
                    capabilities=item.get("capabilities", []),
                    metadata=item.get("metadata", {}),
                    connection_info=item.get("connection_info", {}),
                )
                # Preserve timestamps if available
                if item.get("last_seen"):
                    from datetime import datetime

                    try:
                        resource.last_seen = datetime.fromisoformat(  # type: ignore
                            item["last_seen"]
                        )
                    except Exception:
                        pass
                if item.get("registered_at"):
                    try:
                        resource.registered_at = datetime.fromisoformat(  # type: ignore
                            item["registered_at"]
                        )
                    except Exception:
                        pass
                self._memory.persist_resource(resource)
            except Exception:
                continue

        # Hydrate runtimes (lightweight - store as RuntimeRecord with same fields)
        from datetime import datetime

        for item in data.get("runtimes", []):
            try:
                record = RuntimeRecord(
                    entity_id=item["entity_id"],
                    entity_type=item["entity_type"],
                    status=item.get("status", "running"),
                    metadata=item.get("metadata", {}),
                )
                # Restore times/durations if present
                if item.get("start_time"):
                    record.start_time = datetime.fromisoformat(item["start_time"])  # type: ignore
                if item.get("end_time"):
                    record.end_time = datetime.fromisoformat(item["end_time"])  # type: ignore
                if item.get("duration") is not None:
                    record.duration = item["duration"]  # type: ignore
                self._memory.persist_runtime(record)
            except Exception:
                continue

        # Hydrate registered device identities (tolerate per-item corruption)
        for item in data.get("devices", []):
            try:
                if isinstance(item, dict) and item.get("device_id"):
                    self._memory.persist_device(item)
            except Exception:
                continue

    def _save(self) -> None:
        data = {
            "resources": [r.to_dict() for r in self._memory.list_resources()],
            "runtimes": [r.to_dict() for r in self._memory.list_runtimes()],
            "devices": self._memory.list_devices(),
        }
        # Atomic write via temp file
        temp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            temp.parent.mkdir(parents=True, exist_ok=True)
            with temp.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            temp.replace(self._path)
        except Exception as exc:
            raise IOError(f"Failed to persist R.E.S.C.S. file: {exc}") from exc

    def persist_resource(self, resource: Resource) -> None:
        with self._lock:
            self._memory.persist_resource(resource)
            self._save()

    def fetch_resource(self, resource_id: str) -> Resource | None:
        with self._lock:
            return self._memory.fetch_resource(resource_id)

    def delete_resource(self, resource_id: str) -> None:
        with self._lock:
            self._memory.delete_resource(resource_id)
            self._save()

    def list_resources(self) -> list[Resource]:
        with self._lock:
            return self._memory.list_resources()

    def persist_runtime(self, record: RuntimeRecord) -> None:
        with self._lock:
            self._memory.persist_runtime(record)
            self._save()

    def list_runtimes(self) -> list[RuntimeRecord]:
        with self._lock:
            return self._memory.list_runtimes()

    def persist_device(self, device: dict[str, Any]) -> None:
        with self._lock:
            self._memory.persist_device(device)
            self._save()

    def fetch_device(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._memory.fetch_device(device_id)

    def delete_device(self, device_id: str) -> None:
        with self._lock:
            self._memory.delete_device(device_id)
            self._save()

    def list_devices(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._memory.list_devices()

    def health(self) -> dict[str, Any]:
        with self._lock:
            base = self._memory.health()
            base["adapter"] = "file"
            base["path"] = str(self._path)
            # Check writability
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                healthy = self._path.parent.exists()
            except Exception:
                healthy = False
            base["healthy"] = healthy
            return base

    def clear(self) -> None:
        with self._lock:
            self._memory.clear()
            self._save()


class HttpRescsAdapter(RescsAdapter):
    """
    HTTP adapter for cross-process R.E.S.C.S. on the Windows laptop.

    Delegates persistence over HTTP to a co-hosted R.E.S.C.S. service
    (e.g. ``http://localhost:8081``) using the standard library only.
    When the endpoint is unreachable the adapter falls back to an
    in-memory cache so local operation is undisturbed while health
    correctly reports ``healthy=False`` until the remote is reachable.

    Contract (JSON over HTTP):
      POST   /resources          {resource dict}
      GET    /resources          -> {resources:[...]} or [...]
      GET    /resources/{id}     -> {resource dict} or 404
      DELETE /resources/{id}
      POST   /runtimes           {record dict}
      GET    /runtimes           -> {runtimes:[...]} or [...]
      GET    /health             -> {healthy:bool, resources:int, runtimes:int}
      POST   /clear
      POST   /devices            {device identity dict}
      GET    /devices            -> {devices:[...]} or [...]
      GET    /devices/{id}       -> {device dict} or 404
      DELETE /devices/{id}
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:8081",
        timeout: float = 2.0,
        fallback: bool = True,
    ) -> None:
        self._endpoint = endpoint.rstrip("/") if endpoint else "http://localhost:8081"
        self._timeout = float(timeout) if timeout else 2.0
        self._fallback_enabled = bool(fallback)
        self._fallback = InMemoryRescsAdapter()
        self._lock = RLock()
        self._last_error: str | None = None
        self._healthy: bool | None = None

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self._endpoint + path

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> Any | None:
        import urllib.error
        import urllib.request

        url = self._url(path)
        data = None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                text = raw.decode("utf-8")
                if not text.strip():
                    return None
                try:
                    return json.loads(text)
                except Exception:
                    return text
        except urllib.error.HTTPError as exc:
            # 404 is not an error for fetch – return None
            if exc.code == 404:
                return None
            raise IOError(f"R.E.S.C.S. HTTP {method} {path} -> {exc.code} {exc.reason}") from exc
        except Exception as exc:
            raise IOError(f"R.E.S.C.S. HTTP {method} {path} failed: {exc}") from exc

    def _resource_from_dict(self, data: dict[str, Any]) -> Resource:
        from datetime import datetime

        resource = Resource(
            resource_id=data["resource_id"],
            name=data["name"],
            resource_type=data["resource_type"],
            status=data.get("status", "offline"),
            owner=data.get("owner"),
            source=data.get("source"),
            capabilities=data.get("capabilities", []),
            metadata=data.get("metadata", {}),
            connection_info=data.get("connection_info", {}),
        )
        if data.get("last_seen"):
            try:
                resource.last_seen = datetime.fromisoformat(data["last_seen"])  # type: ignore
            except Exception:
                pass
        if data.get("registered_at"):
            try:
                resource.registered_at = datetime.fromisoformat(data["registered_at"])  # type: ignore
            except Exception:
                pass
        return resource

    def _record_from_dict(self, data: dict[str, Any]) -> RuntimeRecord:
        from datetime import datetime

        record = RuntimeRecord(
            entity_id=data["entity_id"],
            entity_type=data["entity_type"],
            status=data.get("status", "running"),
            metadata=data.get("metadata", {}),
        )
        if data.get("start_time"):
            try:
                record.start_time = datetime.fromisoformat(data["start_time"])  # type: ignore
            except Exception:
                pass
        if data.get("end_time"):
            try:
                record.end_time = datetime.fromisoformat(data["end_time"])  # type: ignore
            except Exception:
                pass
        if data.get("duration") is not None:
            try:
                record.duration = data["duration"]  # type: ignore
            except Exception:
                pass
        return record

    def persist_resource(self, resource: Resource) -> None:
        with self._lock:
            # Always keep fallback in sync
            self._fallback.persist_resource(resource)
            try:
                self._request("POST", "/resources", resource.to_dict())
                self._last_error = None
                self._healthy = True
            except Exception as exc:
                self._last_error = str(exc)
                self._healthy = False
                if not self._fallback_enabled:
                    raise

    def fetch_resource(self, resource_id: str) -> Resource | None:
        with self._lock:
            try:
                result = self._request("GET", f"/resources/{resource_id}")
                if result is None:
                    return self._fallback.fetch_resource(resource_id) if self._fallback_enabled else None
                # Server may wrap in {"resource": {...}} or return dict directly
                if isinstance(result, dict) and "resource" in result and isinstance(result["resource"], dict):
                    result = result["resource"]
                if isinstance(result, dict) and "resource_id" in result:
                    self._last_error = None
                    self._healthy = True
                    return self._resource_from_dict(result)
                return self._fallback.fetch_resource(resource_id) if self._fallback_enabled else None
            except Exception as exc:
                self._last_error = str(exc)
                self._healthy = False
                if self._fallback_enabled:
                    return self._fallback.fetch_resource(resource_id)
                raise

    def delete_resource(self, resource_id: str) -> None:
        with self._lock:
            self._fallback.delete_resource(resource_id)
            try:
                self._request("DELETE", f"/resources/{resource_id}")
                self._last_error = None
                self._healthy = True
            except Exception as exc:
                self._last_error = str(exc)
                self._healthy = False
                if not self._fallback_enabled:
                    raise

    def list_resources(self) -> list[Resource]:
        with self._lock:
            try:
                result = self._request("GET", "/resources")
                if result is None:
                    return self._fallback.list_resources()
                # Normalize: {resources:[...]} or {data:[...]} or [...]
                raw_list = None
                if isinstance(result, list):
                    raw_list = result
                elif isinstance(result, dict):
                    for key in ("resources", "data", "items"):
                        if key in result and isinstance(result[key], list):
                            raw_list = result[key]
                            break
                    if raw_list is None and "resource_id" in result:
                        raw_list = [result]
                if raw_list is not None:
                    self._last_error = None
                    self._healthy = True
                    out: list[Resource] = []
                    for item in raw_list:
                        try:
                            if isinstance(item, dict) and "resource_id" in item:
                                out.append(self._resource_from_dict(item))
                        except Exception:
                            continue
                    # Sync fallback for offline use
                    # Keep fallback consistent but don't replace if remote empty prematurely
                    return out
                return self._fallback.list_resources()
            except Exception as exc:
                self._last_error = str(exc)
                self._healthy = False
                return self._fallback.list_resources()

    def persist_runtime(self, record: RuntimeRecord) -> None:
        with self._lock:
            self._fallback.persist_runtime(record)
            try:
                self._request("POST", "/runtimes", record.to_dict())
                self._last_error = None
                self._healthy = True
            except Exception as exc:
                self._last_error = str(exc)
                self._healthy = False
                if not self._fallback_enabled:
                    raise

    def list_runtimes(self) -> list[RuntimeRecord]:
        with self._lock:
            try:
                result = self._request("GET", "/runtimes")
                if result is None:
                    return self._fallback.list_runtimes()
                raw_list = None
                if isinstance(result, list):
                    raw_list = result
                elif isinstance(result, dict):
                    for key in ("runtimes", "records", "data", "items"):
                        if key in result and isinstance(result[key], list):
                            raw_list = result[key]
                            break
                    if raw_list is None and "entity_id" in result:
                        raw_list = [result]
                if raw_list is not None:
                    self._last_error = None
                    self._healthy = True
                    out: list[RuntimeRecord] = []
                    for item in raw_list:
                        try:
                            if isinstance(item, dict) and "entity_id" in item:
                                out.append(self._record_from_dict(item))
                        except Exception:
                            continue
                    return out
                return self._fallback.list_runtimes()
            except Exception as exc:
                self._last_error = str(exc)
                self._healthy = False
                return self._fallback.list_runtimes()

    def health(self) -> dict[str, Any]:
        with self._lock:
            try:
                result = self._request("GET", "/health")
                if isinstance(result, dict):
                    # Remote health is authoritative when reachable
                    healthy = bool(result.get("healthy", True))
                    self._healthy = healthy
                    self._last_error = None
                    # Normalize keys
                    result.setdefault("adapter", "http")
                    result.setdefault("endpoint", self._endpoint)
                    return result
                # If no JSON, still healthy if request succeeded
                self._healthy = True
                self._last_error = None
                return {
                    "adapter": "http",
                    "endpoint": self._endpoint,
                    "healthy": True,
                    "resources": len(self._fallback.list_resources()),
                    "runtimes": len(self._fallback.list_runtimes()),
                    "devices": len(self._fallback.list_devices()),
                }
            except Exception as exc:
                self._last_error = str(exc)
                self._healthy = False
                return {
                    "adapter": "http",
                    "endpoint": self._endpoint,
                    "healthy": False,
                    "error": str(exc),
                    "fallback_resources": len(self._fallback.list_resources()),
                    "fallback_runtimes": len(self._fallback.list_runtimes()),
                    "fallback_devices": len(self._fallback.list_devices()),
                }

    def persist_device(self, device: dict[str, Any]) -> None:
        snapshot = _validate_device_dict(device)
        with self._lock:
            self._fallback.persist_device(snapshot)
            try:
                self._request("POST", "/devices", snapshot)
                self._last_error = None
                self._healthy = True
            except Exception as exc:
                self._last_error = str(exc)
                self._healthy = False
                if not self._fallback_enabled:
                    raise

    def fetch_device(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                result = self._request("GET", f"/devices/{device_id}")
                if result is None:
                    return self._fallback.fetch_device(device_id) if self._fallback_enabled else None
                if isinstance(result, dict) and "device" in result and isinstance(result["device"], dict):
                    result = result["device"]
                if isinstance(result, dict) and result.get("device_id"):
                    self._last_error = None
                    self._healthy = True
                    return result
                return self._fallback.fetch_device(device_id) if self._fallback_enabled else None
            except Exception as exc:
                self._last_error = str(exc)
                self._healthy = False
                if self._fallback_enabled:
                    return self._fallback.fetch_device(device_id)
                raise

    def delete_device(self, device_id: str) -> None:
        with self._lock:
            self._fallback.delete_device(device_id)
            try:
                self._request("DELETE", f"/devices/{device_id}")
                self._last_error = None
                self._healthy = True
            except Exception as exc:
                self._last_error = str(exc)
                self._healthy = False
                if not self._fallback_enabled:
                    raise

    def list_devices(self) -> list[dict[str, Any]]:
        with self._lock:
            try:
                result = self._request("GET", "/devices")
                if result is None:
                    return self._fallback.list_devices()
                raw_list = None
                if isinstance(result, list):
                    raw_list = result
                elif isinstance(result, dict):
                    for key in ("devices", "data", "items"):
                        if key in result and isinstance(result[key], list):
                            raw_list = result[key]
                            break
                    if raw_list is None and result.get("device_id"):
                        raw_list = [result]
                if raw_list is not None:
                    self._last_error = None
                    self._healthy = True
                    out: list[dict[str, Any]] = []
                    for item in raw_list:
                        try:
                            if isinstance(item, dict) and item.get("device_id"):
                                out.append(item)
                        except Exception:
                            continue
                    return out
                return self._fallback.list_devices()
            except Exception as exc:
                self._last_error = str(exc)
                self._healthy = False
                return self._fallback.list_devices()

    def clear(self) -> None:
        with self._lock:
            self._fallback.clear()
            try:
                # Try standard clear endpoint, fall back to DELETE collection
                try:
                    self._request("POST", "/clear")
                except Exception:
                    self._request("DELETE", "/resources")
                    self._request("DELETE", "/runtimes")
                    try:
                        self._request("DELETE", "/devices")
                    except Exception:
                        pass
                self._last_error = None
                self._healthy = True
            except Exception as exc:
                self._last_error = str(exc)
                self._healthy = False
                if not self._fallback_enabled:
                    raise


__all__ = [
    "RescsAdapter",
    "InMemoryRescsAdapter",
    "FileRescsAdapter",
    "HttpRescsAdapter",
]
