"""Hardened external-device transport tests (Specs W/Y).

Uses 127.0.0.1 sockets only. No physical devices. No 300s sleeps —
timeouts use injected fake clocks.
"""

import socket
import ssl
import struct
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from core.communication import Message
from core.communication.connection import ConnectionSession, ConnectionState
from core.communication.serializer import MessageSerializer
from core.communication.tcp import (
    HANDSHAKE_RESPONSE_TYPE,
    HANDSHAKE_TYPE,
    HEADER_SIZE,
    IDLE_CONNECTION_TIMEOUT,
    MAX_CONNECTIONS,
    MAX_FRAME_SIZE,
    TLS_HANDSHAKE_TIMEOUT,
    TcpTransport,
)
from core.security import SecurityManager
from core.security.models import Identity, IdentityType, Permission
from core.security.provider import TokenAuthenticationProvider


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _make_security():
    sm = SecurityManager(provider=TokenAuthenticationProvider())
    sm.register_identity(
        Identity(
            identity_id="device-a",
            name="Device A",
            identity_type=IdentityType.DEVICE,
            permissions=frozenset({Permission.READ}),
            metadata={"token": "secret-a"},
        )
    )
    return sm


def _make_hardened_transport(port, sm=None):
    t = TcpTransport(
        host="127.0.0.1", port=port, security_manager=sm or _make_security()
    )
    t.register(
        "service:echo",
        lambda m: m.create_response(source="service:echo", payload={"echo": m.payload}),
    )
    return t


_SESSION_TOKENS: dict[int, str] = {}


def _send_msg(sock, msg: Message):
    try:
        token = _SESSION_TOKENS.get(sock.fileno())
        if (
            token
            and isinstance(msg.payload, dict)
            and "_session_token" not in msg.payload
            and msg.message_type != "CORE_HANDSHAKE"
        ):
            msg.payload["_session_token"] = token
    except Exception:
        pass
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
    msg = MessageSerializer.deserialize(buf.decode("utf-8"))
    try:
        token = msg.payload.get("session_token") if isinstance(msg.payload, dict) else None
        if isinstance(token, str) and token:
            _SESSION_TOKENS[sock.fileno()] = token
    except Exception:
        pass
    return msg


def _handshake(sock, identity_id="device-a", credential="secret-a",
               version="0.3.0", mtype="CORE_HANDSHAKE"):
    payload = {}
    if identity_id is not None:
        payload["identity_id"] = identity_id
    if credential is not None:
        payload["credential"] = credential
    if version is not None:
        payload["protocol_version"] = version
    _send_msg(
        sock,
        Message(
            source=identity_id or "anon",
            destination="core",
            message_type=mtype,
            payload=payload,
            identity_id=identity_id,
        ),
    )


def _expect_close(sock):
    """Assert peer closed/timed-out without a valid response."""
    sock.settimeout(2.0)
    try:
        data = sock.recv(4)
    except (socket.timeout, OSError):
        return
    assert data == b"" or len(data) < 4


# ---- TLS (1-5) ----

def test_tls_listener_starts_with_valid_cert():
    tmpdir = tempfile.mkdtemp()
    cert, key = Path(tmpdir) / "c.pem", Path(tmpdir) / "k.pem"
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(key), "-out", str(cert),
             "-subj", "/CN=localhost", "-days", "1"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pytest.skip("openssl not available")
    import shutil
    try:
        port = _free_port()
        t = TcpTransport(host="127.0.0.1", port=port, use_tls=True,
                         certfile=str(cert), keyfile=str(key))
        assert t.is_tls is True
        t.start()
        assert t._server_socket is not None
        t.stop()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_tls_rejects_invalid_setup_localhost_fallback():
    t = TcpTransport(host="127.0.0.1", port=51112, use_tls=True,
                     certfile="/tmp/nonexistent-core-test.pem")
    assert t.uses_tls is True
    assert t.is_tls is False


def test_tls_not_silently_downgraded_for_external():
    # External without TLS -> raise
    with pytest.raises(ValueError):
        TcpTransport(host="0.0.0.0", port=51113)
    # External with bad cert -> raise (fail closed, no fallback)
    with pytest.raises(Exception):
        TcpTransport(host="0.0.0.0", port=51114, use_tls=True,
                     certfile="/tmp/nonexistent-core-test.pem")
    assert TLS_HANDSHAKE_TIMEOUT == 5.0


