"""Temporary host-issued session token tests (HOST authoritative).

Provisioning credential authenticates once; the host mints a
cryptographically random per-connection session token used for all
subsequent messages. 127.0.0.1 sockets only; fake clock for lease bounds.
"""

import re
import socket
import struct
import time

from core.communication.connection import (
    SESSION_TOKEN_BYTES,
    ConnectionSession,
)
from core.communication.serializer import MessageSerializer
from core.communication.tcp import CONNECTION_LEASE_SECONDS, TcpTransport
from core.communication import Message
from core.security import SecurityManager
from core.security.models import Identity, IdentityType, Permission
from core.security.provider import TokenAuthenticationProvider

TOKEN = "provision-secret-01"
DEVICE = "sess-01"
URLSAFE = re.compile(r"^[A-Za-z0-9_-]+$")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _make_security(token=TOKEN, device_id=DEVICE):
    sm = SecurityManager(provider=TokenAuthenticationProvider())
    sm.register_identity(
        Identity(
            identity_id=device_id,
            name=device_id,
            identity_type=IdentityType.DEVICE,
            permissions=frozenset({Permission.READ}),
            metadata={"token": token},
        )
    )
    return sm


def _make_transport(port, **kwargs):
    kwargs.setdefault("security_manager", _make_security())
    return TcpTransport(host="127.0.0.1", port=port, **kwargs)


def _send_msg(sock, msg):
    data = MessageSerializer.serialize(msg).encode("utf-8")
    sock.sendall(struct.pack("!I", len(data)) + data)


def _recv_msg(sock, timeout=3.0):
    sock.settimeout(timeout)
    hdr = b""
    while len(hdr) < 4:
        chunk = sock.recv(4 - len(hdr))
        if not chunk:
            raise ConnectionError("closed")
        hdr += chunk
    (length,) = struct.unpack("!I", hdr)
    buf = b""
    while len(buf) < length:
        chunk = sock.recv(length - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return MessageSerializer.deserialize(buf.decode("utf-8"))


def _handshake(sock, device_id=DEVICE, credential=TOKEN):
    _send_msg(
        sock,
        Message(
            source=device_id,
            destination="core",
            message_type="CORE_HANDSHAKE",
            payload={
                "identity_id": device_id,
                "credential": credential,
                "protocol_version": "0.3.0",
            },
            identity_id=device_id,
        ),
    )
    return _recv_msg(sock)


def _authed(sock, device_id=DEVICE, token=None):
    """Handshake + register, returning (handshake_resp, session_token)."""
    hs = _handshake(sock, device_id)
    assert hs.payload["authenticated"] is True
    session_token = hs.payload.get("session_token")
    assert isinstance(session_token, str) and session_token
    payload = {
        "device_id": device_id,
        "device_name": device_id,
        "device_type": "generic",
        "platform": "test",
        "capabilities": [],
        "protocol_version": "0.3.0",
        "join_name": f"{device_id}-join",
        "_session_token": token or session_token,
    }
    _send_msg(
        sock,
        Message(
            source=device_id,
            destination="core",
            message_type="DEVICE_REGISTER",
            payload=payload,
            identity_id=device_id,
        ),
    )
    reg = _recv_msg(sock)
    assert reg.message_type == "DEVICE_REGISTER_RESPONSE"
    return hs, session_token


def _expect_close(sock):
    sock.settimeout(2.0)
    try:
        data = sock.recv(4)
    except (socket.timeout, OSError):
        return
    assert data == b"" or len(data) < 4


# -- generation ------------------------------------------------------------

def test_session_token_issued_on_auth():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        hs = _handshake(s)
        assert hs.payload["authenticated"] is True
        assert hs.payload["session_token"]
        assert hs.payload["session_token"] != TOKEN
        assert "credential" not in hs.payload
        assert "provision" not in str(hs.payload).lower()
        s.close()
    finally:
        t.stop()


def test_session_token_bytes_and_encoding():
    assert SESSION_TOKEN_BYTES == 32
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        hs = _handshake(s)
        token = hs.payload["session_token"]
        # 32 random bytes urlsafe-base64 → 43 chars, no padding.
        assert len(token) == 43
        assert URLSAFE.match(token)
        s.close()
    finally:
        t.stop()


def test_session_token_crypto_source(monkeypatch):
    import core.communication.connection as conn_mod

    calls = []
    real = conn_mod.secrets.token_urlsafe

    def fake(n):
        calls.append(n)
        return real(n)

    monkeypatch.setattr(conn_mod.secrets, "token_urlsafe", fake)
    sess = ConnectionSession(remote_address="x")
    token = sess.rotate_session_token()
    assert calls == [32]
    assert token == sess.session_token


def test_two_sessions_tokens_differ():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s1 = socket.socket()
        s1.settimeout(3)
        s1.connect(("127.0.0.1", port))
        t1 = _handshake(s1).payload["session_token"]
        s2 = socket.socket()
        s2.settimeout(3)
        s2.connect(("127.0.0.1", port))
        t2 = _handshake(s2).payload["session_token"]
        assert t1 != t2
        s1.close()
        s2.close()
    finally:
        t.stop()


def test_register_repeats_session_token():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        hs, token = _authed(s)
        assert hs.payload["session_token"] == token
        s.close()
    finally:
        t.stop()


# -- validation ------------------------------------------------------------

def test_missing_session_token_rejected():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s)
        # Register WITHOUT the session token → fail closed.
        _send_msg(
            s,
            Message(
                source=DEVICE,
                destination="core",
                message_type="DEVICE_REGISTER",
                payload={
                    "device_id": DEVICE,
                    "device_name": DEVICE,
                    "device_type": "generic",
                    "platform": "test",
                    "capabilities": [],
                    "protocol_version": "0.3.0",
                },
                identity_id=DEVICE,
            ),
        )
        _expect_close(s)
        s.close()
    finally:
        t.stop()


