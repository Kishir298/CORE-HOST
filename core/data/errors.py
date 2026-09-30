"""Typed data-layer errors.

These never cross the wire directly; the organizer translates them into
DATA_ERROR envelopes so no internals leak to devices.
"""

from __future__ import annotations


class DataError(Exception):
    """Base class for data-layer failures (carries a protocol error code)."""

    code = "DATA_RETRIEVAL_FAILED"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class DataNotFound(DataError):
    """R.E.S.C.S. has no such record/file."""

    code = "DATA_NOT_FOUND"


class DataAccessDenied(DataError):
    """Caller is not authorized for the requested data."""

    code = "DATA_ACCESS_DENIED"


class DataSourceUnavailable(DataError):
    """R.E.S.C.S. could not be reached (timeout / connection failure)."""

    code = "DATA_SOURCE_UNAVAILABLE"


class DataRetrievalFailed(DataError):
    """R.E.S.C.S. failed unexpectedly, or integrity validation failed."""

    code = "DATA_RETRIEVAL_FAILED"


__all__ = [
    "DataAccessDenied",
    "DataError",
    "DataNotFound",
    "DataRetrievalFailed",
    "DataSourceUnavailable",
]