def test_tls12_accepted_tls10_11_rejected():
    tmpdir = tempfile.mkdtemp()
    cert, key = Path(tmpdir) / "c.pem", Path(tmpdir) / "k.pem"
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(key), "-out", str(cert),
             "-subj", "/CN=localhost", "-days", "1"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pytest.skip("openssl not available")
    import shutil
    try:
        port = _free_port()
        t = TcpTransport(host="127.0.0.1", port=port, use_tls=True,
                         certfile=str(cert), keyfile=str(key))
        assert t.is_tls
        t.register("service:echo", lambda m: m.create_response(source="s", payload={}))
        t.start()
        time.sleep(0.3)
        # TLS 1.2 client succeeds
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        s = ctx.wrap_socket(socket.socket(), server_hostname="localhost")
        s.settimeout(2)
        s.connect(("127.0.0.1", port))
        s.close()
        # TLS 1.0/1.1 client must fail against TLS1.2-minimum server
        ctx_old = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx_old.check_hostname = False
        ctx_old.verify_mode = ssl.CERT_NONE
        try:
            ctx_old.maximum_version = ssl.TLSVersion.TLSv1_1
        except Exception:
            pytest.skip("cannot cap TLS version on this Python")
        s2 = ctx_old.wrap_socket(socket.socket(), server_hostname="localhost")
        s2.settimeout(2)
        with pytest.raises(Exception):
            s2.connect(("127.0.0.1", port))
            s2.recv(1)
        try:
            s2.close()
        except Exception:
            pass
        t.stop()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---- Connection limits (6-8) ----

def test_up_to_64_connections_accepted():
    t = TcpTransport(host="127.0.0.1", port=0)
    for i in range(64):
        s = ConnectionSession(remote_address=f"10.0.0.{i}:1")
        assert t._register_session(s) is True
    assert t.connection_count() == 64
    assert t.total_connections() == 64


def test_65th_connection_rejected():
    t = TcpTransport(host="127.0.0.1", port=0)
    for _ in range(MAX_CONNECTIONS):
        assert t._register_session(ConnectionSession(remote_address="x")) is True
    assert t._register_session(ConnectionSession(remote_address="y")) is False
    assert t.rejected_connections() >= 1
    assert t.connection_count() == 64


def test_closed_connections_free_slot():
    t = TcpTransport(host="127.0.0.1", port=0)
    sessions = [ConnectionSession(remote_address="x") for _ in range(64)]
    for s in sessions:
        t._register_session(s)
    t._remove_session(sessions[0].connection_id)
    assert t.connection_count() == 63
    assert t._register_session(ConnectionSession(remote_address="new")) is True


def test_live_65th_connection_rejected_and_frees():
    port = _free_port()
    t = TcpTransport(host="127.0.0.1", port=port)
    t.register("service:echo", lambda m: m.create_response(source="s", payload={}))
    t.start()
    time.sleep(0.2)
    try:
        # Fill registry directly to simulate 64 active
        for _ in range(64):
            t._register_session(ConnectionSession(remote_address="fill"))
        s = socket.socket()
        s.settimeout(2)
        s.connect(("127.0.0.1", port))
        # Server should immediately close (cap reached)
        _expect_close(s)
        s.close()
    finally:
        t.stop()


# ---- Handshake (9-15) ----

def _live_handshake_case(payload_overrides=None, raw_type="CORE_HANDSHAKE",
                         identity_id="device-a", pre_app=False):
    port = _free_port()
    t = _make_hardened_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        if pre_app:
            _send_msg(s, Message(source="device-a", destination="service:echo",
                                 message_type="TEST", payload={},
                                 identity_id="device-a"))
            _expect_close(s)
            s.close()
            return t, None
        if payload_overrides is not None:
            _send_msg(s, Message(source=identity_id or "anon", destination="core",
                                 message_type=raw_type, payload=payload_overrides,
                                 identity_id=identity_id))
        else:
            _handshake(s)
        return t, s
    except Exception:
        t.stop()
        raise


def test_valid_handshake_succeeds():
    port = _free_port()
    t = _make_hardened_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s)
        resp = _recv_msg(s)
        assert resp.message_type == HANDSHAKE_RESPONSE_TYPE
        assert resp.payload["authenticated"] is True
        assert resp.payload["identity_id"] == "device-a"
        assert resp.payload["protocol_version"]
        assert resp.payload["connection_id"]
        s.close()
    finally:
        t.stop()


def test_handshake_missing_identity_fails():
    t, s = _live_handshake_case({"credential": "secret-a", "protocol_version": "0.3.0"},
                                identity_id=None)
    try:
        _expect_close(s)
        s.close()
    finally:
        t.stop()


def test_handshake_missing_credential_fails():
    t, s = _live_handshake_case({"identity_id": "device-a",
                                 "protocol_version": "0.3.0"})
    try:
        _expect_close(s)
        s.close()
    finally:
        t.stop()


def test_handshake_invalid_credential_fails():
    port = _free_port()
    sm = _make_security()
    t = _make_hardened_transport(port, sm)
    before = sm._authentication_failures
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s, credential="wrong")
        _expect_close(s)
        s.close()
        assert t.authentication_failures() >= 1
        assert sm._authentication_failures > before
    finally:
        t.stop()


