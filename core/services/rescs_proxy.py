from __future__ import annotations

from typing import Any

from core.communication.devices import DeviceIdentity
from core.rescs.adapter import RescsAdapter


class RescsDeviceProxy:
    """
    Proxy RESCS requests with device-scoped namespace enforcement.
    
    Rewrites namespaces to device-scoped format and validates access.
    Also performs API-level validation for defense in depth.
    """

    # Reserved namespace prefixes that devices cannot access directly
    RESERVED_PREFIXES = ("core.", "rescs.", "asis.", "tiviss.")
    
    # Device-scoped prefix format
    DEVICE_PREFIX = "personal.device."

    def __init__(self, rescs_adapter: RescsAdapter) -> None:
        self.adapter = rescs_adapter

    def _get_device_namespace(self, device: DeviceIdentity) -> str:
        """Generate the device-scoped namespace prefix."""
        return f"{self.DEVICE_PREFIX}{device.device_id}."

    def _enforce_device_namespace(
        self, 
        device: DeviceIdentity, 
        payload: dict[str, Any],
        operation: str
    ) -> dict[str, Any]:
        """Enforce device-scoped namespace on the payload."""
        # Create a copy to avoid mutating original
        new_payload = dict(payload)
        
        # Determine the namespace field based on operation
        namespace_field = self._get_namespace_field(operation)
        if namespace_field and namespace_field in new_payload:
            original_namespace = new_payload[namespace_field]
            
            # Check if trying to access reserved namespace
            for prefix in self.RESERVED_PREFIXES:
                if original_namespace.startswith(prefix):
                    raise ValueError(
                        f"Access denied: namespace '{original_namespace}' is reserved"
                    )
            
            # If already device-scoped, verify it matches this device
            if original_namespace.startswith(self.DEVICE_PREFIX):
                expected_prefix = self._get_device_namespace(device)
                if not original_namespace.startswith(expected_prefix):
                    raise ValueError(
                        "Access denied: cannot access other device's namespace"
                    )
            else:
                # Rewrite to device-scoped namespace
                new_payload[namespace_field] = (
                    f"{self._get_device_namespace(device)}{original_namespace}"
                )
        
        return new_payload

    def _get_namespace_field(self, operation: str) -> str | None:
        """Get the namespace field name for a given operation."""
        namespace_fields = {
            "record_create": "namespace",
            "record_get": "namespace", 
            "record_list": "namespace",
            "record_search": "namespace",
            "record_update": "namespace",
            "record_delete": "namespace",
            "record_restore": "namespace",
            "record_purge": "namespace",
            "file_upload": "namespace",
            "file_metadata": "namespace",
            "file_download": "namespace",
            "file_delete": "namespace",
            "file_restore": "namespace",
            "file_purge": "namespace",
            "upload_create": "namespace",
        }
        return namespace_fields.get(operation)

    def _get_owner_field(self, operation: str) -> str | None:
        """Get the owner field name for a given operation."""
        # Most operations accept owner field
        owner_fields = {
            "record_create": "owner",
            "record_get": "owner",
            "record_list": "owner",
            "record_search": "owner",
            "record_update": "owner",
            "record_delete": "owner",
            "record_restore": "owner",
            "record_purge": "owner",
            "file_upload": "owner",
            "file_metadata": "owner",
            "file_download": "owner",
            "file_delete": "owner",
            "file_restore": "owner",
            "file_purge": "owner",
            "upload_create": "owner",
        }
        return owner_fields.get(operation)

    def _enforce_device_owner(
        self, 
        device: DeviceIdentity, 
        payload: dict[str, Any],
        operation: str
    ) -> dict[str, Any]:
        """Enforce device-scoped owner on the payload."""
        new_payload = dict(payload)
        owner_field = self._get_owner_field(operation)
        
        if owner_field and owner_field in new_payload:
            original_owner = new_payload[owner_field]
            # If owner is already device-scoped, verify it matches
            if original_owner.startswith(self.DEVICE_PREFIX):
                expected_prefix = self._get_device_namespace(device).rstrip(".")
                if not original_owner.startswith(expected_prefix):
                    raise ValueError(
                        "Access denied: cannot access other device's owner scope"
                    )
            else:
                # Rewrite to device-scoped owner
                new_payload[owner_field] = f"{self._get_device_namespace(device)}{original_owner}"
        
        return new_payload

    def data_request(
        self, 
        device: DeviceIdentity, 
        request_type: str, 
        payload: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Process a RESCS data request with device-scoped enforcement.
        
        Args:
            device: The requesting device identity
            request_type: The RESCS request type (e.g., "record_create", "file_download")
            payload: The request payload
            
        Returns:
            The response from the RESCS adapter
            
        Raises:
            ValueError: If access is denied
        """
        # Enforce device-scoped namespace
        payload = self._enforce_device_namespace(device, payload, request_type)
        
        # Enforce device-scoped owner
        payload = self._enforce_device_owner(device, payload, request_type)
        
        # Add device context for audit
        if "metadata" not in payload:
            payload["metadata"] = {}
        payload["metadata"]["_device_id"] = device.device_id
        payload["metadata"]["_device_type"] = device.metadata.get("device_type")
        payload["metadata"]["_platform"] = device.metadata.get("platform")
        
        # Forward to RESCS adapter
        return self.adapter.data_request(request_type, payload)

    def validate_device_access(
        self, 
        device: DeviceIdentity, 
        namespace: str,
        owner: str | None = None
    ) -> tuple[str, str | None]:
        """
        Validate and transform namespace/owner for device access.
        
        Returns:
            Tuple of (transformed_namespace, transformed_owner)
        """
        # Transform namespace
        if namespace.startswith(self.RESERVED_PREFIXES):
            raise ValueError(f"Access denied: namespace '{namespace}' is reserved")
        
        if namespace.startswith(self.DEVICE_PREFIX):
            # Verify it's this device's namespace
            expected = self._get_device_namespace(device)
            if not namespace.startswith(expected):
                raise ValueError("Access denied: cannot access other device's namespace")
            transformed_namespace = namespace
        else:
            transformed_namespace = f"{self._get_device_namespace(device)}{namespace}"
        
        # Transform owner if provided
        transformed_owner = None
        if owner is not None:
            if owner.startswith(self.DEVICE_PREFIX):
                expected = self._get_device_namespace(device).rstrip(".")
                if not owner.startswith(expected):
                    raise ValueError("Access denied: cannot access other device's owner scope")
                transformed_owner = owner
            else:
                transformed_owner = f"{self._get_device_namespace(device)}{owner}"
        
        return transformed_namespace, transformed_owner