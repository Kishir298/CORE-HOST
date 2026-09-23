"""R.E.S.C.S. -> C.O.R.E. ingestion boundary for the Organization layer.

R.E.S.C.S. remains the persistent storage authority. This module defines
the single contract C.O.R.E. expects from R.E.S.C.S. data, validates and
normalizes external responses into the existing ``Resource`` model (no
duplicate model), and organizes them through the existing
``ResourceRegistry`` / ``OrganizationEngine`` path.

Data flow::

    R.E.S.C.S. adapter (fetch_resource / list_resources)
        -> validate_rescs_resource (external input, strict)
        -> normalize_resource (C.O.R.E. Resource, storage extras dropped)
        -> ResourceRegistry (register new / update existing)
        -> OrganizationEngine.categorize_resource (stable resource:<id>)

Backend failures are translated to C.O.R.E.-level errors and never
treated as deletions. Only an explicit missing resource (fetch -> None)
may remove the associated C.O.R.E.-side entry.
"""

from __future__ import annotations

from typing import Any

from core.errors import ResourceNotFound
from core.resources.models import Resource

from .engine import OrganizationEngine, OrganizationError

# Fields C.O.R.E. requires from every R.E.S.C.S. resource representation.
REQUIRED_RESOURCE_FIELDS = ("resource_id", "resource_type", "name")


class IngestionError(OrganizationError):
    """Base error for the R.E.S.C.S. ingestion boundary."""


class InvalidResourceData(IngestionError):
    """Raised when a R.E.S.C.S. response violates the resource contract."""


class RescsUnavailable(IngestionError):
    """Raised when R.E.S.C.S. cannot be reached (never a deletion)."""


class RescsResourceNotFound(IngestionError):
    """Raised when R.E.S.C.S. explicitly reports a resource as missing."""


