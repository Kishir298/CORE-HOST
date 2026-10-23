"""Internal authentication context set only after transport verification.

This context is never accepted from a serialized message or its payload.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_transport_identity: ContextVar[str | None] = ContextVar("core_transport_identity", default=None)


def transport_identity() -> str | None:
    return _transport_identity.get()


@contextmanager
def authenticated_transport(identity_id: str):
    marker = _transport_identity.set(identity_id)
    try:
        yield
    finally:
        _transport_identity.reset(marker)
