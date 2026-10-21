"""C.O.R.E. organization index: categorization, discovery and linkage.

Organization is an in-memory indexing layer over ``ResourceRegistry``.
R.E.S.C.S. remains the persistence authority; nothing here persists.
"""

from __future__ import annotations

import copy
from threading import RLock
from typing import Any

from .models import OrganizationEntry


class OrganizationError(Exception):
    """Base organization error."""


class OrganizationValidationError(OrganizationError):
    """Raised when an organization entry or update violates the contract."""


class OrganizationEntryAlreadyExists(OrganizationError):
    """Raised when an organization entry already exists."""


class OrganizationEntryNotFound(OrganizationError):
    """Raised when an organization entry cannot be found."""


def _require_non_empty_str(value: Any, field: str) -> str:
    """Validate a required non-empty, non-whitespace string field."""
    if not isinstance(value, str) or not value or not value.strip():
        raise OrganizationValidationError(
            f"Invalid organization entry: {field!r} must be a "
            "non-empty string."
        )
    return value


def _require_optional_resource_id(value: Any) -> str | None:
    """Validate the optional ``resource_id`` linkage field."""
    if value is None:
        return None
    if not isinstance(value, str) or not value or not value.strip():
        raise OrganizationValidationError(
            "Invalid organization entry: 'resource_id' must be None or "
            "a non-empty string."
        )
    return value


def validate_organization_entry(entry: OrganizationEntry) -> OrganizationEntry:
    """Validate an entry at the Organization boundary.

    Normalizes a ``None`` metadata mapping to ``{}``. Raises
    :class:`OrganizationValidationError` on any violation.
    """
    if not isinstance(entry, OrganizationEntry):
        raise OrganizationValidationError(
            "Invalid organization entry: expected an OrganizationEntry."
        )
    _require_non_empty_str(entry.entry_id, "entry_id")
    _require_non_empty_str(entry.category, "category")
    _require_non_empty_str(entry.name, "name")
    _require_optional_resource_id(entry.resource_id)
    if entry.metadata is None:
        entry.metadata = {}
    if not isinstance(entry.metadata, dict):
        raise OrganizationValidationError(
            "Invalid organization entry: 'metadata' must be a dictionary."
        )
    return entry


def build_organization_metadata(resource: Any) -> dict[str, Any]:
    """Build the organization metadata contract for a resource.

    Contract (documented in ``docs/organization-rescs.md``)::

        resource_type, owner, source, status,
        capabilities (copied list),
        metadata (deep-copied dict),
        connection_info (deep-copied dict)

    ``last_seen`` / ``registered_at`` are intentionally excluded: they are
    C.O.R.E.-side lifecycle fields, not organizational identity. All
    mutable values are copied so later mutation of the ``Resource`` never
    leaks into the stored entry.
    """
    return {
        "resource_type": getattr(resource, "resource_type", None),
        "owner": getattr(resource, "owner", None),
        "source": getattr(resource, "source", None),
        "status": getattr(resource, "status", None),
        "capabilities": list(getattr(resource, "capabilities", None) or []),
        "metadata": copy.deepcopy(getattr(resource, "metadata", None) or {}),
        "connection_info": copy.deepcopy(
            getattr(resource, "connection_info", None) or {}
        ),
    }


def _validate_categorized_resource(resource: Any) -> None:
    """Validate the identity fields required for categorization."""
    _require_non_empty_str(
        getattr(resource, "resource_id", None), "resource.resource_id"
    )
    _require_non_empty_str(
        getattr(resource, "resource_type", None), "resource.resource_type"
    )
    _require_non_empty_str(getattr(resource, "name", None), "resource.name")