def _require_non_empty_str(data: dict, field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise InvalidResourceData(
            f"Invalid R.E.S.C.S. resource: {field!r} must be a non-empty string."
        )
    return value


def validate_rescs_resource(data: Any) -> dict[str, Any]:
    """Validate external R.E.S.C.S. data against the resource contract.

    Accepts a plain dict (wire/storage shape) or an existing ``Resource``.
    Returns a normalized plain dict with exactly the contract fields.
    Raises :class:`InvalidResourceData` on any violation; never invents
    values for missing required fields.
    """
    if isinstance(data, Resource):
        data = data.to_dict()
    if not isinstance(data, dict):
        raise InvalidResourceData(
            "Invalid R.E.S.C.S. resource: expected a JSON object."
        )
    resource_id = _require_non_empty_str(data, "resource_id")
    resource_type = _require_non_empty_str(data, "resource_type")
    name = _require_non_empty_str(data, "name")

    owner = data.get("owner")
    if owner is not None and (not isinstance(owner, str) or not owner):
        raise InvalidResourceData(
            "Invalid R.E.S.C.S. resource: 'owner' must be a non-empty "
            "string when present."
        )
    source = data.get("source")
    if source is not None and (not isinstance(source, str) or not source):
        raise InvalidResourceData(
            "Invalid R.E.S.C.S. resource: 'source' must be a non-empty "
            "string when present."
        )
    capabilities = data.get("capabilities", [])
    if not isinstance(capabilities, list) or not all(
        isinstance(item, str) for item in capabilities
    ):
        raise InvalidResourceData(
            "Invalid R.E.S.C.S. resource: 'capabilities' must be a list "
            "of strings when present."
        )
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise InvalidResourceData(
            "Invalid R.E.S.C.S. resource: 'metadata' must be an object "
            "when present."
        )
    return {
        "resource_id": resource_id,
        "resource_type": resource_type,
        "name": name,
        "owner": owner,
        "source": source,
        "capabilities": list(capabilities),
        "metadata": dict(metadata),
    }


def normalize_resource(data: Any) -> Resource:
    """Normalize validated R.E.S.C.S. data into a C.O.R.E. ``Resource``.

    Deterministic: the same logical resource always yields the same
    identity fields (``resource_id`` -> entry ``resource:<id>`` via
    ``categorize_resource``). Storage-specific extras (storage paths,
    internal database ids, backend timestamps) are intentionally dropped;
    R.E.S.C.S. remains their owner and they can always be re-fetched.
    ``registered_at``/``last_seen`` are C.O.R.E.-side lifecycle fields.
    """
    validated = validate_rescs_resource(data)
    return Resource(
        resource_id=validated["resource_id"],
        name=validated["name"],
        resource_type=validated["resource_type"],
        owner=validated["owner"],
        source=validated["source"],
        capabilities=validated["capabilities"],
        metadata=validated["metadata"],
    )


class ResourceIngestor:
    """Move R.E.S.C.S. resources into the C.O.R.E. organization index.

    Depends only on the C.O.R.E.-side adapter interface
    (``fetch_resource`` / ``list_resources``); never on database or HTTP
    internals. Organization stays an index/discovery layer: nothing here
    persists.
    """

    def __init__(
        self,
        adapter: Any,
        registry: Any | None = None,
        organization: OrganizationEngine | None = None,
    ) -> None:
        if adapter is None:
            raise ValueError("ResourceIngestor requires a R.E.S.C.S. adapter.")
        self._adapter = adapter
        self._registry = registry
        self._organization = organization
        if organization is not None:
            attach = getattr(organization, "attach_ingestor", None)
            if callable(attach):
                # Deliberately unguarded: a failing attach means the
                # organization wiring itself is broken and must surface
                # instead of leaving the engine silently detached.
                attach(self)

    @property
    def adapter(self) -> Any:
        """Return the R.E.S.C.S. adapter this ingestor reads through."""
        return self._adapter

    def _require_registry(self) -> Any:
        if self._registry is None:
            raise IngestionError(
                "No resource registry is attached for ingestion."
            )
        return self._registry

    def _fetch(self, resource_id: str) -> Any | None:
        try:
            return self._adapter.fetch_resource(resource_id)
        except Exception as exc:
            raise RescsUnavailable(
                f"R.E.S.C.S. unavailable while fetching {resource_id!r}."
            ) from exc

    def _upsert_normalized(self, normalized: Resource) -> tuple[Resource, bool]:
        """Register or update one normalized resource; return (resource, created).

        A changed ``resource_type`` re-registers so the organization
        category (derived from type) stays exact under the stable entry id
        ``resource:<id>``. Otherwise the existing object is updated in
        place and re-categorized so the organization entry refreshes.
        """
        registry = self._require_registry()
        resource_id = normalized.resource_id
        try:
            existing = registry.get(resource_id)
        except ResourceNotFound:
            registry.register(normalized)
            return normalized, True
        if existing.resource_type != normalized.resource_type:
            # Category is derived from type: re-register so the
            # organization index stays exact. Runtime presence fields
            # live on the old object and are intentionally not carried.
            registry.unregister(resource_id)
            registry.register(normalized)
            return normalized, True
        registry.update(
            resource_id,
            name=normalized.name,
            owner=normalized.owner,
            source=normalized.source,
            capabilities=list(normalized.capabilities),
            metadata=dict(normalized.metadata),
        )
        if self._organization is not None:
            self._organization.categorize_resource(existing)
        return existing, False

    @staticmethod
    def _normalized_equals(first: Resource, second: Resource) -> bool:
        """Compare the organization-relevant fields of two resources."""
        return (
            first.name == second.name
            and first.resource_type == second.resource_type
            and first.owner == second.owner
            and first.source == second.source
            and list(first.capabilities or []) == list(second.capabilities or [])
            and dict(first.metadata or {}) == dict(second.metadata or {})
        )

    def _current_resources(self) -> dict[str, Resource]:
        """Snapshot the current C.O.R.E.-side resources by id."""
        registry = self._require_registry()
        if hasattr(registry, "list_resources"):
            items = registry.list_resources()
        else:
            items = registry.list()
        return {item.resource_id: item for item in (items or [])}

    def ingest_resource(self, resource_id: str) -> Resource:
        """Retrieve one resource and organize it (idempotent).

        Repeated calls for the same id update the existing entry instead
        of duplicating it. A missing resource removes the C.O.R.E.-side
        entry if present and raises :class:`RescsResourceNotFound`.
        Backend failures raise :class:`RescsUnavailable` and touch nothing.
        """
        if not isinstance(resource_id, str) or not resource_id:
            raise InvalidResourceData(
                "Invalid resource_id: must be a non-empty string."
            )
        registry = self._require_registry()
        stored = self._fetch(resource_id)
        if stored is None:
            try:
                registry.unregister(resource_id)
            except ResourceNotFound:
                pass
            raise RescsResourceNotFound(
                f"R.E.S.C.S. reports no such resource: {resource_id!r}."
            )
        normalized = normalize_resource(stored)
        resource, _ = self._upsert_normalized(normalized)
        return resource

    def reconcile(self) -> dict[str, Any]:
        """Reconcile C.O.R.E. state against authoritative R.E.S.C.S. state.

        R.E.S.C.S. is authoritative: resources present there are added or
        updated here; resources explicitly absent there are removed here
        (registry + organization entries). A backend failure raises
        :class:`RescsUnavailable` and mutates nothing. Invalid items are
        reported without aborting the run and never cause deletion.

        Returns a deterministic summary with sorted id lists::

            {"added": [...], "updated": [...], "removed": [...],
             "unchanged": [...], "failed": [...], "errors": {...}}
        """
        registry = self._require_registry()
        try:
            stored_all = self._adapter.list_resources()
        except Exception as exc:
            raise RescsUnavailable(
                "R.E.S.C.S. unavailable while listing resources."
            ) from exc
        authoritative: dict[str, Resource] = {}
        failed: list[str] = []
        errors: dict[str, str] = {}
        for idx, item in enumerate(sorted(stored_all or [], key=lambda i: self._sort_key(i))):
            try:
                normalized = normalize_resource(item)
            except InvalidResourceData as exc:
                label = self._describe(item)
                # Disambiguate <unknown> collisions with index; keep the
                # first "<unknown>" stable for backward compatibility.
                if label in errors:
                    if label == "<unknown>":
                        label = f"<unknown-{idx}>"
                    else:
                        label = f"{label}-{idx}"
                    base, suffix = label, 0
                    while label in errors:
                        suffix += 1
                        label = f"{base}-{suffix}"
                failed.append(label)
                errors[label] = str(exc)
                continue
            authoritative[normalized.resource_id] = normalized
        current = self._current_resources()
        added: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []
        for resource_id in sorted(authoritative):
            normalized = authoritative[resource_id]
            existing = current.get(resource_id)
            if existing is None:
                resource, _ = self._upsert_normalized(normalized)
                added.append(resource.resource_id)
                continue
            if self._normalized_equals(existing, normalized):
                # Still re-categorize to heal any drifted organization entry
                # without counting it as an update.
                if self._organization is not None:
                    try:
                        self._organization.categorize_resource(existing)
                    except Exception:
                        pass
                unchanged.append(resource_id)
                continue
            self._upsert_normalized(normalized)
            updated.append(resource_id)
        removed: list[str] = []
        for resource_id in sorted(current):
            if resource_id not in authoritative:
                try:
                    registry.unregister(resource_id)
                except ResourceNotFound:
                    pass
                removed.append(resource_id)
        return {
            "added": sorted(added),
            "updated": sorted(updated),
            "removed": sorted(removed),
            "unchanged": sorted(unchanged),
            "failed": sorted(failed),
            "errors": dict(sorted(errors.items())),
        }

    def ingest_all(self) -> dict[str, Any]:
        """Retrieve every resource and organize it, deterministically.

        Resources are processed in ``resource_id`` order. Invalid items
        are collected (``failed``) without aborting the run; a backend
        failure aborts with :class:`RescsUnavailable`.
        """
        self._require_registry()  # existence is resolved per item below
        try:
            stored_all = self._adapter.list_resources()
        except Exception as exc:
            raise RescsUnavailable(
                "R.E.S.C.S. unavailable while listing resources."
            ) from exc
        summary: dict[str, Any] = {
            "ingested": 0,
            "updated": 0,
            "failed": 0,
            "errors": {},
        }
        items = sorted(stored_all or [], key=lambda item: self._sort_key(item))
        for idx, item in enumerate(items):
            try:
                before = self._registry_count(item)
                self.ingest_resource(self._resource_id_of(item))
                if before:
                    summary["updated"] += 1
                else:
                    summary["ingested"] += 1
            except RescsUnavailable:
                raise
            except (InvalidResourceData, RescsResourceNotFound) as exc:
                summary["failed"] += 1
                label = self._describe(item)
                if label in summary["errors"]:
                    if label == "<unknown>":
                        label = f"<unknown-{idx}>"
                    else:
                        label = f"{label}-{idx}"
                    base, suffix = label, 0
                    while label in summary["errors"]:
                        suffix += 1
                        label = f"{base}-{suffix}"
                summary["errors"][label] = str(exc)
        return summary

    def forget_resource(self, resource_id: str) -> Resource:
        """Remove the C.O.R.E.-side entry for an explicitly deleted resource.

        This only drops the registry/organization entry; it never deletes
        from R.E.S.C.S. storage. Raises :class:`ResourceNotFound` when the
        resource is unknown to C.O.R.E.
        """
        registry = self._require_registry()
        return registry.unregister(resource_id)

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _sort_key(item: Any) -> str:
        if isinstance(item, Resource):
            return item.resource_id
        if isinstance(item, dict):
            value = item.get("resource_id")
            return value if isinstance(value, str) else ""
        return ""

    @staticmethod
    def _resource_id_of(item: Any) -> str:
        if isinstance(item, Resource):
            return item.resource_id
        if isinstance(item, dict):
            value = item.get("resource_id")
            if isinstance(value, str) and value:
                return value
        raise InvalidResourceData(
            "Invalid R.E.S.C.S. resource: 'resource_id' must be a "
            "non-empty string."
        )

    def _registry_count(self, item: Any) -> bool:
        """Return whether the item is already known (update vs ingest)."""
        registry = self._require_registry()
        try:
            registry.get(self._resource_id_of(item))
            return True
        except ResourceNotFound:
            return False
        except InvalidResourceData:
            return False

    @staticmethod
    def _describe(item: Any) -> str:
        if isinstance(item, Resource):
            return item.resource_id
        if isinstance(item, dict):
            value = item.get("resource_id")
            return value if isinstance(value, str) and value else "<unknown>"
        return "<unknown>"


__all__ = [
    "REQUIRED_RESOURCE_FIELDS",
    "IngestionError",
    "InvalidResourceData",
    "RescsUnavailable",
    "RescsResourceNotFound",
    "validate_rescs_resource",
    "normalize_resource",
    "ResourceIngestor",
]
