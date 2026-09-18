"""DATA_REQUEST payload validation.

Pure functions: no I/O, no state. Returns ``(error_code, message)`` on
failure or a :class:`ParsedRequest` on success. Invalid pagination is
reported, never silently clamped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.communication.protocol import (
    DEFAULT_LIMIT,
    INVALID_DATA_REQUEST,
    INVALID_PAGINATION,
    MAX_LIMIT,
    MIN_LIMIT,
    MIN_OFFSET,
    REQUEST_FILE_DOWNLOAD,
    REQUEST_FILE_METADATA,
    REQUEST_RECORD_GET,
    REQUEST_RECORD_SEARCH,
    SUPPORTED_REQUEST_TYPES,
)


@dataclass(frozen=True)
class ParsedRequest:
    """Validated DATA_REQUEST ready for authorization and retrieval."""

    request_type: str
    namespace: str | None = None
    key: str | None = None
    query: str | None = None
    key_prefix: str | None = None
    owner: str | None = None
    file_id: str | None = None
    limit: int = DEFAULT_LIMIT
    offset: int = MIN_OFFSET
    destination_device_id: str | None = None
    raw: dict = field(default_factory=dict)


def validate_pagination(
    limit: Any = DEFAULT_LIMIT, offset: Any = MIN_OFFSET
) -> tuple[int, int, tuple[str, str] | None]:
    """Validate limit/offset. Returns (limit, offset, None) or (0, 0, error)."""
    if isinstance(limit, bool):
        return 0, 0, (INVALID_PAGINATION, "Invalid limit: must be an integer.")
    if isinstance(offset, bool):
        return 0, 0, (INVALID_PAGINATION, "Invalid offset: must be an integer.")
    if not isinstance(limit, int) or not isinstance(offset, int):
        return 0, 0, (INVALID_PAGINATION, "Invalid pagination: limit and offset must be integers.")
    if limit < MIN_LIMIT or limit > MAX_LIMIT:
        return (
            0,
            0,
            (
                INVALID_PAGINATION,
                f"Invalid limit: must be between {MIN_LIMIT} and {MAX_LIMIT}.",
            ),
        )
    if offset < MIN_OFFSET:
        return 0, 0, (INVALID_PAGINATION, "Invalid offset: must be >= 0.")
    return limit, offset, None


def _optional_str(payload: dict, name: str) -> tuple[str | None, tuple[str, str] | None]:
    value = payload.get(name)
    if value is None:
        return None, None
    if not isinstance(value, str) or value == "":
        return None, (INVALID_DATA_REQUEST, f"Invalid {name}: must be a non-empty string.")
    return value, None


def validate_data_request(payload: object) -> tuple[ParsedRequest | None, tuple[str, str] | None]:
    """Validate a DATA_REQUEST payload.

    Returns ``(parsed, None)`` on success or ``(None, (code, message))``.
    """
    if not isinstance(payload, dict):
        return None, (INVALID_DATA_REQUEST, "Invalid DATA_REQUEST: payload must be a JSON object.")
    request_type = payload.get("request_type")
    if request_type is None:
        return None, (INVALID_DATA_REQUEST, "Missing request_type.")
    if request_type not in SUPPORTED_REQUEST_TYPES:
        return None, (INVALID_DATA_REQUEST, f"Unknown request_type: {request_type!r}.")

    destination, err = _optional_str(payload, "destination_device_id")
    if err is not None:
        return None, err

    if request_type in (REQUEST_FILE_METADATA, REQUEST_FILE_DOWNLOAD):
        file_id, ferr = _optional_str(payload, "file_id")
        if ferr is not None:
            return None, ferr
        if file_id is None:
            return None, (INVALID_DATA_REQUEST, "Missing file_id.")
        return (
            ParsedRequest(
                request_type=request_type,
                file_id=file_id,
                destination_device_id=destination,
                raw=dict(payload),
            ),
            None,
        )

    # Record operations: pagination always validated (explicit or default).
    limit, offset, perr = validate_pagination(
        payload.get("limit", DEFAULT_LIMIT), payload.get("offset", MIN_OFFSET)
    )
    if perr is not None:
        return None, perr

    namespace, nerr = _optional_str(payload, "namespace")
    if nerr is not None:
        return None, nerr
    key_prefix, kperr = _optional_str(payload, "key_prefix")
    if kperr is not None:
        return None, kperr
    owner, oerr = _optional_str(payload, "owner")
    if oerr is not None:
        return None, oerr

    if request_type == REQUEST_RECORD_GET:
        key, kerr = _optional_str(payload, "key")
        if kerr is not None:
            return None, kerr
        if namespace is None:
            return None, (INVALID_DATA_REQUEST, "Missing namespace.")
        if key is None:
            return None, (INVALID_DATA_REQUEST, "Missing key.")
        return (
            ParsedRequest(
                request_type=request_type,
                namespace=namespace,
                key=key,
                key_prefix=key_prefix,
                owner=owner,
                limit=limit,
                offset=offset,
                destination_device_id=destination,
                raw=dict(payload),
            ),
            None,
        )

    if request_type == REQUEST_RECORD_SEARCH:
        query = payload.get("query")
        if not isinstance(query, str) or query == "":
            return None, (INVALID_DATA_REQUEST, "Missing query.")
        return (
            ParsedRequest(
                request_type=request_type,
                namespace=namespace,
                query=query,
                key_prefix=key_prefix,
                owner=owner,
                limit=limit,
                offset=offset,
                destination_device_id=destination,
                raw=dict(payload),
            ),
            None,
        )

    # REQUEST_RECORD_LIST: all filters optional.
    return (
        ParsedRequest(
            request_type=request_type,
            namespace=namespace,
            key_prefix=key_prefix,
            owner=owner,
            limit=limit,
            offset=offset,
            destination_device_id=destination,
            raw=dict(payload),
        ),
        None,
    )


__all__ = ["ParsedRequest", "validate_data_request", "validate_pagination"]
