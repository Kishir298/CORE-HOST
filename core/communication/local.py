"""
Backward-compatible local communication aliases.

The transport implementation now lives in core.communication.transport.
LocalCommunication is retained as an alias for the concrete local
transport so existing v0.1 call sites and tests continue to work while
the architecture is transport-agnostic.
"""

from .transport import LocalTransport, MessageHandler

LocalCommunication = LocalTransport

__all__ = [
    "LocalCommunication",
    "LocalTransport",
    "MessageHandler",
]
