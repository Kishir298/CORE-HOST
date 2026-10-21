from threading import RLock

from .models import Permission


class SecurityPolicy:
    """
    Declares the permissions required to invoke service operations.

    The policy maps a service/operation pair to a single required
    Permission. Operations without a registered requirement are open to any
    authenticated caller. A denied operation never reaches its service
    implementation.

    Enforcement is opt-in. A policy may carry requirements while enforcement
    remains inactive; callers consult ``is_enforced`` before denying access so
    that behaviour is unchanged until a security boundary is configured.
    """

    def __init__(self) -> None:
        self._requirements: dict[str, dict[str, Permission]] = {}
        self._enforced = False
        self._lock = RLock()

    @property
    def enforced(self) -> bool:
        """Return whether operations are actively authorization-gated."""

        with self._lock:
            return self._enforced

    def set_enforced(self, enforced: bool) -> None:
        """Enable or disable active enforcement of the declared policy."""

        if not isinstance(enforced, bool):
            raise TypeError("Enforcement flag must be a boolean.")

        with self._lock:
            self._enforced = enforced

    def grant(
        self,
        service_id: str,
        operation: str,
        permission: Permission,
    ) -> None:
        """Register the permission required for a service operation."""

        self._validate(service_id, operation)

        if not isinstance(permission, Permission):
            raise TypeError("Permission must be a Permission value.")

        with self._lock:
            self._requirements.setdefault(service_id, {})[operation] = (
                permission
            )

    def revoke(self, service_id: str, operation: str) -> None:
        """Remove the requirement for a service operation."""

        self._validate(service_id, operation)

        with self._lock:
            operations = self._requirements.get(service_id)

            if operations is None:
                return

            operations.pop(operation, None)

            if not operations:
                self._requirements.pop(service_id, None)

    def required(
        self,
        service_id: str,
        operation: str,
    ) -> Permission | None:
        """Return the permission required for a service operation."""

        self._validate(service_id, operation)

        with self._lock:
            return self._requirements.get(service_id, {}).get(operation)

    def services(self) -> list[str]:
        """Return the service ids with registered requirements."""

        with self._lock:
            return sorted(self._requirements)

    def count(self) -> int:
        """Return the number of registered operation requirements."""

        with self._lock:
            return sum(
                len(operations)
                for operations in self._requirements.values()
            )

    def clear(self) -> None:
        """Remove all registered requirements."""

        with self._lock:
            self._requirements.clear()

    @staticmethod
    def _validate(service_id: str, operation: str) -> None:
        """Validate service and operation identifiers."""

        if not isinstance(service_id, str) or not service_id.strip():
            raise ValueError("Service id must be a non-empty string.")

        if not isinstance(operation, str) or not operation.strip():
            raise ValueError("Operation must be a non-empty string.")

    def __repr__(self) -> str:
        with self._lock:
            summary = ", ".join(
                f"{service}.{operation}={permission.value}"
                for service, operations in sorted(self._requirements.items())
                for operation, permission in sorted(operations.items())
            )

        return f"SecurityPolicy({summary or 'empty'})"