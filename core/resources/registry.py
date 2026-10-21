from collections.abc import Iterable
from functools import wraps
from threading import RLock

from core.errors import (
    ResourceAlreadyRegistered,
    ResourceNotFound,
)
from core.organization import OrganizationEngine

from .models import Resource


_UNSET = object()

def _synchronized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call

class ResourceRegistry:
    """Central registry of resources known to C.O.R.E."""

    def __init__(
        self,
        organization: OrganizationEngine | None = None,
    ) -> None:
        self._lock = RLock()
        self._resources: dict[str, Resource] = {}
        self._organization = organization

    @_synchronized
    def attach_organization(self, organization: OrganizationEngine) -> None:
        """Connect an organization engine for resource categorization."""

        self._organization = organization

    @_synchronized
    def register(
        self,
        resource: Resource,
        *,
        categorize: bool = True,
    ) -> Resource:
        if resource.resource_id in self._resources:
            raise ResourceAlreadyRegistered(
                f"Resource already registered: {resource.resource_id}"
            )

        self._resources[resource.resource_id] = resource

        if categorize and self._organization is not None:
            self._organization.categorize_resource(resource)

        return resource

    @_synchronized
    def unregister(self, resource_id: str) -> Resource:
        resource = self.get(resource_id)
        del self._resources[resource_id]

        if self._organization is not None:
            self._organization.remove_resource(resource_id)

        return resource

    @_synchronized
    def get(self, resource_id: str) -> Resource:
        try:
            return self._resources[resource_id]
        except KeyError as exc:
            raise ResourceNotFound(
                f"Resource not found: {resource_id}"
            ) from exc

    @_synchronized
    def update(
        self,
        resource_id: str,
        *,
        name: str | None = None,
        status: str | None = None,
        owner: str | None | object = _UNSET,
        source: str | None | object = _UNSET,
        capabilities: list[str] | None = None,
        metadata: dict | None = None,
        connection_info: dict | None = None,
    ) -> Resource:
        resource = self.get(resource_id)

        if name is not None:
            resource.name = name

        if status is not None:
            resource.status = status

        if owner is not _UNSET:
            resource.owner = owner

        if source is not _UNSET:
            resource.source = source

        if capabilities is not None:
            resource.capabilities = capabilities

        if metadata is not None:
            resource.metadata = metadata

        if connection_info is not None:
            resource.connection_info = connection_info
        if self._organization is not None:
            self._organization.categorize_resource(resource)

        return resource

    @_synchronized
    def mark_seen(self, resource_id: str) -> Resource:
        resource = self.get(resource_id)
        resource.mark_seen()
        return resource

    @_synchronized
    def list(
        self,
        *,
        resource_type: str | None = None,
        status: str | None = None,
    ) -> list[Resource]:
        resources = list(self._resources.values())

        if resource_type is not None:
            resources = [
                resource
                for resource in resources
                if resource.resource_type == resource_type
            ]

        if status is not None:
            resources = [
                resource
                for resource in resources
                if resource.status == status
            ]

        return resources

    @_synchronized
    def list_resources(self) -> list[Resource]:
        """Return all registered resources."""

        return list(self._resources.values())

    @_synchronized
    def discover(
        self,
        *,
        resource_type: str | None = None,
        status: str | None = None,
        owner: str | None = None,
        category: str | None = None,
    ) -> list[Resource]:
        """
        Discover resources matching optional criteria.

        Category-based discovery resolves through the organization engine.
        """

        resources = self.list(
            resource_type=resource_type,
            status=status,
        )

        if owner is not None:
            resources = [
                resource
                for resource in resources
                if resource.owner == owner
            ]

        if category is not None:
            if self._organization is None:
                return []
            resource_ids = {
                entry.resource_id
                for entry in self._organization.by_category(category)
                if entry.resource_id is not None
            }

            resources = [
                resource
                for resource in resources
                if resource.resource_id in resource_ids
            ]

        return resources

    @_synchronized
    def count(self) -> int:
        return len(self._resources)

    @_synchronized
    def clear(self) -> None:
        self._resources.clear()

        if self._organization is not None:
            self._organization.clear()

    @_synchronized
    def __iter__(self) -> Iterable[Resource]:
        return iter(list(self._resources.values()))