def test_handshake_unsupported_version_fails():
    t, s = _live_handshake_case({"identity_id": "device-a",
                                 "credential": "secret-a",
                                 "protocol_version": "9.9.9"})
    try:
        _expect_close(s)
        s.close()
    finally:
        t.stop()


def test_duplicate_handshake_fails():
    port = _free_port()
    t = _make_hardened_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s)
        resp = _recv_msg(s)
        assert resp.message_type == HANDSHAKE_RESPONSE_TYPE
        _handshake(s)  # second handshake
        _expect_close(s)
        s.close()
    finally:
        t.stop()


def test_app_before_handshake_fails():
    t, s = _live_handshake_case(pre_app=True)
    t.stop()


# ---- Identity (16-19) ----

def test_authenticated_identity_stored():
    port = _free_port()
    t = _make_hardened_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s)
        _recv_msg(s)
        time.sleep(0.2)
        conns = t.list_connections()
        assert len(conns) == 1
        assert conns[0].identity_id == "device-a"
        assert conns[0].authenticated is True
        s.close()
    finally:
        t.stop()


def test_message_identity_match_succeeds():
    port = _free_port()
    t = _make_hardened_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s)
        _recv_msg(s)
        _send_msg(s, Message(source="device-a", destination="service:echo",
                             message_type="TEST", payload={"n": 1},
                             identity_id="device-a"))
        resp = _recv_msg(s)
        assert resp.payload["echo"] == {"n": 1}
        s.close()
    finally:
        t.stop()


def test_message_identity_mismatch_fails():
    port = _free_port()
    t = _make_hardened_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s)
        _recv_msg(s)
        before = t.protocol_failures()
        _send_msg(s, Message(source="device-a", destination="service:echo",
                             message_type="TEST", payload={},
                             identity_id="device-B"))
        _expect_close(s)
        s.close()
        assert t.protocol_failures() > before
    finally:
        t.stop()


def test_message_source_mismatch_fails():
    port = _free_port()
    t = _make_hardened_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s)
        _recv_msg(s)
        _send_msg(s, Message(source="device_b", destination="service:echo",
                             message_type="TEST", payload={},
                             identity_id="device-a"))
        _expect_close(s)
        s.close()
    finally:
        t.stop()


# ---- Framing (20-24) ----

def test_valid_frame_succeeds():
    assert HEADER_SIZE == 4
    port = _free_port()
    t = TcpTransport(host="127.0.0.1", port=port)
    t.register("service:echo", lambda m: m.create_response(source="s",
                                                           payload={"ok": True}))
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect(("127.0.0.1", port))
        msg = Message(source="a", destination="service:echo",
                      message_type="X", payload={})
        data = MessageSerializer.serialize(msg).encode("utf-8")
        s.sendall(struct.pack("!I", len(data)) + data)
        hdr = s.recv(4)
        (ln,) = struct.unpack("!I", hdr)
        body = s.recv(ln)
        assert MessageSerializer.deserialize(body.decode()).payload == {"ok": True}
        s.close()
    finally:
        t.stop()


def test_partial_reads_handled():
    port = _free_port()
    t = TcpTransport(host="127.0.0.1", port=port)
    t.register("service:echo", lambda m: m.create_response(source="s",
                                                           payload={"ok": True}))
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect(("127.0.0.1", port))
        msg = Message(source="a", destination="service:echo",
                      message_type="X", payload={"v": "partial"})
        data = MessageSerializer.serialize(msg).encode("utf-8")
        frame = struct.pack("!I", len(data)) + data
        for i in range(0, len(frame), 3):
            s.sendall(frame[i:i + 3])
            time.sleep(0.01)
        hdr = s.recv(4)
        (ln,) = struct.unpack("!I", hdr)
        body = b""
        while len(body) < ln:
            body += s.recv(ln - len(body))
        assert MessageSerializer.deserialize(body.decode()).payload == {"ok": True}
        s.close()
    finally:
        t.stop()


def test_zero_length_frame_rejected():
    port = _free_port()
    t = TcpTransport(host="127.0.0.1", port=port)
    t.register("service:echo", lambda m: m.create_response(source="s", payload={}))
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect(("127.0.0.1", port))
        s.sendall(struct.pack("!I", 0))
        _expect_close(s)
        s.close()
    finally:
        t.stop()


def test_oversize_frame_rejected():
    assert MAX_FRAME_SIZE == 10 * 1024 * 1024
    port = _free_port()
    t = TcpTransport(host="127.0.0.1", port=port)
    t.register("service:echo", lambda m: m.create_response(source="s", payload={}))
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect(("127.0.0.1", port))
        # Declare oversized length but send no body; server must close.
        s.sendall(struct.pack("!I", MAX_FRAME_SIZE + 1))
        _expect_close(s)
        s.close()
    finally:
        t.stop()


