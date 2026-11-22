"""Authorization PDP adapter: embedded parity, daemon delegation, fail-closed.

Embedded mode must reproduce the historical local membership check exactly;
daemon mode delegates to a real local T.I.V.I.S.S. PDP daemon (started in
-process) and fails closed when it is unreachable.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from core.security import (
    AuthorizationError,
    DaemonAuthorizationPDP,
    EmbeddedAuthorizationPDP,
    Identity,
    IdentityType,
    PDPUnavailableError,
    Permission,
    SecurityManager,
    create_pdp,
)
from core.security.manager import IdentityNotFound

try:
    from tiviss_security_host import PDPServer, PolicyEngine

    HAVE_TIVISS = True
except ImportError:  # pragma: no cover - extra not installed
    HAVE_TIVISS = False


def make_sock_dir(prefix: str) -> Path:
    """Short temp dir for AF_UNIX sockets (macOS ~104-byte path limit)."""
    base = "/tmp" if Path("/tmp").is_dir() else None
    return Path(tempfile.mkdtemp(prefix=prefix, dir=base))


def make_manager(*permissions: Permission) -> SecurityManager:
    manager = SecurityManager()
    manager.register_identity(
        Identity(
            identity_id="asis",
            name="ASIS",
            identity_type=IdentityType.SERVICE,
            permissions=frozenset(permissions),
        )
    )
    return manager


class StubPDP:
    """Deterministic PDP double."""

    def __init__(self, allow: bool, *, fail: bool = False) -> None:
        self.allow = allow
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def allowed(self, identity_id: str, permission: str) -> bool:
        self.calls.append((identity_id, permission))
        if self.fail:
            raise PDPUnavailableError("stub: daemon down")
        return self.allow


# --------------------------------------------------------------------------- #
# Adapter basics
# --------------------------------------------------------------------------- #


def test_manager_defaults_to_local_membership():
    manager = make_manager(Permission.READ)
    assert manager.pdp is None
    assert manager.authorize("asis", Permission.READ) is True
    with pytest.raises(AuthorizationError):
        manager.authorize("asis", Permission.ADMIN)


def test_set_pdp_validation():
    manager = SecurityManager()
    with pytest.raises(TypeError, match="allowed"):
        manager.set_pdp(object())
    stub = StubPDP(True)
    manager.set_pdp(stub)
    assert manager.pdp is stub
    manager.set_pdp(None)
    assert manager.pdp is None


def test_constructor_rejects_bad_pdp():
    with pytest.raises(TypeError, match="allowed"):
        SecurityManager(pdp=object())


def test_embedded_pdp_from_manager_parity():
    manager = make_manager(Permission.READ, Permission.WRITE)
    pdp = EmbeddedAuthorizationPDP.from_manager(manager)
    assert pdp.allowed("asis", "read") is True
    assert pdp.allowed("asis", "write") is True
    assert pdp.allowed("asis", "admin") is False
    assert pdp.allowed("ghost", "read") is False


def test_authorize_with_embedded_pdp_matches_legacy():
    manager = make_manager(Permission.READ)
    manager.set_pdp(EmbeddedAuthorizationPDP.from_manager(manager))

    assert manager.authorize("asis", Permission.READ) is True
    with pytest.raises(AuthorizationError):
        manager.authorize("asis", Permission.WRITE)
    assert manager.has_permission("asis", Permission.READ) is True
    assert manager.has_permission("asis", Permission.WRITE) is False

    # Metrics keep counting the same way.
    assert manager.authorization_count() == 2
    assert manager.authorization_failure_count() == 1


def test_authorize_events_with_embedded_pdp():
    manager = make_manager(Permission.READ)
    manager.set_pdp(EmbeddedAuthorizationPDP.from_manager(manager))
    events: list[dict] = []
    manager.register_event_handler(events.append)

    manager.authorize("asis", Permission.READ)
    with pytest.raises(AuthorizationError):
        manager.authorize("asis", Permission.ADMIN)

    kinds = [event["event_type"] for event in events]
    assert kinds == ["AUTHORIZATION_SUCCEEDED", "AUTHORIZATION_FAILED"]


def test_stub_pdp_deny_raises_authorization_error():
    manager = make_manager(Permission.ADMIN)  # local says yes
    stub = StubPDP(False)
    manager.set_pdp(stub)

    with pytest.raises(AuthorizationError):
        manager.authorize("asis", Permission.ADMIN)
    assert stub.calls == [("asis", "admin")]
    assert manager.authorization_failure_count() == 1


def test_stub_pdp_allow_overrides_local_membership():
    manager = make_manager()  # local says no
    stub = StubPDP(True)
    manager.set_pdp(stub)

    assert manager.authorize("asis", Permission.ADMIN) is True
    assert manager.has_permission("asis", Permission.ADMIN) is True


def test_unavailable_pdp_fails_closed():
    manager = make_manager(Permission.READ)  # local would allow
    stub = StubPDP(True, fail=True)
    manager.set_pdp(stub)

    with pytest.raises(PDPUnavailableError):
        manager.authorize("asis", Permission.READ)
    assert manager.has_permission("asis", Permission.READ) is False
    assert manager.authorization_failure_count() == 1


def test_unknown_identity_still_raises_before_pdp():
    manager = make_manager(Permission.READ)
    manager.set_pdp(EmbeddedAuthorizationPDP.from_manager(manager))
    with pytest.raises(IdentityNotFound):
        manager.authorize("ghost", Permission.READ)


def test_create_pdp_modes():
    manager = make_manager(Permission.READ)
    embedded = create_pdp("embedded", manager=manager)
    assert isinstance(embedded, EmbeddedAuthorizationPDP)
    daemon = create_pdp("daemon", socket_path="/tmp/x.sock", timeout=1.0)
    assert isinstance(daemon, DaemonAuthorizationPDP)
    with pytest.raises(ValueError, match="requires the SecurityManager"):
        create_pdp("embedded")
    with pytest.raises(ValueError, match="security.mode"):
        create_pdp("banana", manager=manager)


def test_daemon_pdp_rejects_bad_timeout():
    with pytest.raises(ValueError, match="timeout"):
        DaemonAuthorizationPDP(timeout=0)


# --------------------------------------------------------------------------- #
# Daemon mode against a real local PDP daemon
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not HAVE_TIVISS, reason="TIVISS security packages missing")
def test_daemon_pdp_delegates_to_tiviss_daemon():
    sock_dir = make_sock_dir("core-pdp-")
    try:
        socket_path = str(sock_dir / "pdp.sock")
        server = PDPServer(
            PolicyEngine.from_permission_lists(allow=["read", "write"]),
            socket_path=socket_path,
            request_timeout_s=2.0,
        )
        server.start()
        try:
            # Local membership would DENY (identity has nothing); the
            # daemon allows action "read" -> delegation is authoritative.
            manager = SecurityManager()
            manager.register_identity(
                Identity(
                    identity_id="asis",
                    name="ASIS",
                    identity_type=IdentityType.SERVICE,
                    permissions=frozenset(),
                )
            )
            manager.set_pdp(DaemonAuthorizationPDP(socket_path, timeout=2.0))

            assert manager.authorize("asis", Permission.READ) is True
            with pytest.raises(AuthorizationError):
                manager.authorize("asis", Permission.ADMIN)  # default deny
            assert manager.has_permission("asis", Permission.READ) is True
            assert manager.has_permission("asis", Permission.ADMIN) is False
        finally:
            server.stop()
    finally:
        shutil.rmtree(sock_dir, ignore_errors=True)


@pytest.mark.skipif(not HAVE_TIVISS, reason="TIVISS security packages missing")
def test_daemon_pdp_unavailable_fails_closed():
    sock_dir = make_sock_dir("core-pdp-")
    try:
        socket_path = str(sock_dir / "absent.sock")
        manager = make_manager(Permission.READ)  # local would allow
        manager.set_pdp(DaemonAuthorizationPDP(socket_path, timeout=1.0))

        with pytest.raises(PDPUnavailableError, match="unavailable"):
            manager.authorize("asis", Permission.READ)
        assert manager.has_permission("asis", Permission.READ) is False
        assert manager.authorization_failure_count() == 1
    finally:
        shutil.rmtree(sock_dir, ignore_errors=True)


@pytest.mark.skipif(not HAVE_TIVISS, reason="TIVISS security packages missing")
def test_daemon_pdp_direct_allowed_calls():
    sock_dir = make_sock_dir("core-pdp-")
    try:
        socket_path = str(sock_dir / "pdp.sock")
        server = PDPServer(
            PolicyEngine.from_permission_lists(allow=["read"]),
            socket_path=socket_path,
            request_timeout_s=2.0,
        )
        server.start()
        try:
            pdp = DaemonAuthorizationPDP(socket_path, timeout=2.0)
            assert pdp.allowed("anyone", "read") is True
            assert pdp.allowed("anyone", "admin") is False
        finally:
            server.stop()

        with pytest.raises(PDPUnavailableError, match="unavailable"):
            pdp.allowed("anyone", "read")
    finally:
        shutil.rmtree(sock_dir, ignore_errors=True)