def test_wrong_session_token_rejected():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s)
        _send_msg(
            s,
            Message(
                source=DEVICE,
                destination="core",
                message_type="DEVICE_REGISTER",
                payload={
                    "device_id": DEVICE,
                    "device_name": DEVICE,
                    "device_type": "generic",
                    "platform": "test",
                    "capabilities": [],
                    "protocol_version": "0.3.0",
                    "_session_token": "forged-token-value-00000000000000000000",
                },
                identity_id=DEVICE,
            ),
        )
        _expect_close(s)
        s.close()
    finally:
        t.stop()


def test_token_for_other_identity_rejected():
    port = _free_port()
    sm = SecurityManager(provider=TokenAuthenticationProvider())
    for did, tok in (("dev-a", "tok-a"), ("dev-b", "tok-b")):
        sm.register_identity(
            Identity(
                identity_id=did,
                name=did,
                identity_type=IdentityType.DEVICE,
                permissions=frozenset({Permission.READ}),
                metadata={"token": tok},
            )
        )
    t = TcpTransport(host="127.0.0.1", port=port, security_manager=sm)
    t.start()
    time.sleep(0.2)
    try:
        sa = socket.socket()
        sa.settimeout(3)
        sa.connect(("127.0.0.1", port))
        token_a = _handshake(sa, "dev-a", "tok-a").payload["session_token"]
        sb = socket.socket()
        sb.settimeout(3)
        sb.connect(("127.0.0.1", port))
        _handshake(sb, "dev-b", "tok-b")
        # dev-a's token presented as dev-b → reject.
        _send_msg(
            sb,
            Message(
                source="dev-b",
                destination="core",
                message_type="DEVICE_REGISTER",
                payload={
                    "device_id": "dev-b",
                    "device_name": "dev-b",
                    "device_type": "generic",
                    "platform": "test",
                    "capabilities": [],
                    "protocol_version": "0.3.0",
                    "_session_token": token_a,
                },
                identity_id="dev-b",
            ),
        )
        _expect_close(sb)
        sa.close()
        sb.close()
    finally:
        t.stop()


def test_provisioning_credential_not_accepted_as_session():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s)
        # Replay the long-term credential where the session token belongs.
        _send_msg(
            s,
            Message(
                source=DEVICE,
                destination="core",
                message_type="DEVICE_REGISTER",
                payload={
                    "device_id": DEVICE,
                    "device_name": DEVICE,
                    "device_type": "generic",
                    "platform": "test",
                    "capabilities": [],
                    "protocol_version": "0.3.0",
                    "_session_token": TOKEN,
                },
                identity_id=DEVICE,
            ),
        )
        _expect_close(s)
        s.close()
    finally:
        t.stop()


# -- rotation / invalidation ------------------------------------------------