def test_utf8_payload_byte_length():
    port = _free_port()
    t = TcpTransport(host="127.0.0.1", port=port)
    t.register("service:echo", lambda m: m.create_response(source="s",
                                                           payload={"echo": m.payload}))
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect(("127.0.0.1", port))
        text = "héllo-日本語-emoji-🎉"
        msg = Message(source="a", destination="service:echo",
                      message_type="X", payload={"t": text})
        serialized = MessageSerializer.serialize(msg)
        payload = serialized.encode("utf-8")
        # Frame length MUST be byte length of UTF-8 encoding (spec E).
        assert struct.pack("!I", len(payload)) is not None
        assert len(payload) == len(serialized.encode("utf-8"))
        s.sendall(struct.pack("!I", len(payload)) + payload)
        hdr = s.recv(4)
        (ln,) = struct.unpack("!I", hdr)
        body = b""
        while len(body) < ln:
            body += s.recv(ln - len(body))
        assert MessageSerializer.deserialize(body.decode()).payload["echo"]["t"] == text
        s.close()
    finally:
        t.stop()


# ---- Persistent connections (25-29) ----

def test_one_connection_multiple_messages():
    port = _free_port()
    t = _make_hardened_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s)
        _recv_msg(s)
        for i in range(3):
            _send_msg(s, Message(source="device-a", destination="service:echo",
                                 message_type="TEST", payload={"n": i},
                                 identity_id="device-a"))
            resp = _recv_msg(s)
            assert resp.payload["echo"] == {"n": i}
        # Still alive
        assert t.connection_count() == 1
        s.close()
    finally:
        t.stop()


def test_peer_disconnect_cleans_session():
    port = _free_port()
    t = _make_hardened_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s)
        _recv_msg(s)
        assert t.connection_count() == 1
        s.close()
        time.sleep(0.5)
        assert t.connection_count() == 0
    finally:
        t.stop()


def test_shutdown_cleans_sessions():
    port = _free_port()
    t = _make_hardened_transport(port)
    t.start()
    time.sleep(0.2)
    s = socket.socket()
    s.settimeout(3)
    s.connect(("127.0.0.1", port))
    _handshake(s)
    _recv_msg(s)
    assert t.connection_count() == 1
    t.stop()
    assert t.connection_count() == 0
    s.close()


# ---- Timeouts (30-31) ----

def test_idle_connection_closes_after_300s_fake_clock():
    assert IDLE_CONNECTION_TIMEOUT == 300.0
    now = [1000.0]
    t = TcpTransport(host="127.0.0.1", port=0, time_func=lambda: now[0])
    session = ConnectionSession(remote_address="x", connected_at=now[0],
                                last_activity=now[0])
    # Fresh: readable wait would block briefly; simulate expiry:
    now[0] += 301.0
    # _recv_exact must refuse when idle expired (use closed socket pair)
    a, b = socket.socketpair()
    try:
        b.settimeout(0.1)
        # last_activity is 301s ago -> immediate None without blocking
        result = t._recv_exact(b, 4, session)
        assert result is None
    finally:
        a.close()
        b.close()


def test_active_traffic_prevents_idle_timeout():
    now = [2000.0]
    t = TcpTransport(host="127.0.0.1", port=0, time_func=lambda: now[0])
    session = ConnectionSession(remote_address="x", connected_at=now[0],
                                last_activity=now[0])
    now[0] += 100.0
    session.touch(now[0])  # activity resets timer
    now[0] += 100.0  # only 100s since last activity
    a, b = socket.socketpair()
    try:
        a.sendall(b"abcd")
        b.settimeout(2)
        result = t._recv_exact(b, 4, session)
        assert result == b"abcd"
    finally:
        a.close()
        b.close()


# ---- State machine + metrics ----

def test_connection_state_transitions():
    s = ConnectionSession(remote_address="r")
    assert s.state == ConnectionState.CONNECTED
    s.transition(ConnectionState.TLS_ESTABLISHED)
    s.mark_authenticated("dev-1", now=123.0)
    assert s.state == ConnectionState.AUTHENTICATED
    assert s.authenticated_at == 123.0
    assert s.connection_id and s.identity_id == "dev-1"


def test_metrics_preserved_and_extended():
    t = TcpTransport(host="127.0.0.1", port=0)
    t.register("e", lambda m: m.create_response(source="e", payload={}))
    t.send(Message(source="a", destination="e", message_type="T", payload={}))
    assert t.message_count() == 1
    assert t.response_count() == 1
    assert t.active_connections() == 0
    assert t.total_connections() == 0
