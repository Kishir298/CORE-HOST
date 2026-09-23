"""Normalization for R.E.S.C.S. data bound for devices.

Pure functions. Authoritative metadata passes through untouched; nothing
is stored, rewritten or timestamped here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

RECORD_FIELDS = (
    "id",
    "namespace",
    "key",
    "value",
    "metadata",
    "owner",
    "version",
    "etag",
    "created_at",
    "updated_at",
)

FILE_FIELDS = (
    "id",
    "filename",
    "mime_type",
    "size",
    "sha256",
    "etag",
    "created_at",
    "updated_at",
)


def normalize_record(raw: dict) -> dict[str, Any]:
    """Normalize one raw record to the device record shape."""
    source = raw if isinstance(raw, dict) else {}
    metadata = source.get("metadata")
    return {
        "id": source.get("id"),
        "namespace": source.get("namespace"),
        "key": source.get("key"),
        "value": source.get("value"),
        "metadata": dict(metadata) if isinstance(metadata, dict) else {},
        "owner": source.get("owner"),
        "version": source.get("version"),
        "etag": source.get("etag"),
        "created_at": source.get("created_at"),
        "updated_at": source.get("updated_at"),
    }


def normalize_file_metadata(raw: dict) -> dict[str, Any]:
    """Normalize one raw file entry to the device file-metadata shape."""
    source = raw if isinstance(raw, dict) else {}
    return {
        "id": source.get("id"),
        "filename": source.get("filename"),
        "mime_type": source.get("mime_type"),
        "size": source.get("size", 0),
        "sha256": source.get("sha256"),
        "etag": source.get("etag"),
        "created_at": source.get("created_at"),
        "updated_at": source.get("updated_at"),
    }


def _sort_time(value: Any) -> tuple[int, Any]:
    """Parse ``updated_at`` to an aware datetime for ordering.

    Returns ``(0, datetime)`` when parseable, ``(1, str)`` as a fallback so
    mixed types never raise ``TypeError`` during comparison.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except Exception:
            return (1, value)
    else:
        return (1, str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (0, dt)


def order_records(records: list[dict]) -> list[dict]:
    """Deterministic order: updated_at DESC, then id ASC.

    Records without ``updated_at`` keep their authoritative relative order
    and sort after timestamped records, ordered by id.
    """
    stamped = [r for r in records if r.get("updated_at") not in (None, "")]
    unstamped = [r for r in records if r.get("updated_at") in (None, "")]
    # Stable sorts: id ASC first, then parsed updated_at DESC (ties keep id order).
    stamped.sort(key=lambda r: str(r.get("id")))
    stamped.sort(key=lambda r: _sort_time(r.get("updated_at")), reverse=True)
    unstamped.sort(key=lambda r: str(r.get("id")))
    return stamped + unstamped


def paginate(
    records: list[dict], limit: int, offset: int
) -> dict[str, Any]:
    """Paginate ordered records into the fixed response shape."""
    total = len(records)
    return {
        "items": records[offset : offset + limit],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


__all__ = [
    "RECORD_FIELDS",
    "FILE_FIELDS",
    "normalize_record",
    "normalize_file_metadata",
    "order_records",
    "paginate",
]