class OrganizationEngine:
    """Thread-safe index of organized C.O.R.E. information."""

    def __init__(self, registry=None) -> None:
        self._lock = RLock()
        self._entries: dict[str, OrganizationEntry] = {}
        self._registry = registry
        self._ingestor: Any | None = None

    def attach_registry(self, registry) -> None:
        """Attach a resource registry for resource discovery."""
        with self._lock:
            self._registry = registry

    def attach_ingestor(self, ingestor: Any) -> None:
        """Attach the R.E.S.C.S. ingestor for organization-facing writes."""
        with self._lock:
            self._ingestor = ingestor

    def add(self, entry: OrganizationEntry) -> OrganizationEntry:
        validate_organization_entry(entry)
        with self._lock:
            if entry.entry_id in self._entries:
                raise OrganizationEntryAlreadyExists(
                    f"Organization entry already exists: {entry.entry_id}"
                )
            self._entries[entry.entry_id] = entry
            return entry

    def get(self, entry_id: str) -> OrganizationEntry:
        with self._lock:
            try:
                return self._entries[entry_id]
            except KeyError as exc:
                raise OrganizationEntryNotFound(
                    f"Organization entry not found: {entry_id}"
                ) from exc

    def remove(self, entry_id: str) -> OrganizationEntry:
        with self._lock:
            try:
                entry = self._entries[entry_id]
            except KeyError as exc:
                raise OrganizationEntryNotFound(
                    f"Organization entry not found: {entry_id}"
                ) from exc
            del self._entries[entry_id]
            return entry

    def list(self) -> list[OrganizationEntry]:
        with self._lock:
            return list(self._entries.values())

    def by_category(self, category: str) -> list[OrganizationEntry]:
        with self._lock:
            return [
                entry
                for entry in self._entries.values()
                if entry.category == category
            ]

    def by_resource(self, resource_id: str) -> list[OrganizationEntry]:
        with self._lock:
            return [
                entry
                for entry in self._entries.values()
                if entry.resource_id == resource_id
            ]

    def discover(
        self,
        *,
        category: str | None = None,
        resource_id: str | None = None,
    ) -> list[OrganizationEntry]:
        """Discover organized entries without touching internals.

        Unified organization-facing discovery over the existing index:

        * neither argument → snapshot of all entries (same as ``list()``)
        * only ``category`` → entries in that category
        * only ``resource_id`` → entries linked to that resource
        * both → intersection of the two filters
        * unknown category/resource → ``[]`` (no exception)

        Pure discovery: performs no R.E.S.C.S. I/O, mutates nothing, and
        is idempotent. Returns a snapshot list; mutating it never affects
        the index. Thread-safe.
        """
        with self._lock:
            entries = list(self._entries.values())
        if category is not None:
            entries = [e for e in entries if e.category == category]
        if resource_id is not None:
            entries = [e for e in entries if e.resource_id == resource_id]
        return entries

    def categorize_resource(self, resource) -> OrganizationEntry:
        """
        Create or update an organization entry that links a resource.

        The entry category is taken from the resource type so resources can
        be discovered by category through the organization layer. The entry
        id is stable (``resource:<resource_id>``) so repeated calls update
        the same entry instead of duplicating it, including across
        resource-type changes.
        """
        _validate_categorized_resource(resource)
        entry_id = f"resource:{resource.resource_id}"
        metadata = build_organization_metadata(resource)
        candidate = OrganizationEntry(
            entry_id=entry_id,
            category=resource.resource_type,
            name=resource.name,
            resource_id=resource.resource_id,
            metadata=metadata,
        )
        validate_organization_entry(candidate)

        with self._lock:
            existing = self._entries.get(entry_id)
            if existing is not None:
                existing.category = candidate.category
                existing.name = candidate.name
                existing.resource_id = candidate.resource_id
                existing.metadata = candidate.metadata
                return existing
            self._entries[entry_id] = candidate
            return candidate

    def organize_resource(self, resource) -> OrganizationEntry:
        """First-class organization entry point for resource ingestion.

        Reuses :meth:`categorize_resource` (validate → normalize → register
        via the caller → categorize). Callers that care about organization
        rather than low-level registry manipulation should prefer this.
        """
        return self.categorize_resource(resource)

    def remove_resource(self, resource_id: str) -> None:
        """Remove organization entries linked to a resource."""
        with self._lock:
            for entry_id in [
                entry_id
                for entry_id, entry in self._entries.items()
                if entry.resource_id == resource_id
            ]:
                del self._entries[entry_id]

    def forget_resource(self, resource_id: str) -> Any:
        """Forget a resource representation (registry + organization).

        C.O.R.E.-side removal only: drops the registry entry (which
        cascades to organization entries) and never deletes from R.E.S.C.S.
        storage. A later ingestion/reconciliation restores it if R.E.S.C.S.
        still holds it. Idempotent only in the sense that a second call
        raises the registry's not-found error. Thread-safe (ingestor
        reference is copied out before delegating).
        """
        with self._lock:
            ingestor = self._ingestor
            registry = self._registry
        if ingestor is not None and hasattr(ingestor, "forget_resource"):
            return ingestor.forget_resource(resource_id)
        if registry is not None and hasattr(registry, "unregister"):
            return registry.unregister(resource_id)
        self.remove_resource(resource_id)
        return None

    def ingest_resource(self, resource_id: str) -> Any:
        """Ingest one R.E.S.C.S. resource through the attached ingestor.

        Talks to R.E.S.C.S. (fetch), validates/normalizes, then mutates
        C.O.R.E. (registry upsert + categorize). Idempotent: repeated calls
        update the same entry. Raises ``RescsResourceNotFound`` when
        R.E.S.C.S. explicitly reports absence, ``RescsUnavailable`` on
        backend failure (mutates nothing), ``InvalidResourceData`` on bad
        data, ``OrganizationError`` when no ingestor is attached.
        """
        with self._lock:
            ingestor = self._ingestor
        if ingestor is None or not hasattr(ingestor, "ingest_resource"):
            raise OrganizationError(
                "No resource ingestor is attached for ingestion."
            )
        return ingestor.ingest_resource(resource_id)

    def ingest_all(self) -> dict[str, Any]:
        """Ingest all R.E.S.C.S. resources through the attached ingestor.

        Bulk form of :meth:`ingest_resource`: deterministic
        ``resource_id`` order, ``{ingested, updated, failed, errors}``
        result, invalid items collected without aborting, backend failure
        raises ``RescsUnavailable``. Mutates C.O.R.E.; idempotent on
        unchanged state.
        """
        with self._lock:
            ingestor = self._ingestor
        if ingestor is None or not hasattr(ingestor, "ingest_all"):
            raise OrganizationError(
                "No resource ingestor is attached for ingestion."
            )
        return ingestor.ingest_all()

    def reconcile(self) -> dict[str, Any]:
        """Reconcile against authoritative R.E.S.C.S. state.

        Delegates to the attached ingestor's authoritative pipeline:
        adds missing, updates changed, heals unchanged, removes explicitly
        absent ids (registry + organization), reports invalid items, and
        raises ``RescsUnavailable`` with zero mutations on backend failure.
        Returns deterministic ``{added, updated, removed, unchanged,
        failed, errors}`` with sorted id lists. Idempotent.
        """
        with self._lock:
            ingestor = self._ingestor
        if ingestor is None or not hasattr(ingestor, "reconcile"):
            raise OrganizationError(
                "No resource ingestor is attached for reconciliation."
            )
        return ingestor.reconcile()

    def resource(self, resource_id: str) -> Any:
        """
        Return the underlying resource for an id (registry read-through).

        Resource discovery requires an attached registry and raises via the
        registry when the resource is unknown. The registry call happens
        outside the engine lock to avoid lock-ordering problems.
        """
        with self._lock:
            registry = self._registry
        if registry is None:
            raise OrganizationError(
                "No resource registry is attached for discovery."
            )
        return registry.get(resource_id)

    def update(
        self,
        entry_id: str,
        *,
        category: str | None = None,
        name: str | None = None,
        resource_id: str | None = None,
        metadata: dict | None = None,
    ) -> OrganizationEntry:
        if category is not None:
            _require_non_empty_str(category, "category")
        if name is not None:
            _require_non_empty_str(name, "name")
        if resource_id is not None:
            _require_optional_resource_id(resource_id)
            if resource_id is None:  # pragma: no cover - defensive
                raise OrganizationValidationError(
                    "Invalid organization entry: 'resource_id' must be None "
                    "or a non-empty string."
                )
        if metadata is not None and not isinstance(metadata, dict):
            raise OrganizationValidationError(
                "Invalid organization entry: 'metadata' must be a dictionary."
            )
        with self._lock:
            try:
                entry = self._entries[entry_id]
            except KeyError as exc:
                raise OrganizationEntryNotFound(
                    f"Organization entry not found: {entry_id}"
                ) from exc
            if category is not None:
                entry.category = category
            if name is not None:
                entry.name = name
            if resource_id is not None:
                entry.resource_id = resource_id
            if metadata is not None:
                entry.metadata = metadata
            return entry

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __iter__(self):
        with self._lock:
            snapshot = list(self._entries.values())
        return iter(snapshot)
