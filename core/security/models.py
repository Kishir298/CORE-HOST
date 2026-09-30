from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any


class IdentityType(str, Enum):
    DEVICE = "device"
    SERVICE = "service"
    USER = "user"


class Permission(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    ADMIN = "admin"


@dataclass(frozen=True)
class Identity:
    """
    Represents an authenticated C.O.R.E. identity.

    An identity belongs to a user, service, or device and carries the
    permissions and metadata required by the security layer.
    """

    identity_id: str
    name: str
    identity_type: IdentityType
    permissions: frozenset[Permission] = field(default_factory=frozenset)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        """Validate and normalize identity data."""

        if not self.identity_id:
            raise ValueError("Identity ID cannot be empty.")

        if not self.name:
            raise ValueError("Identity name cannot be empty.")

        if not isinstance(self.identity_type, IdentityType):
            raise TypeError("Identity type must be an IdentityType.")

        normalized_permissions = frozenset(self.permissions)

        if not all(
            isinstance(permission, Permission)
            for permission in normalized_permissions
        ):
            raise TypeError(
                "Identity permissions must contain Permission values."
            )

        object.__setattr__(
            self,
            "permissions",
            normalized_permissions,
        )

        if not isinstance(self.metadata, Mapping):
            raise TypeError("Identity metadata must be a mapping.")

        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )

        if self.created_at.tzinfo is None:
            object.__setattr__(
                self,
                "created_at",
                self.created_at.replace(tzinfo=timezone.utc),
            )

    def has_permission(self, permission: Permission) -> bool:
        """Return whether the identity has a specific permission."""

        return permission in self.permissions

    def has_any_permission(
        self,
        permissions: frozenset[Permission],
    ) -> bool:
        """Return whether the identity has at least one given permission."""

        return bool(self.permissions.intersection(permissions))

    def has_all_permissions(
        self,
        permissions: frozenset[Permission],
    ) -> bool:
        """Return whether the identity has every given permission."""

        return permissions.issubset(self.permissions)

    def with_permissions(
        self,
        permissions: frozenset[Permission],
    ) -> "Identity":
        """Return a new identity with the supplied permissions."""

        return Identity(
            identity_id=self.identity_id,
            name=self.name,
            identity_type=self.identity_type,
            permissions=frozenset(permissions),
            metadata=dict(self.metadata),
            created_at=self.created_at,
        )

    def with_metadata(
        self,
        metadata: Mapping[str, Any],
    ) -> "Identity":
        """Return a new identity with updated metadata."""

        return Identity(
            identity_id=self.identity_id,
            name=self.name,
            identity_type=self.identity_type,
            permissions=self.permissions,
            metadata=dict(metadata),
            created_at=self.created_at,
        )