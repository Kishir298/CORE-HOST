from collections.abc import Callable, Iterable
from typing import Any

from core.errors import (
    ServiceAlreadyRegistered,
    ServiceDependencyError,
    ServiceNotFound,
)

from .models import Service, ServiceStatus

ServiceHandler = Callable[..., Any]


class ServiceManager:
    """
    Manages the lifecycle, dependencies, and operations of C.O.R.E. services.

    A service may optionally expose named operation handlers. This allows
    C.O.R.E. to move beyond lifecycle bookkeeping and actually execute
    operations through managed services.
    """

    def __init__(self) -> None:
        self._services: dict[str, Service] = {}
        self._handlers: dict[str, dict[str, ServiceHandler]] = {}

    def register(self, service: Service) -> Service:
        """Register a new service."""

        if service.service_id in self._services:
            raise ServiceAlreadyRegistered(
                f"Service already registered: {service.service_id}"
            )

        self._services[service.service_id] = service
        self._handlers[service.service_id] = {}

        return service

    def unregister(self, service_id: str) -> Service:
        """Stop and remove a service."""

        service = self.get(service_id)

        if service.status in {
            ServiceStatus.RUNNING,
            ServiceStatus.STARTING,
            ServiceStatus.INITIALIZING,
        }:
            self.stop(service_id)

        del self._services[service_id]
        self._handlers.pop(service_id, None)

        return service

    def get(self, service_id: str) -> Service:
        """Return a registered service."""

        try:
            return self._services[service_id]
        except KeyError as exc:
            raise ServiceNotFound(
                f"Service not found: {service_id}"
            ) from exc

    def initialize(self, service_id: str) -> Service:
        """Initialize a service without starting it."""

        service = self.get(service_id)

        if service.status == ServiceStatus.RUNNING:
            return service

        service.status = ServiceStatus.INITIALIZING
        service.status = ServiceStatus.STOPPED

        return service

    def start(self, service_id: str) -> Service:
        """
        Start a service and all of its dependencies.

        Dependencies are started first according to their dependency graph.
        """

        service = self.get(service_id)

        if service.status == ServiceStatus.RUNNING:
            return service

        self._validate_dependencies(service)

        for dependency_id in self._dependency_order(service_id):
            dependency = self.get(dependency_id)

            if dependency.status != ServiceStatus.RUNNING:
                self.start(dependency_id)

        service.status = ServiceStatus.STARTING

        try:
            service.status = ServiceStatus.RUNNING
            service.mark_healthy()
        except Exception:
            service.status = ServiceStatus.FAILED
            service.mark_unhealthy()
            raise

        return service

    def stop(self, service_id: str) -> Service:
        """
        Stop a service and any running dependents.

        Dependents are stopped before their dependencies.
        """

        service = self.get(service_id)

        dependents = [
            other
            for other in self._services.values()
            if service_id in other.dependencies
            and other.status == ServiceStatus.RUNNING
        ]

        for dependent in dependents:
            self.stop(dependent.service_id)

        if service.status != ServiceStatus.RUNNING:
            service.status = ServiceStatus.STOPPED
            service.health = "unknown"
            return service

        service.status = ServiceStatus.STOPPING
        service.status = ServiceStatus.STOPPED
        service.health = "unknown"

        return service

    def restart(self, service_id: str) -> Service:
        """Restart a service."""

        self.stop(service_id)
        return self.start(service_id)

    def status(self, service_id: str) -> ServiceStatus:
        """Return the current service status."""

        return self.get(service_id).status

    def health_check(self, service_id: str) -> str:
        """Evaluate and return the current service health."""

        service = self.get(service_id)

        if service.status == ServiceStatus.RUNNING:
            service.mark_healthy()
        else:
            service.mark_unhealthy()

        return service.health

    def register_handler(
        self,
        service_id: str,
        operation: str,
        handler: ServiceHandler,
    ) -> None:
        """
        Register an operation handler for a service.

        Handlers are intentionally generic so services can define their own
        operation contracts while C.O.R.E. remains responsible for routing
        and lifecycle management.
        """

        if not operation:
            raise ValueError("Operation cannot be empty.")

        if not callable(handler):
            raise TypeError("Service handler must be callable.")

        self.get(service_id)

        self._handlers.setdefault(service_id, {})[operation] = handler

    def unregister_handler(
        self,
        service_id: str,
        operation: str,
    ) -> None:
        """Remove an operation handler from a service."""

        self.get(service_id)

        handlers = self._handlers.get(service_id, {})
        handlers.pop(operation, None)

    def has_handler(
        self,
        service_id: str,
        operation: str,
    ) -> bool:
        """Return whether a service exposes a specific operation."""

        self.get(service_id)

        return operation in self._handlers.get(service_id, {})

    def list_operations(
        self,
        service_id: str,
    ) -> list[str]:
        """Return all operations exposed by a service."""

        self.get(service_id)

        return list(
            self._handlers.get(service_id, {}).keys()
        )

    def execute(
        self,
        service_id: str,
        operation: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Execute an operation provided by a running service.

        A service must be running and must have a registered handler for the
        requested operation.
        """

        service = self.get(service_id)

        if service.status != ServiceStatus.RUNNING:
            raise RuntimeError(
                f"Service is not running: {service_id}"
            )

        handlers = self._handlers.get(service_id, {})
        handler = handlers.get(operation)

        if handler is None:
            raise ServiceNotFound(
                f"Operation not found: {service_id}.{operation}"
            )

        try:
            return handler(*args, **kwargs)
        except Exception:
            service.status = ServiceStatus.FAILED
            service.mark_unhealthy()
            raise

    def list(self) -> list[Service]:
        """Return all registered services."""

        return list(self._services.values())

    def list_services(self) -> list[Service]:
        """Return all registered services.

        Alias provided for the application and CLI layers.
        """

        return self.list()

    def count(self) -> int:
        """Return the number of registered services."""

        return len(self._services)

    def clear(self) -> None:
        """Remove all services and operation handlers."""

        self._services.clear()
        self._handlers.clear()

    def _validate_dependencies(self, service: Service) -> None:
        """Validate that every direct dependency exists."""

        for dependency_id in service.dependencies:
            if dependency_id not in self._services:
                raise ServiceDependencyError(
                    f"Missing dependency '{dependency_id}' "
                    f"for service '{service.service_id}'"
                )

    def _dependency_order(self, service_id: str) -> list[str]:
        """Resolve dependencies in startup order."""

        order: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(current_id: str) -> None:
            if current_id in visiting:
                raise ServiceDependencyError(
                    f"Circular dependency detected involving: {current_id}"
                )

            if current_id in visited:
                return

            visiting.add(current_id)

            service = self.get(current_id)

            for dependency_id in sorted(
                service.dependencies,
                key=lambda dependency: self.get(
                    dependency
                ).startup_priority,
            ):
                if dependency_id not in self._services:
                    raise ServiceDependencyError(
                        f"Missing dependency '{dependency_id}' "
                        f"for service '{current_id}'"
                    )

                visit(dependency_id)

            visiting.remove(current_id)
            visited.add(current_id)

            if current_id != service_id:
                order.append(current_id)

        visit(service_id)

        return order

    def __iter__(self) -> Iterable[Service]:
        """Iterate over registered services."""

        return iter(self._services.values())