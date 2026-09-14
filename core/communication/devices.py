"""Authoritative C.O.R.E. device registry.

One logical record per ``device_id``. Built on the existing resource
infrastructure: every registration also mirrors into the injected
``ResourceRegistry`` via ``create_device_resource`` so discovery,
organization and R.E.S.C.S. persistence keep working. The device registry
remains the single authority for presence (``online``/``offline``),
``connection_id`` binding and ``last_seen`` timestamps.

Thread-safe: all lookup / registration / presence transitions hold one
``RLock`` so concurrent connections cannot create duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from core.resources.models import create_device_resource

from .protocol import (
    DEVICE_ALREADY_REGISTERED,
    DEVICE_STATUS_OFFLINE,
    DEVICE_STATUS_ONLINE,
    SUPPORTED_PROTOCOL_VERSION,
    validate_registration_payload,
)


class DeviceRegistryError(Exception):
    """Device registry failure carrying a protocol error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _sanitize_join_part(value: str) -> str:
    """Keep a join-name part human-readable (mirrors the client derivation)."""
    cleaned = "".join(
        c if (c.isalnum() or c in ("-", "_", ".")) else "-" for c in value.strip()
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-._") or "device"


def derive_join_name(device_name: str, device_id: str) -> str:
    """Derive the stable ``<device-name>-<short-device-id>`` join name.

    Used as the server-side fallback when a client registers without one
    (e.g. older v0.3.0 clients), keeping discovery labels stable.
    """
    return f"{_sanitize_join_part(device_name or device_id)}-{_sanitize_join_part(device_id)}"[:64]


@dataclass
class DeviceRecord:
    """Single authoritative record for one ``device_id``."""

    device_id: str
    device_name: str
    device_type: str = "generic"
    platform: str = "unknown"
    capabilities: list[str] = field(default_factory=list)
    status: str = DEVICE_STATUS_OFFLINE
    identity_id: str | None = None
    connection_id: str | None = None
    last_seen: datetime | None = None
    registered_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    protocol_version: str = "0.3.0"
    join_name: str = ""

    def touch(self, now: datetime | None = None) -> None:
        """Update last_seen to now (UTC)."""
        self.last_seen = now or datetime.now(timezone.utc)

    def to_discovery_entry(self) -> dict[str, Any]:
        """Discovery shape required by DEVICE_DISCOVER_RESPONSE."""
        return {
            "device_id": self.device_id,
            "join_name": self.join_name or derive_join_name(self.device_name, self.device_id),
            "device_name": self.device_name,
            "device_type": self.device_type,
            "platform": self.platform,
            "capabilities": list(self.capabilities),
            "status": self.status,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }

    def to_info_entry(self) -> dict[str, Any]:
        """Full shape for DEVICE_INFO_RESPONSE."""
        return self.to_discovery_entry()

    def to_dict(self) -> dict[str, Any]:
        """Return a complete serializable representation (persistence)."""
        return {
            "device_id": self.device_id,
            "join_name": self.join_name,
            "device_name": self.device_name,
            "device_type": self.device_type,
            "platform": self.platform,
            "capabilities": list(self.capabilities),
            "status": self.status,
            "identity_id": self.identity_id,
            "connection_id": self.connection_id,
            "last_seen": (
                self.last_seen.isoformat() if self.last_seen is not None else None
            ),
            "registered_at": self.registered_at.isoformat(),
            "protocol_version": self.protocol_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeviceRecord":
        """Reconstruct a record from :meth:`to_dict` output.

        Raises ``ValueError`` for missing/invalid identity fields.
        """
        if not isinstance(data, dict):
            raise ValueError("Device record must be a JSON object.")
        payload = {
            "device_id": data.get("device_id", ""),
            "device_name": data.get("device_name", ""),
            "device_type": data.get("device_type", "generic"),
            "platform": data.get("platform", "unknown"),
            "capabilities": data.get("capabilities", []),
            "protocol_version": data.get(
                "protocol_version", SUPPORTED_PROTOCOL_VERSION
            ),
        }
        code, message = validate_registration_payload(payload)
        if code is not None:
            raise ValueError(message or "Invalid persisted device record.")
        status = data.get("status", DEVICE_STATUS_OFFLINE)
        if status not in (DEVICE_STATUS_ONLINE, DEVICE_STATUS_OFFLINE):
            raise ValueError(f"Invalid device status: {status!r}.")
        last_seen = _parse_optional_datetime(data.get("last_seen"), "last_seen")
        registered_at = _parse_optional_datetime(
            data.get("registered_at"), "registered_at"
        ) or datetime.now(timezone.utc)
        capabilities = data.get("capabilities", [])
        stored_join = data.get("join_name")
        join_name = (
            stored_join.strip()
            if isinstance(stored_join, str) and stored_join.strip()
            else derive_join_name(payload["device_name"], payload["device_id"])
        )
        return cls(
            device_id=payload["device_id"],
            device_name=payload["device_name"],
            device_type=payload["device_type"] or "generic",
            platform=payload["platform"] or "unknown",
            capabilities=list(capabilities),
            status=status,
            identity_id=data.get("identity_id") or payload["device_id"],
            connection_id=data.get("connection_id"),
            last_seen=last_seen,
            registered_at=registered_at,
            protocol_version=payload["protocol_version"],
            join_name=join_name[:64],
        )

    def to_persistent_dict(
        self,
        *,
        token: str | None = None,
        permissions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return the sanitized identity snapshot for R.E.S.C.S. persistence.

        Runtime connection state (``status``, ``connection_id``) is
        intentionally excluded: restoration always starts offline.
        """
        return {
            "device_id": self.device_id,
            "join_name": self.join_name,
            "device_name": self.device_name,
            "device_type": self.device_type,
            "platform": self.platform,
            "capabilities": list(self.capabilities),
            "identity_id": self.identity_id or self.device_id,
            "protocol_version": self.protocol_version,
            "registered_at": self.registered_at.isoformat(),
            "last_seen": (
                self.last_seen.isoformat() if self.last_seen is not None else None
            ),
            "permissions": list(permissions or []),
            "token": token,
        }


def _parse_optional_datetime(value: Any, field_name: str) -> datetime | None:
    """Parse an ISO-8601 datetime or return None (raise on garbage)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if not isinstance(value, str):
        raise ValueError(f"Invalid {field_name}: must be an ISO-8601 string.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name}: {value!r}.") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class DeviceRegistry:
    """Thread-safe authoritative store of device records."""

    def __init__(
        self,
        resource_registry: Any | None = None,
        device_store: Any | None = None,
    ) -> None:
        self._devices: dict[str, DeviceRecord] = {}
        self._lock = RLock()
        self._resources = resource_registry
        # Optional persistence backend (a R.E.S.C.S. adapter exposing
        # persist_device/fetch_device/delete_device). Memory-only when None.
        self._store = device_store

    @property
    def device_store(self) -> Any | None:
        """Return the attached persistence backend, if any."""
        with self._lock:
            return self._store

    def set_device_store(self, store: Any | None) -> None:
        """Attach or replace the R.E.S.C.S. persistence backend."""
        with self._lock:
            self._store = store

    # -- registration (atomic) -------------------------------------------
    def register(
        self,
        *,
        device_id: str,
        device_name: str,
        device_type: str = "generic",
        platform: str = "unknown",
        capabilities: list[str] | None = None,
        protocol_version: str = "0.3.0",
        identity_id: str | None = None,
        connection_id: str | None = None,
        join_name: str | None = None,
    ) -> DeviceRecord:
        """Atomically register (or re-register after disconnect) a device.

        Only one *active* (online) connection may represent a device_id.
        A duplicate active registration raises ``DeviceRegistryError`` with
        code ``DEVICE_ALREADY_REGISTERED``. Reconnect after the previous
        connection went offline replaces the binding and returns online.
        ``join_name`` is optional: absent/blank values fall back to the
        ``<device-name>-<short-device-id>`` derivation so older clients
        keep working; a provided value is kept verbatim (stripped).
        """
        payload = {
            "device_id": device_id,
            "device_name": device_name,
            "device_type": device_type,
            "platform": platform,
            "capabilities": capabilities if capabilities is not None else [],
            "protocol_version": protocol_version,
        }
        code, msg = validate_registration_payload(payload)
        if code is not None:
            raise DeviceRegistryError(code, msg or "Invalid registration.")
        now = datetime.now(timezone.utc)
        resolved_join = (
            join_name.strip()
            if isinstance(join_name, str) and join_name.strip()
            else derive_join_name(device_name, device_id)
        )[:64]
        with self._lock:
            existing = self._devices.get(device_id)
            if existing is not None and existing.status == DEVICE_STATUS_ONLINE:
                raise DeviceRegistryError(
                    DEVICE_ALREADY_REGISTERED,
                    f"Device already registered: {device_id}",
                )
            if existing is not None:
                existing.device_name = device_name
                existing.device_type = device_type
                existing.platform = platform
                existing.capabilities = list(capabilities or [])
                existing.protocol_version = protocol_version
                existing.identity_id = identity_id or device_id
                existing.connection_id = connection_id
                existing.status = DEVICE_STATUS_ONLINE
                existing.last_seen = now
                # A reconnect keeps the established join_name unless the
                # client explicitly provides a (non-blank) new one.
                if isinstance(join_name, str) and join_name.strip():
                    existing.join_name = resolved_join
                elif not existing.join_name:
                    existing.join_name = resolved_join
                record = existing
            else:
                record = DeviceRecord(
                    device_id=device_id,
                    device_name=device_name,
                    device_type=device_type,
                    platform=platform,
                    capabilities=list(capabilities or []),
                    status=DEVICE_STATUS_ONLINE,
                    identity_id=identity_id or device_id,
                    connection_id=connection_id,
                    last_seen=now,
                    registered_at=now,
                    protocol_version=protocol_version,
                    join_name=resolved_join,
                )
                self._devices[device_id] = record
            self._mirror_to_resources(record)
            self._persist_record(record)
            return record

    def register_payload(
        self,
        payload: dict,
        *,
        identity_id: str | None,
        connection_id: str | None,
    ) -> DeviceRecord:
        """Validate + atomically register from a raw DEVICE_REGISTER payload."""
        code, msg = validate_registration_payload(payload)
        if code is not None:
            raise DeviceRegistryError(code, msg or "Invalid registration.")
        return self.register(
            device_id=payload["device_id"],
            device_name=payload["device_name"],
            device_type=payload.get("device_type", "generic"),
            platform=payload.get("platform", "unknown"),
            capabilities=payload.get("capabilities", []),
            protocol_version=payload.get("protocol_version", "0.3.0"),
            identity_id=identity_id,
            connection_id=connection_id,
            join_name=payload.get("join_name"),
        )

    # -- persistence ---------------------------------------------------------
    def persist_identity(
        self,
        device_id: str,
        *,
        token: str | None = None,
        permissions: list[str] | None = None,
    ) -> None:
        """Persist a registered device identity (with credentials) to R.E.S.C.S.

        Called after authentication material is available (e.g. by the TCP
        transport on registration). Unknown devices and missing backends
        are ignored; backend failures never raise.
        """
        with self._lock:
            record = self._devices.get(device_id)
            store = self._store
        if record is None or store is None:
            return
        try:
            stored: dict[str, Any] | None = None
            fetch = getattr(store, "fetch_device", None)
            if callable(fetch):
                try:
                    stored = fetch(device_id)
                except Exception:
                    stored = None
            if token is None and isinstance(stored, dict):
                token = stored.get("token")
            if permissions is None and isinstance(stored, dict):
                kept = stored.get("permissions")
                permissions = list(kept) if isinstance(kept, list) else []
            persist = getattr(store, "persist_device", None)
            if callable(persist):
                persist(
                    record.to_persistent_dict(token=token, permissions=permissions)
                )
        except Exception:
            pass

    def restore(self, data: dict[str, Any]) -> DeviceRecord:
        """Restore one persisted identity as an offline record.

        Never revives a live connection: ``status`` is forced offline and
        ``connection_id`` cleared. Re-raises ``ValueError`` for invalid
        data. Restoring over an online record leaves it untouched.
        """
        record = DeviceRecord.from_dict(data)
        with self._lock:
            existing = self._devices.get(record.device_id)
            if existing is not None and existing.status == DEVICE_STATUS_ONLINE:
                return existing
            record.status = DEVICE_STATUS_OFFLINE
            record.connection_id = None
            self._devices[record.device_id] = record
            self._mirror_to_resources(record)
            return record

    def restore_all(self, items: list[dict[str, Any]]) -> int:
        """Restore many persisted identities; returns the count restored.

        Invalid entries are skipped so one corrupt record cannot block
        startup restoration.
        """
        restored = 0
        for item in items or []:
            try:
                self.restore(item)
                restored += 1
            except Exception:
                continue
        return restored

    def _persist_record(self, record: DeviceRecord) -> None:
        """Persist a record snapshot, preserving stored credentials."""
        with self._lock:
            store = self._store
        if store is None:
            return
        try:
            stored: dict[str, Any] | None = None
            fetch = getattr(store, "fetch_device", None)
            if callable(fetch):
                try:
                    stored = fetch(record.device_id)
                except Exception:
                    stored = None
            token = stored.get("token") if isinstance(stored, dict) else None
            kept = stored.get("permissions") if isinstance(stored, dict) else None
            permissions = list(kept) if isinstance(kept, list) else []
            persist = getattr(store, "persist_device", None)
            if callable(persist):
                persist(
                    record.to_persistent_dict(token=token, permissions=permissions)
                )
        except Exception:
            pass

    # -- lookup ------------------------------------------------------------
    def get(self, device_id: str) -> DeviceRecord:
        with self._lock:
            try:
                return self._devices[device_id]
            except KeyError as exc:
                raise KeyError(f"Device not found: {device_id}") from exc

    def has(self, device_id: str) -> bool:
        with self._lock:
            return device_id in self._devices

    def list_devices(self, *, include_offline: bool = True) -> list[DeviceRecord]:
        with self._lock:
            devices = list(self._devices.values())
        if not include_offline:
            devices = [d for d in devices if d.status == DEVICE_STATUS_ONLINE]
        return devices

    def find_by_connection(self, connection_id: str) -> DeviceRecord | None:
        with self._lock:
            for record in self._devices.values():
                if record.connection_id == connection_id:
                    return record
        return None

    # -- presence ------------------------------------------------------------
    def mark_offline(self, device_id: str, connection_id: str | None = None) -> DeviceRecord | None:
        """Mark offline only if the stored connection matches (race-safe).

        Returns the record, or None if the device is unknown. A stale close
        for an older connection_id never marks a newer connection offline.
        """
        now = datetime.now(timezone.utc)
        with self._lock:
            record = self._devices.get(device_id)
            if record is None:
                return None
            if connection_id is not None and record.connection_id != connection_id:
                return record
            record.status = DEVICE_STATUS_OFFLINE
            record.connection_id = None
            record.last_seen = now
            self._mirror_to_resources(record)
            self._persist_record(record)
            return record

    def mark_offline_by_connection(self, connection_id: str) -> DeviceRecord | None:
        with self._lock:
            for record in self._devices.values():
                if record.connection_id == connection_id:
                    return self.mark_offline(record.device_id, connection_id)
        return None

    def update_last_seen(self, device_id: str, now: datetime | None = None) -> None:
        with self._lock:
            record = self._devices.get(device_id)
            if record is not None:
                record.last_seen = now or datetime.now(timezone.utc)
                self._mirror_to_resources(record)

    # -- counts / metrics ------------------------------------------------------
    def registered_count(self) -> int:
        with self._lock:
            return len(self._devices)

    def online_count(self) -> int:
        with self._lock:
            return sum(1 for d in self._devices.values() if d.status == DEVICE_STATUS_ONLINE)

    def offline_count(self) -> int:
        with self._lock:
            return sum(1 for d in self._devices.values() if d.status == DEVICE_STATUS_OFFLINE)

    def mark_all_offline(self) -> None:
        with self._lock:
            ids = [(d.device_id, d.connection_id) for d in self._devices.values()]
        for device_id, _cid in ids:
            self.mark_offline(device_id, None)
            with self._lock:
                record = self._devices.get(device_id)
                if record is not None:
                    record.connection_id = None

    def clear(self) -> None:
        with self._lock:
            self._devices.clear()

    def __len__(self) -> int:
        return self.registered_count()

    # -- resource mirror ---------------------------------------------------------
    def _mirror_to_resources(self, record: DeviceRecord) -> None:
        if self._resources is None:
            return
        try:
            existing = self._resources.get(record.device_id)
            self._resources.update(
                record.device_id,
                name=record.device_name,
                status=record.status,
                capabilities=list(record.capabilities),
                metadata={
                    **dict(getattr(existing, "metadata", {}) or {}),
                    "device_type": record.device_type,
                    "platform": record.platform,
                    "identity_id": record.identity_id,
                    "protocol_version": record.protocol_version,
                },
                connection_info={"connection_id": record.connection_id},
            )
            try:
                if record.last_seen is not None:
                    existing.last_seen = record.last_seen
            except Exception:
                pass
        except Exception:
            try:
                resource = create_device_resource(
                    device_id=record.device_id,
                    name=record.device_name,
                    device_type=record.device_type,
                    platform=record.platform,
                    capabilities=list(record.capabilities),
                    status=record.status,
                    connection_info={"connection_id": record.connection_id},
                    metadata={
                        "identity_id": record.identity_id,
                        "protocol_version": record.protocol_version,
                    },
                )
                if record.last_seen is not None:
                    resource.last_seen = record.last_seen
                resource.registered_at = record.registered_at
                self._resources.register(resource)
            except Exception:
                pass


__all__ = ["DeviceRecord", "DeviceRegistry", "DeviceRegistryError"]
