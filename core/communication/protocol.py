"""C.O.R.E. device communication protocol constants.

Central definition of the device registration / discovery / presence /
routing protocol so TCP transport, router, application and tests share one
authoritative contract. No networking code lives here.
"""

from __future__ import annotations

# -- message types (exact wire values) ------------------------------------
DEVICE_REGISTER = "DEVICE_REGISTER"
DEVICE_REGISTER_RESPONSE = "DEVICE_REGISTER_RESPONSE"
DEVICE_DISCOVER = "DEVICE_DISCOVER"
DEVICE_DISCOVER_RESPONSE = "DEVICE_DISCOVER_RESPONSE"
DEVICE_INFO = "DEVICE_INFO"
DEVICE_INFO_RESPONSE = "DEVICE_INFO_RESPONSE"
DEVICE_ERROR = "DEVICE_ERROR"

# -- data message types (exact wire values) ------------------------------------
DATA_REQUEST = "DATA_REQUEST"
DATA_RESPONSE = "DATA_RESPONSE"
DATA_ERROR = "DATA_ERROR"

# -- fixed device error codes (exact wire values for DEVICE_ERROR.error) ----
DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
DEVICE_ALREADY_REGISTERED = "DEVICE_ALREADY_REGISTERED"
DEVICE_NOT_REGISTERED = "DEVICE_NOT_REGISTERED"
DEVICE_REGISTRATION_FAILED = "DEVICE_REGISTRATION_FAILED"
INVALID_DESTINATION = "INVALID_DESTINATION"
COMMUNICATION_ERROR = "COMMUNICATION_ERROR"

# -- fixed data error codes (exact wire values for DATA_ERROR.error) ---------
INVALID_DATA_REQUEST = "INVALID_DATA_REQUEST"
INVALID_PAGINATION = "INVALID_PAGINATION"
DATA_NOT_FOUND = "DATA_NOT_FOUND"
DATA_ACCESS_DENIED = "DATA_ACCESS_DENIED"
DATA_SOURCE_UNAVAILABLE = "DATA_SOURCE_UNAVAILABLE"
DATA_RETRIEVAL_FAILED = "DATA_RETRIEVAL_FAILED"
FILE_TRANSFER_REQUIRED = "FILE_TRANSFER_REQUIRED"
DATA_RESPONSE_TOO_LARGE = "DATA_RESPONSE_TOO_LARGE"
DESTINATION_UNAVAILABLE = "DESTINATION_UNAVAILABLE"

ERROR_CODES = frozenset(
    {
        DEVICE_ERROR,
        DEVICE_UNAVAILABLE,
        DEVICE_NOT_FOUND,
        DEVICE_ALREADY_REGISTERED,
        DEVICE_NOT_REGISTERED,
        DEVICE_REGISTRATION_FAILED,
        INVALID_DESTINATION,
        COMMUNICATION_ERROR,
        DATA_ERROR,
        INVALID_DATA_REQUEST,
        INVALID_PAGINATION,
        DATA_NOT_FOUND,
        DATA_ACCESS_DENIED,
        DATA_SOURCE_UNAVAILABLE,
        DATA_RETRIEVAL_FAILED,
        FILE_TRANSFER_REQUIRED,
        DATA_RESPONSE_TOO_LARGE,
        DESTINATION_UNAVAILABLE,
    }
)

# -- device presence (only these two values are valid) ---------------------
DEVICE_STATUS_ONLINE = "online"
DEVICE_STATUS_OFFLINE = "offline"

# -- protocol version used by the device registration contract -------------
SUPPORTED_PROTOCOL_VERSION = "0.3.0"

# -- minimum TLS version (TLS 1.2) ------------------------------------------
try:  # pragma: no cover - environment dependent
    import ssl as _ssl

    MINIMUM_TLS_VERSION = _ssl.TLSVersion.TLSv1_2
except Exception:  # pragma: no cover - very old Python
    MINIMUM_TLS_VERSION = "TLSv1.2"

# -- registration payload contract ------------------------------------------
REQUIRED_REGISTRATION_FIELDS = (
    "device_id",
    "device_name",
    "device_type",
    "platform",
    "capabilities",
    "protocol_version",
)


def is_supported_protocol_version(version: str | None) -> bool:
    """Return whether a device protocol version is supported."""
    if not isinstance(version, str) or not version.strip():
        return False
    try:
        from core.version import is_supported as _is_supported

        return bool(_is_supported(version.strip()))
    except Exception:
        return version.strip() == SUPPORTED_PROTOCOL_VERSION


