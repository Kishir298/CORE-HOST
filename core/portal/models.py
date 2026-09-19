"""Portal serialization models: redaction + stable API schemas.

Every portal response passes through :func:`redact` first. Anything that
looks like a credential, token, key material, or connection URL with
embedded secrets is removed or masked — the UI layer can never leak what
the backend never sends it.
"""

from __future__ import annotations

from typing import Any

_SECRET_HINTS = (
    "token",
    "credential",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "secret",
    "private_key",
    "privatekey",
    "s3_secret",
    "access_key",
    "session_token",
    "session-token",
)

_URL_KEYS = ("database_url", "db_url", "connection_string", "dsn", "endpoint_url")


def _looks_secret(name: str) -> bool:
    """Boundary-aware secret check (avoids false positives).

    Matches whole names (``api_key``) or ``_``/``-``-suffixed forms
    (``session_token``), so display markers like ``session_token_state``
    and data fields like ``key`` survive redaction.
    """
    lowered = str(name).lower()
    if lowered in _SECRET_HINTS:
        return True
    return any(
        lowered.endswith("_" + hint) or lowered.endswith("-" + hint)
        for hint in _SECRET_HINTS
    )


def _scrub_url(value: Any) -> Any:
    """Mask credentials inside URL-like strings, keep scheme + host."""
    if not isinstance(value, str) or "://" not in value:
        return value
    try:
        scheme, _, rest = value.partition("://")
        authority, _, _path = rest.partition("/")
        if "@" in authority:
            _userinfo, _, host = authority.partition("@")
            return f"{scheme}://***@{host}"
        return value
    except Exception:
        return "***redacted***"


def redact(value: Any, *, _key: str = "") -> Any:
    """Recursively remove/mask secret material from a JSON-safe structure."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if _looks_secret(key):
                continue  # drop secret keys entirely
            if str(key).lower() in _URL_KEYS:
                cleaned[key] = _scrub_url(item)
            else:
                cleaned[key] = redact(item, _key=str(key))
        return cleaned
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def envelope(data: Any, *, ok: bool = True, error: str | None = None) -> dict:
    """Stable portal response envelope."""
    body: dict[str, Any] = {"ok": ok, "data": redact(data)}
    if error:
        body["error"] = error
    return body


def device_entry(record: Any, *, host_location: dict | None = None) -> dict:
    """Public device shape (no tokens, no credentials — ever)."""
    try:
        base = record.to_dict()
    except AttributeError:
        base = dict(record)
    entry = {
        "device_id": base.get("device_id"),
        "identity_id": base.get("identity_id"),
        "join_name": base.get("join_name"),
        "device_name": base.get("device_name"),
        "platform": base.get("platform"),
        "device_type": base.get("device_type"),
        "capabilities": list(base.get("capabilities") or []),
        "status": base.get("status"),
        "connection_id": base.get("connection_id"),
        "last_seen": base.get("last_seen"),
        "registered_at": base.get("registered_at"),
        "protocol_version": base.get("protocol_version"),
        "lease": {
            "connected_at": base.get("connected_at"),
            "expires_at": base.get("lease_expires_at"),
            "duration_seconds": base.get("lease_duration_seconds"),
        },
    }
    if host_location is not None:
        entry["host_view"] = host_location
    return redact(entry)


def session_entry(session: Any) -> dict:
    """Public session shape: connection facts, never the session token.

    The token itself is always redacted away; callers only learn whether
    a temporary token is currently active (``ACTIVE``) or absent.
    """
    has_token = bool(getattr(session, "session_token", None))
    entry = {
        "connection_id": getattr(session, "connection_id", None),
        "identity_id": getattr(session, "identity_id", None),
        "remote_address": getattr(session, "remote_address", None),
        "authenticated": bool(getattr(session, "authenticated", False)),
        "state": str(getattr(getattr(session, "state", ""), "value", getattr(session, "state", "")) or ""),
        "connected_at": getattr(session, "connected_iso", None),
        "lease_expires_at": getattr(session, "lease_expires_iso", None),
        "messages_received": getattr(session, "messages_received", 0),
        "messages_sent": getattr(session, "messages_sent", 0),
    }
    cleaned = redact(entry)
    # Named *_state (not *token) so envelope redaction can never strip it;
    # the value is only ever "ACTIVE" or None — never the secret itself.
    cleaned["session_token_state"] = "ACTIVE" if has_token else None
    return cleaned


def health_entry(result: Any) -> dict:
    """Public health-check shape."""
    status = getattr(result, "status", "")
    return {
        "component_id": getattr(result, "component_id", None),
        "status": str(getattr(status, "value", status) or "").upper(),
        "message": getattr(result, "message", ""),
    }


def event_entry(event: Any) -> dict:
    """Public event shape with derived severity (no secret dump)."""
    payload = getattr(event, "payload", {}) or {}
    if not isinstance(payload, dict):
        payload = {"value": payload}
    text = f"{getattr(event, 'event_type', '')} {payload}".lower()
    if "fail" in text or "error" in text or "denied" in text:
        severity = "error"
    elif "warn" in text or "expir" in text:
        severity = "warning"
    else:
        severity = "info"
    timestamp = getattr(event, "timestamp", None)
    return redact(
        {
            "event_id": getattr(event, "event_id", None),
            "event_type": getattr(event, "event_type", None),
            "source": getattr(event, "source", None),
            "timestamp": timestamp.isoformat()
            if hasattr(timestamp, "isoformat")
            else timestamp,
            "severity": severity,
            "summary": str(payload)[:300],
        }
    )
