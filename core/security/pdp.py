"""Authorization PDP adapter: embedded (local) or daemon (T.I.V.I.S.S.).

C.O.R.E. keeps its historical decision semantics behind one interface:

- :class:`EmbeddedAuthorizationPDP` — the local identity-permission
  membership check that C.O.R.E. has always performed (default; zero
  behaviour change).
- :class:`DaemonAuthorizationPDP` — delegates each question to the local
  T.I.V.I.S.S. PDP daemon over its Unix domain socket
  (``tiviss-security-client``).  Unreachable daemon or protocol failures
  raise :class:`PDPUnavailableError` so callers can fail closed
  explicitly; ``SecurityManager.has_permission`` maps it to ``False``.

The T.I.V.I.S.S. packages are optional dependencies (``.[test]`` installs
them from the public TIVISS-HOST repository at pinned commits) and are
imported lazily so embedded-only deployments never need them.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Any, Protocol

__all__ = [
    "AuthorizationPDP",
    "PDPError",
    "PDPUnavailableError",
    "EmbeddedAuthorizationPDP",
    "DaemonAuthorizationPDP",
    "create_pdp",
]


class PDPError(Exception):
    """Base class for authorization-PDP failures."""


class PDPUnavailableError(PDPError):
    """The policy decision point could not be reached or misbehaved.

    Callers must fail closed (deny / raise) when they see this.
    """


#: The T.I.V.I.S.S. PDP daemon speaks AF_UNIX (Unix domain sockets) only.
#: CPython does not expose ``socket.AF_UNIX`` on Windows, so daemon mode is
#: unsupported there and must fail closed with an actionable message instead
#: of an ``AttributeError`` from deep inside the IPC client.
AF_UNIX_SUPPORTED = hasattr(socket, "AF_UNIX")


class AuthorizationPDP(Protocol):
    """What ``SecurityManager`` needs from any decision backend."""

    def allowed(self, identity_id: str, permission: str) -> bool: ...


class EmbeddedAuthorizationPDP:
    """Local membership check behind the PDP interface (default mode).

    ``resolver(identity_id)`` returns the identity's permission values
    (strings); ``None``/``KeyError`` means "unknown identity" -> deny.
    """

    def __init__(
        self,
        resolver: Callable[[str], Any],
    ) -> None:
        if not callable(resolver):
            raise TypeError("resolver must be callable.")
        self._resolver = resolver

    @classmethod
    def from_manager(cls, manager: Any) -> EmbeddedAuthorizationPDP:
        """Build the resolver from a :class:`SecurityManager`."""

        def resolve(identity_id: str) -> Any:
            identity = manager.get_identity(identity_id)
            return frozenset(
                permission.value for permission in identity.permissions
            )

        return cls(resolve)

    def allowed(self, identity_id: str, permission: str) -> bool:
        try:
            permissions = self._resolver(identity_id)
        except Exception:
            # Unknown identity or broken resolver: fail closed.
            return False
        if permissions is None:
            return False
        return permission in permissions


class DaemonAuthorizationPDP:
    """Delegate decisions to the local T.I.V.I.S.S. PDP daemon."""

    def __init__(
        self,
        socket_path: str | None = None,
        *,
        timeout: float = 5.0,
        resource: str = "core",
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be > 0")
        self.socket_path = socket_path
        self.timeout = timeout
        self.resource = resource
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            if not AF_UNIX_SUPPORTED:
                raise PDPUnavailableError(
                    "security daemon unavailable: security.mode=daemon requires "
                    "the T.I.V.I.S.S. PDP daemon's Unix domain socket transport "
                    "(AF_UNIX), which this platform's Python does not provide. "
                    "Use security.mode=embedded here, or run the PDP daemon on "
                    "a Linux/macOS host."
                )
            try:
                from tiviss_security_client import SecurityClient
            except ImportError as exc:
                raise PDPUnavailableError(
                    "tiviss-security-client is not installed; install "
                    "C.O.R.E. with the 'test' extra or the security "
                    "requirements to use security.mode=daemon."
                ) from exc
            self._client = SecurityClient(self.socket_path, timeout=self.timeout)
        return self._client

    def allowed(self, identity_id: str, permission: str) -> bool:
        try:
            from tiviss_security_client import PDPError as WirePDPError
            from tiviss_security_ipc import IPCError
            from tiviss_security_protocol import (
                DecisionRequest,
                ProtocolError,
            )
        except ImportError as exc:
            raise PDPUnavailableError(
                "T.I.V.I.S.S. security packages are not installed."
            ) from exc

        request = DecisionRequest(
            subject=identity_id,
            action=permission,
            resource=self.resource,
        )
        try:
            response = self._get_client().decide(request)
        except IPCError as exc:
            raise PDPUnavailableError(
                f"security daemon unavailable at "
                f"{self.socket_path or 'default socket'}: {exc}"
            ) from exc
        except (WirePDPError, ProtocolError) as exc:
            raise PDPUnavailableError(
                f"security daemon rejected the decision request: {exc}"
            ) from exc
        return bool(response.allowed)


def create_pdp(
    mode: str,
    *,
    manager: Any | None = None,
    socket_path: str | None = None,
    timeout: float = 5.0,
    resource: str = "core",
) -> AuthorizationPDP:
    """Build the PDP for ``security.mode`` (``embedded`` | ``daemon``)."""
    if mode == "embedded":
        if manager is None:
            raise ValueError("embedded mode requires the SecurityManager.")
        return EmbeddedAuthorizationPDP.from_manager(manager)
    if mode == "daemon":
        return DaemonAuthorizationPDP(
            socket_path, timeout=timeout, resource=resource
        )
    raise ValueError(
        f"security.mode must be 'embedded' or 'daemon', got {mode!r}"
    )
