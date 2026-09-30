from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Any


class EntityType(str, Enum):
    DEVICE = "device"
    AGENT = "agent"
    SERVICE = "service"
    CONNECTION = "connection"


class RuntimeStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class RuntimeRecord:
    """
    Structured runtime history entry for cross-system use.

    Tracks a single lifecycle interval for a device, agent, service, or
    connection on the Windows host.
    """

    entity_id: str
    entity_type: EntityType | str
    start_time: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    end_time: datetime | None = None
    duration: float | None = None
    status: str = RuntimeStatus.RUNNING.value
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.entity_id:
            raise ValueError("entity_id cannot be empty.")

        # Normalize entity_type to string value
        if isinstance(self.entity_type, Enum):
            object.__setattr__(self, "entity_type", self.entity_type.value)

        if self.start_time.tzinfo is None:
            object.__setattr__(
                self,
                "start_time",
                self.start_time.replace(tzinfo=timezone.utc),
            )

        if self.end_time is not None and self.end_time.tzinfo is None:
            object.__setattr__(
                self,
                "end_time",
                self.end_time.replace(tzinfo=timezone.utc),
            )

        # Ensure metadata is a plain dict copy
        object.__setattr__(self, "metadata", dict(self.metadata))

    def finish(
        self,
        status: str = RuntimeStatus.STOPPED.value,
        end_time: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Mark the record as completed and compute duration."""

        finish_time = end_time or datetime.now(timezone.utc)
        if finish_time.tzinfo is None:
            finish_time = finish_time.replace(tzinfo=timezone.utc)

        object.__setattr__(self, "end_time", finish_time)
        object.__setattr__(self, "status", status)

        if metadata:
            merged = dict(self.metadata)
            merged.update(metadata)
            object.__setattr__(self, "metadata", merged)

        delta = (self.end_time - self.start_time).total_seconds()
        object.__setattr__(self, "duration", max(0.0, delta))

    def to_dict(self) -> dict[str, Any]:
        """Return a serializable representation."""

        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type
            if isinstance(self.entity_type, str)
            else self.entity_type.value,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration": self.duration,
            "status": self.status,
            "metadata": dict(self.metadata),
        }


class RuntimeHistory:
    """
    Thread-safe, in-memory runtime history store for the Windows host.

    Suitable for 32GB RAM / 1TB storage laptop. Persistence to R.E.S.C.S.
    is delegated to the adapter layer; this store is the local authoritative
    ledger used by health and service layers.
    """

    def __init__(self) -> None:
        self._records: dict[str, RuntimeRecord] = {}
        # Completed records in order of completion
        self._completed: list[RuntimeRecord] = []
        self._lock = RLock()

    def start(
        self,
        entity_id: str,
        entity_type: EntityType | str,
        metadata: dict[str, Any] | None = None,
        start_time: datetime | None = None,
    ) -> RuntimeRecord:
        """Begin tracking a runtime interval."""

        if not entity_id:
            raise ValueError("entity_id cannot be empty.")

        record = RuntimeRecord(
            entity_id=entity_id,
            entity_type=entity_type,
            start_time=start_time or datetime.now(timezone.utc),
            status=RuntimeStatus.RUNNING.value,
            metadata=dict(metadata or {}),
        )

        with self._lock:
            # If already tracking, finish previous as failed before starting new
            existing = self._records.get(entity_id)
            if existing is not None and existing.end_time is None:
                existing.finish(status=RuntimeStatus.FAILED.value)
                self._completed.append(existing)

            self._records[entity_id] = record

        return record

    def end(
        self,
        entity_id: str,
        status: str = RuntimeStatus.STOPPED.value,
        metadata: dict[str, Any] | None = None,
        end_time: datetime | None = None,
    ) -> RuntimeRecord:
        """Complete tracking for an entity."""

        with self._lock:
            record = self._records.get(entity_id)
            if record is None:
                raise KeyError(f"No runtime record for entity: {entity_id}")

            if record.end_time is not None:
                return record

            record.finish(status=status, end_time=end_time, metadata=metadata)
            self._completed.append(record)

            return record

    def get(self, entity_id: str) -> RuntimeRecord:
        """Return a record by entity id."""

        with self._lock:
            try:
                return self._records[entity_id]
            except KeyError as exc:
                raise KeyError(
                    f"No runtime record for entity: {entity_id}"
                ) from exc

    def list_active(self) -> list[RuntimeRecord]:
        """Return currently running records."""

        with self._lock:
            return [
                record
                for record in self._records.values()
                if record.end_time is None
            ]

    def list_completed(self) -> list[RuntimeRecord]:
        """Return completed records in completion order."""

        with self._lock:
            return list(self._completed)

    def list_all(self) -> list[RuntimeRecord]:
        """Return all records (active + completed)."""

        with self._lock:
            return list(self._records.values())

    def count(self) -> int:
        """Return total number of tracked entities."""

        with self._lock:
            return len(self._records)

    def active_count(self) -> int:
        """Return number of active intervals."""

        with self._lock:
            return sum(
                1 for r in self._records.values() if r.end_time is None
            )

    def clear(self) -> None:
        """Remove all records."""

        with self._lock:
            self._records.clear()
            self._completed.clear()


__all__ = [
    "EntityType",
    "RuntimeHistory",
    "RuntimeRecord",
    "RuntimeStatus",
]