def validate_registration_payload(payload: object) -> tuple[str | None, str | None]:
    """Validate a DEVICE_REGISTER payload.

    Returns ``(None, None)`` when valid, else ``(error_code, message)``.
    Never raises and never synthesizes a device ID.
    """
    if not isinstance(payload, dict):
        return DEVICE_REGISTRATION_FAILED, "Invalid registration payload structure."
    device_id = payload.get("device_id")
    if device_id is None or (isinstance(device_id, str) and device_id == ""):
        if device_id is None:
            return DEVICE_REGISTRATION_FAILED, "Missing device_id."
        return DEVICE_REGISTRATION_FAILED, "Empty device_id."
    if not isinstance(device_id, str):
        return DEVICE_REGISTRATION_FAILED, "Invalid device_id."
    device_name = payload.get("device_name")
    if device_name is None:
        return DEVICE_REGISTRATION_FAILED, "Missing device_name."
    if not isinstance(device_name, str) or device_name == "":
        return DEVICE_REGISTRATION_FAILED, "Empty device_name."
    if payload.get("device_type") is None:
        return DEVICE_REGISTRATION_FAILED, "Missing device_type."
    if not isinstance(payload.get("device_type"), str) or not payload["device_type"].strip():
        return DEVICE_REGISTRATION_FAILED, "Empty device_type."
    if payload.get("platform") is None:
        return DEVICE_REGISTRATION_FAILED, "Missing platform."
    if not isinstance(payload.get("platform"), str) or not payload["platform"].strip():
        return DEVICE_REGISTRATION_FAILED, "Empty platform."
    if "capabilities" not in payload:
        return DEVICE_REGISTRATION_FAILED, "Missing capabilities."
    if not isinstance(payload.get("capabilities"), list):
        return DEVICE_REGISTRATION_FAILED, "Malformed capabilities: must be a list."
    if payload.get("protocol_version") is None:
        return DEVICE_REGISTRATION_FAILED, "Missing protocol_version."
    if not is_supported_protocol_version(payload.get("protocol_version")):
        return DEVICE_REGISTRATION_FAILED, "Unsupported protocol version."
    # join_name is optional (older clients omit it and the host derives a
    # fallback), but when present it must be a sane human-readable label.
    if "join_name" in payload and payload.get("join_name") is not None:
        join_name = payload.get("join_name")
        if not isinstance(join_name, str) or not join_name.strip():
            return DEVICE_REGISTRATION_FAILED, "Invalid join_name."
        if len(join_name.strip()) > 64:
            return DEVICE_REGISTRATION_FAILED, "Invalid join_name."
    return None, None


def build_device_error(
    error_code: str, message: str, request_id: str | None
) -> dict:
    """Build a DEVICE_ERROR / DATA_ERROR payload envelope."""
    code = error_code if error_code in ERROR_CODES else COMMUNICATION_ERROR
    return {"error": code, "message": message, "request_id": request_id}


# -- data organization limits (exact values, NOT configurable) ----------------
MAX_DATA_ITEMS = 500
MAX_RECORD_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_FILE_METADATA_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_INLINE_FILE_BYTES = 1 * 1024 * 1024

DEFAULT_LIMIT = 100
MIN_LIMIT = 1
MAX_LIMIT = 500
MIN_OFFSET = 0

# -- R.E.S.C.S. retrieval behavior (exact values, NOT configurable) -----------
RESCS_REQUEST_TIMEOUT = 2.0
RESCS_MAX_RETRIES = 0
DEFAULT_RESCS_ENDPOINT = "http://localhost:8081"

# -- supported data request types (exact wire values) --------------------------
REQUEST_RECORD_GET = "record_get"
REQUEST_RECORD_LIST = "record_list"
REQUEST_RECORD_SEARCH = "record_search"
REQUEST_FILE_METADATA = "file_metadata"
REQUEST_FILE_DOWNLOAD = "file_download"

SUPPORTED_REQUEST_TYPES = frozenset(
    {
        REQUEST_RECORD_GET,
        REQUEST_RECORD_LIST,
        REQUEST_RECORD_SEARCH,
        REQUEST_FILE_METADATA,
        REQUEST_FILE_DOWNLOAD,
    }
)


__all__ = [
    "DEVICE_REGISTER",
    "DEVICE_REGISTER_RESPONSE",
    "DEVICE_DISCOVER",
    "DEVICE_DISCOVER_RESPONSE",
    "DEVICE_INFO",
    "DEVICE_INFO_RESPONSE",
    "DEVICE_ERROR",
    "DEVICE_UNAVAILABLE",
    "DEVICE_NOT_FOUND",
    "DEVICE_ALREADY_REGISTERED",
    "DEVICE_NOT_REGISTERED",
    "DEVICE_REGISTRATION_FAILED",
    "INVALID_DESTINATION",
    "COMMUNICATION_ERROR",
    "DATA_REQUEST",
    "DATA_RESPONSE",
    "DATA_ERROR",
    "INVALID_DATA_REQUEST",
    "INVALID_PAGINATION",
    "DATA_NOT_FOUND",
    "DATA_ACCESS_DENIED",
    "DATA_SOURCE_UNAVAILABLE",
    "DATA_RETRIEVAL_FAILED",
    "FILE_TRANSFER_REQUIRED",
    "DATA_RESPONSE_TOO_LARGE",
    "DESTINATION_UNAVAILABLE",
    "MAX_DATA_ITEMS",
    "MAX_RECORD_RESPONSE_BYTES",
    "MAX_FILE_METADATA_RESPONSE_BYTES",
    "MAX_INLINE_FILE_BYTES",
    "DEFAULT_LIMIT",
    "MIN_LIMIT",
    "MAX_LIMIT",
    "MIN_OFFSET",
    "RESCS_REQUEST_TIMEOUT",
    "RESCS_MAX_RETRIES",
    "DEFAULT_RESCS_ENDPOINT",
    "REQUEST_RECORD_GET",
    "REQUEST_RECORD_LIST",
    "REQUEST_RECORD_SEARCH",
    "REQUEST_FILE_METADATA",
    "REQUEST_FILE_DOWNLOAD",
    "SUPPORTED_REQUEST_TYPES",
    "ERROR_CODES",
    "DEVICE_STATUS_ONLINE",
    "DEVICE_STATUS_OFFLINE",
    "SUPPORTED_PROTOCOL_VERSION",
    "MINIMUM_TLS_VERSION",
    "REQUIRED_REGISTRATION_FIELDS",
    "is_supported_protocol_version",
    "validate_registration_payload",
    "build_device_error",
]