def test_reconnect_rotates_token_and_invalidates_old():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s1 = socket.socket()
        s1.settimeout(3)
        s1.connect(("127.0.0.1", port))
        hs1, token_a = _authed(s1)
        cid_a = hs1.payload["connection_id"]
        s1.close()
        time.sleep(0.3)
        s2 = socket.socket()
        s2.settimeout(3)
        s2.connect(("127.0.0.1", port))
        hs2, token_b = _authed(s2)
        assert hs2.payload["connection_id"] != cid_a
        assert token_b != token_a
        assert hs2.payload["lease_expires_at"] != hs1.payload["lease_expires_at"]
        # Old token on the NEW connection → reject.
        _send_msg(
            s2,
            Message(
                source=DEVICE,
                destination="core",
                message_type="DEVICE_DISCOVER",
                payload={"_session_token": token_a},
                identity_id=DEVICE,
            ),
        )
        _expect_close(s2)
        try:
            s2.close()
        except Exception:
            pass
    finally:
        t.stop()


def test_disconnect_clears_session_token():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        hs, _ = _authed(s)
        cid = hs.payload["connection_id"]
        session = t.get_connection(cid)
        assert session is not None and session.session_token
        s.close()
        deadline = time.time() + 5
        while time.time() < deadline and t.get_connection(cid) is not None:
            time.sleep(0.05)
        assert t.get_connection(cid) is None
    finally:
        t.stop()


# -- lease bounds (fake clock, no 24h wait) ---------------------------------

def test_lease_bounds_fake_clock():
    assert CONNECTION_LEASE_SECONDS == 86400
    now = [1000.0]
    sess = ConnectionSession(remote_address="x", connected_at=now[0],
                             last_activity=now[0])
    sess.mark_authenticated("d1", now=now[0])
    sess.rotate_session_token()
    sess.start_lease(86400, now[0])
    assert sess.lease_expires_at == 1000.0 + 86400
    assert sess.is_lease_expired(1000.0 + 86399) is False
    assert sess.is_lease_expired(1000.0 + 86400) is True
    assert sess.is_lease_expired(1000.0 + 86401) is True


def test_expiry_invalidates_token_and_offlines():
    port = _free_port()
    now = [5000.0]
    sm = _make_security()
    t = TcpTransport(
        host="127.0.0.1", port=port, security_manager=sm,
        time_func=lambda: now[0], connection_lease_seconds=100,
    )
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        hs = _handshake(s)
        cid = hs.payload["connection_id"]
        token = hs.payload["session_token"]
        assert t.get_connection(cid).session_token == token
        # Register before expiry so the device is online.
        _send_msg(
            s,
            Message(
                source=DEVICE,
                destination="core",
                message_type="DEVICE_REGISTER",
                payload={
                    "device_id": DEVICE,
                    "device_name": DEVICE,
                    "device_type": "generic",
                    "platform": "test",
                    "capabilities": [],
                    "protocol_version": "0.3.0",
                    "_session_token": token,
                },
                identity_id=DEVICE,
            ),
        )
        reg = _recv_msg(s)
        assert reg.message_type == "DEVICE_REGISTER_RESPONSE"
        # Advance past the lease: next message hits the expired lease path.
        now[0] += 101.0
        _send_msg(
            s,
            Message(
                source=DEVICE,
                destination="core",
                message_type="DEVICE_DISCOVER",
                payload={"_session_token": token},
                identity_id=DEVICE,
            ),
        )
        _expect_close(s)
        deadline = time.time() + 5
        while time.time() < deadline and t.get_connection(cid) is not None:
            time.sleep(0.05)
        assert t.get_connection(cid) is None
        rec = t.device_registry.get(DEVICE)
        assert rec.status == "offline"
        assert rec.device_id == DEVICE
        s.close()
    finally:
        t.stop()


# -- non-persistence ---------------------------------------------------------

def test_session_token_never_persisted_or_discovered():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        hs, token = _authed(s)
        assert token not in str(t.device_registry.get(DEVICE).to_dict())
        assert token not in str(t.device_registry.get(DEVICE).to_persistent_dict())
        assert token not in str(t.device_metrics())
        assert token not in str(t.list_devices())
        s.close()
    finally:
        t.stop()


def test_session_token_redacted_in_logs(caplog):
    import logging

    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        with caplog.at_level(logging.INFO, logger="core.communication.tcp"):
            s = socket.socket()
            s.settimeout(3)
            s.connect(("127.0.0.1", port))
            hs = _handshake(s)
            s.close()
        time.sleep(0.1)
        assert hs.payload["session_token"] not in caplog.text
    finally:
        t.stop()
