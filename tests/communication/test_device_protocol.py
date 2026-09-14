"""Device registration / discovery / presence / validation unit tests.

Localhost TCP simulation only (127.0.0.1). No physical devices.
"""

import socket
import struct
import threading
import time

import pytest

from core.communication import Message
from core.communication.devices import DeviceRegistry
from core.communication.protocol import (
    DEVICE_ALREADY_REGISTERED,
    DEVICE_DISCOVER,
    DEVICE_DISCOVER_RESPONSE,
    DEVICE_ERROR,
    DEVICE_INFO,
    DEVICE_INFO_RESPONSE,
    DEVICE_NOT_FOUND,
    DEVICE_NOT_REGISTERED,
    DEVICE_REGISTER,
    DEVICE_REGISTER_RESPONSE,
    DEVICE_REGISTRATION_FAILED,
    DEVICE_UNAVAILABLE,
    INVALID_DESTINATION,
    validate_registration_payload,
)
from core.communication.serializer import MessageSerializer
from core.communication.tcp import TcpTransport
from core.security import SecurityManager
from core.security.models import Identity, IdentityType, Permission
from core.security.provider import TokenAuthenticationProvider

CREDENTIALS = {
    "device-a": "secret-a",
    "device-b": "secret-b",
    "device-c": "secret-c",
    "device-d": "secret-d",
}


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _make_security(ids=None):
    sm = SecurityManager(provider=TokenAuthenticationProvider())
    for device_id in ids or list(CREDENTIALS):
        sm.register_identity(
            Identity(
                identity_id=device_id,
                name=device_id,
                identity_type=IdentityType.DEVICE,
                permissions=frozenset({Permission.READ}),
                metadata={"token": CREDENTIALS[device_id]},
            )
        )
    return sm


def _make_transport(port, sm=None):
    return TcpTransport(
        host="127.0.0.1", port=port, security_manager=sm or _make_security()
    )


_SESSION_TOKENS: dict[int, str] = {}


def _send_msg(sock, msg):
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
    return MessageSerializer.deserialize(buf.decode("utf-8"))


def _handshake(sock, device_id):
    _send_msg(
        sock,
        Message(
            source=device_id,
            destination="core",
            message_type="CORE_HANDSHAKE",
            payload={
                "identity_id": device_id,
                "credential": CREDENTIALS[device_id],
                "protocol_version": "0.3.0",
            },
            identity_id=device_id,
        ),
    )
    resp = _recv_msg(sock)
    try:
        token = resp.payload.get("session_token") if isinstance(resp.payload, dict) else None
        if isinstance(token, str) and token:
            _SESSION_TOKENS[sock.fileno()] = token
    except Exception:
        pass
    return resp


def _register(sock, device_id, name=None, **overrides):
    payload = {
        "device_id": device_id,
        "device_name": name or device_id,
        "device_type": "generic",
        "platform": "test",
        "capabilities": [],
        "protocol_version": "0.3.0",
    }
    payload.update(overrides)
    msg = Message(
        source=device_id,
        destination="core",
        message_type=DEVICE_REGISTER,
        payload=payload,
        identity_id=device_id,
    )
    _send_msg(sock, msg)
    return _recv_msg(sock), msg


def _connect_registered(port_transport, device_id):
    s = socket.socket()
    s.settimeout(3)
    s.connect(("127.0.0.1", port_transport))
    _handshake(s, device_id)
    resp, _ = _register(s, device_id, name=f"Device {device_id[-1].upper()}")
    assert resp.message_type == DEVICE_REGISTER_RESPONSE
    return s


def _expect_close(sock):
    sock.settimeout(2.0)
    try:
        data = sock.recv(4)
    except (socket.timeout, OSError):
        return
    assert data == b"" or len(data) < 4


# -- payload validation (no network) ----------------------------------------


def test_validate_ok():
    code, _ = validate_registration_payload(
        {
            "device_id": "device-a",
            "device_name": "Device A",
            "device_type": "generic",
            "platform": "unknown",
            "capabilities": [],
            "protocol_version": "0.3.0",
        }
    )
    assert code is None


def test_validate_missing_device_id():
    payload = {
        "device_name": "Device A",
        "device_type": "generic",
        "platform": "test",
        "capabilities": [],
        "protocol_version": "0.3.0",
    }
    code, _ = validate_registration_payload(payload)
    assert code == DEVICE_REGISTRATION_FAILED


def test_validate_empty_device_id():
    payload = {
        "device_id": "",
        "device_name": "Device A",
        "device_type": "generic",
        "platform": "test",
        "capabilities": [],
        "protocol_version": "0.3.0",
    }
    code, _ = validate_registration_payload(payload)
    assert code == DEVICE_REGISTRATION_FAILED


def test_validate_missing_device_name():
    payload = {
        "device_id": "device-a",
        "device_type": "generic",
        "platform": "test",
        "capabilities": [],
        "protocol_version": "0.3.0",
    }
    code, _ = validate_registration_payload(payload)
    assert code == DEVICE_REGISTRATION_FAILED


def test_validate_malformed_capabilities():
    payload = {
        "device_id": "device-a",
        "device_name": "Device A",
        "device_type": "generic",
        "platform": "test",
        "capabilities": "not-a-list",
        "protocol_version": "0.3.0",
    }
    code, _ = validate_registration_payload(payload)
    assert code == DEVICE_REGISTRATION_FAILED


def test_validate_unsupported_version():
    payload = {
        "device_id": "device-a",
        "device_name": "Device A",
        "device_type": "generic",
        "platform": "test",
        "capabilities": [],
        "protocol_version": "9.9.9",
    }
    code, _ = validate_registration_payload(payload)
    assert code == DEVICE_REGISTRATION_FAILED


# -- registration over localhost TCP -----------------------------------------


def test_successful_registration():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s, "device-a")
        resp, req = _register(s, "device-a", name="Device A")
        assert resp.message_type == DEVICE_REGISTER_RESPONSE
        assert resp.payload["registered"] is True
        assert resp.payload["device_id"] == "device-a"
        assert resp.payload["status"] == "online"
        assert resp.payload["join_name"] == "Device-A-device-a"
        assert resp.payload["lease_duration_seconds"] == 24 * 60 * 60
        assert resp.payload["connected_at"]
        assert resp.payload["lease_expires_at"]
        assert resp.request_id == req.message_id
        assert t.registered_devices() == 1
        assert t.online_devices() == 1
        record = t.device_registry.get("device-a")
        assert record.status == "online"
        assert record.identity_id == "device-a"
        assert record.connection_id is not None
        assert record.last_seen is not None
        assert record.registered_at is not None
        s.close()
    finally:
        t.stop()


def test_registration_missing_device_id():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s, "device-a")
        payload = {
            "device_name": "Device A",
            "device_type": "generic",
            "platform": "test",
            "capabilities": [],
            "protocol_version": "0.3.0",
        }
        _send_msg(
            s,
            Message(
                source="device-a",
                destination="core",
                message_type=DEVICE_REGISTER,
                payload=payload,
                identity_id="device-a",
            ),
        )
        resp = _recv_msg(s)
        assert resp.message_type == DEVICE_ERROR
        assert resp.payload["error"] == DEVICE_REGISTRATION_FAILED
        assert resp.payload["request_id"] is not None
        assert t.device_registration_failures() >= 1
        s.close()
    finally:
        t.stop()


def test_registration_unsupported_version():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s, "device-a")
        resp, _ = _register(s, "device-a", protocol_version="9.9.9")
        assert resp.message_type == DEVICE_ERROR
        assert resp.payload["error"] == DEVICE_REGISTRATION_FAILED
        s.close()
    finally:
        t.stop()


def test_unauthenticated_registration_rejected():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        # No handshake: registration before authentication must fail.
        _send_msg(
            s,
            Message(
                source="device-a",
                destination="core",
                message_type=DEVICE_REGISTER,
                payload={
                    "device_id": "device-a",
                    "device_name": "Device A",
                    "device_type": "generic",
                    "platform": "test",
                    "capabilities": [],
                    "protocol_version": "0.3.0",
                },
                identity_id="device-a",
            ),
        )
        _expect_close(s)
        s.close()
        assert not t.device_registry.has("device-a")
    finally:
        t.stop()


def test_identity_device_mismatch_rejected():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s, "device-a")
        before = t.device_registration_failures()
        # Authenticated as device-a but registering device-b.
        _send_msg(
            s,
            Message(
                source="device-a",
                destination="core",
                message_type=DEVICE_REGISTER,
                payload={
                    "device_id": "device-b",
                    "device_name": "Device B",
                    "device_type": "generic",
                    "platform": "test",
                    "capabilities": [],
                    "protocol_version": "0.3.0",
                },
                identity_id="device-a",
            ),
        )
        resp = _recv_msg(s)
        assert resp.message_type == DEVICE_ERROR
        assert t.device_registration_failures() > before
        _expect_close(s)
        s.close()
        assert not t.device_registry.has("device-b")
    finally:
        t.stop()


def test_duplicate_registration_rejected():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        first = _connect_registered(port, "device-a")
        second = socket.socket()
        second.settimeout(3)
        second.connect(("127.0.0.1", port))
        _handshake(second, "device-a")
        resp, _ = _register(second, "device-a")
        assert resp.message_type == DEVICE_ERROR
        assert resp.payload["error"] == DEVICE_ALREADY_REGISTERED
        assert resp.payload["request_id"] is not None
        # First connection still owns the binding.
        assert t.online_devices() == 1
        assert t.registered_devices() == 1
        first.close()
        second.close()
    finally:
        t.stop()


def test_concurrent_duplicate_registration_single_winner():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    barrier_in = threading.Barrier(2)
    barrier_out = threading.Barrier(2)
    outcomes = [None, None]
    try:

        def worker(i):
            s = socket.socket()
            s.settimeout(5)
            try:
                s.connect(("127.0.0.1", port))
                _handshake(s, "device-a")
                barrier_in.wait(timeout=10)
                resp, _ = _register(s, "device-a")
                outcomes[i] = resp.message_type
                # Hold the connection open until both sides registered so
                # the loser cannot win via reconnect-after-close.
                barrier_out.wait(timeout=10)
            except Exception as exc:  # pragma: no cover - diagnostic
                outcomes[i] = f"EXC:{exc}"
            finally:
                s.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=15)
        assert DEVICE_REGISTER_RESPONSE in outcomes
        assert DEVICE_ERROR in outcomes
        assert t.registered_devices() == 1
    finally:
        t.stop()


# -- discovery ----------------------------------------------------------------


def test_discover_single_device():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = _connect_registered(port, "device-a")
        _send_msg(
            s,
            Message(
                source="device-a",
                destination="core",
                message_type=DEVICE_DISCOVER,
                payload={},
                identity_id="device-a",
            ),
        )
        resp = _recv_msg(s)
        assert resp.message_type == DEVICE_DISCOVER_RESPONSE
        assert len(resp.payload["devices"]) == 1
        entry = resp.payload["devices"][0]
        for key in (
            "device_id",
            "device_name",
            "device_type",
            "platform",
            "capabilities",
            "status",
            "last_seen",
        ):
            assert key in entry
        assert entry["device_id"] == "device-a"
        assert entry["status"] == "online"
        assert t.device_discovery_requests() >= 1
        s.close()
    finally:
        t.stop()


def test_discover_multiple_and_offline_devices():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        a = _connect_registered(port, "device-a")
        b = _connect_registered(port, "device-b")
        b.close()
        deadline = time.time() + 5
        while t.online_devices() != 1 and time.time() < deadline:
            time.sleep(0.05)
        assert t.offline_devices() == 1
        _send_msg(
            a,
            Message(
                source="device-a",
                destination="core",
                message_type=DEVICE_DISCOVER,
                payload={},
                identity_id="device-a",
            ),
        )
        resp = _recv_msg(a)
        assert resp.message_type == DEVICE_DISCOVER_RESPONSE
        by_id = {d["device_id"]: d for d in resp.payload["devices"]}
        assert set(by_id) == {"device-a", "device-b"}
        assert by_id["device-a"]["status"] == "online"
        assert by_id["device-b"]["status"] == "offline"
        a.close()
    finally:
        t.stop()


def test_discover_requires_registration():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s, "device-a")
        _send_msg(
            s,
            Message(
                source="device-a",
                destination="core",
                message_type=DEVICE_DISCOVER,
                payload={},
                identity_id="device-a",
            ),
        )
        resp = _recv_msg(s)
        assert resp.message_type == DEVICE_ERROR
        assert resp.payload["error"] == DEVICE_NOT_REGISTERED
        s.close()
    finally:
        t.stop()


def test_device_info_and_unknown_device():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        a = _connect_registered(port, "device-a")
        b = _connect_registered(port, "device-b")
        _send_msg(
            a,
            Message(
                source="device-a",
                destination="core",
                message_type=DEVICE_INFO,
                payload={"device_id": "device-b"},
                identity_id="device-a",
            ),
        )
        resp = _recv_msg(a)
        assert resp.message_type == DEVICE_INFO_RESPONSE
        assert resp.payload["device"]["device_id"] == "device-b"
        assert resp.payload["device"]["status"] == "online"
        _send_msg(
            a,
            Message(
                source="device-a",
                destination="core",
                message_type=DEVICE_INFO,
                payload={"device_id": "device-ghost"},
                identity_id="device-a",
            ),
        )
        err = _recv_msg(a)
        assert err.message_type == DEVICE_ERROR
        assert err.payload["error"] == DEVICE_NOT_FOUND
        assert err.payload["request_id"] is not None
        a.close()
        b.close()
    finally:
        t.stop()


# -- presence ------------------------------------------------------------------


def test_presence_online_offline_reconnect():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = _connect_registered(port, "device-a")
        first_cid = t.device_registry.get("device-a").connection_id
        assert t.device_registry.get("device-a").status == "online"
        first_seen = t.device_registry.get("device-a").last_seen
        s.close()
        deadline = time.time() + 5
        while t.online_devices() != 0 and time.time() < deadline:
            time.sleep(0.05)
        assert t.device_registry.get("device-a").status == "offline"
        assert t.device_registry.get("device-a").connection_id is None
        # Record preserved (not deleted).
        assert t.registered_devices() == 1
        s2 = _connect_registered(port, "device-a")
        record = t.device_registry.get("device-a")
        assert record.status == "online"
        assert record.connection_id is not None
        assert record.connection_id != first_cid
        assert t.registered_devices() == 1
        assert record.last_seen is not None and record.last_seen >= first_seen
        s2.close()
    finally:
        t.stop()


def test_stale_connection_cannot_mark_newer_offline():
    registry = DeviceRegistry()
    registry.register(
        device_id="device-a",
        device_name="Device A",
        device_type="generic",
        platform="test",
        capabilities=[],
        protocol_version="0.3.0",
        identity_id="device-a",
        connection_id="old-conn",
    )
    registry.register_payload(
        {
            "device_id": "device-a",
            "device_name": "Device A",
            "device_type": "generic",
            "platform": "test",
            "capabilities": [],
            "protocol_version": "0.3.0",
        },
        identity_id="device-a",
        connection_id="old-conn",
    ) if False else None
    # Simulate reconnect: mark offline then re-register with new connection.
    registry.mark_offline("device-a", "old-conn")
    registry.register(
        device_id="device-a",
        device_name="Device A",
        device_type="generic",
        platform="test",
        capabilities=[],
        protocol_version="0.3.0",
        identity_id="device-a",
        connection_id="new-conn",
    )
    # Stale close for the old connection must not affect the new binding.
    registry.mark_offline("device-a", "old-conn")
    record = registry.get("device-a")
    assert record.status == "online"
    assert record.connection_id == "new-conn"
    assert registry.registered_count() == 1


# -- routing validation ----------------------------------------------------------


def _route_from_a(t_port, sock_a, destination, mtype="APP_DATA", payload=None):
    msg = Message(
        source="device-a",
        destination=destination,
        message_type=mtype,
        payload=payload or {"n": 1},
        identity_id="device-a",
    )
    _send_msg(sock_a, msg)
    return msg


def test_routing_empty_destination_rejected():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        a = _connect_registered(port, "device-a")
        msg = Message(
            source="device-a",
            destination="",
            message_type="APP_DATA",
            payload={},
            identity_id="device-a",
        )
        # Empty destination fails shape validation at the transport layer
        # (connection closes) — never silently routed.
        _send_msg(a, msg)
        _expect_close(a)
        a.close()
    finally:
        t.stop()


def test_routing_unknown_destination():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        a = _connect_registered(port, "device-a")
        before = t.device_routing_failures()
        _route_from_a(port, a, "device-ghost")
        err = _recv_msg(a)
        assert err.message_type == DEVICE_ERROR
        assert err.payload["error"] == DEVICE_NOT_FOUND
        assert err.payload["request_id"] is not None
        assert "traceback" not in err.payload["message"].lower()
        assert t.device_routing_failures() > before
        a.close()
    finally:
        t.stop()


def test_routing_offline_destination_unavailable():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        a = _connect_registered(port, "device-a")
        b = _connect_registered(port, "device-b")
        b.close()
        deadline = time.time() + 5
        while t.online_devices() != 1 and time.time() < deadline:
            time.sleep(0.05)
        before = t.device_routing_failures()
        _route_from_a(port, a, "device-b")
        err = _recv_msg(a)
        assert err.message_type == DEVICE_ERROR
        assert err.payload["error"] == DEVICE_UNAVAILABLE
        assert t.device_routing_failures() > before
        # Sender connection stays usable after a routing failure.
        _send_msg(
            a,
            Message(
                source="device-a",
                destination="core",
                message_type=DEVICE_DISCOVER,
                payload={},
                identity_id="device-a",
            ),
        )
        resp = _recv_msg(a)
        assert resp.message_type == DEVICE_DISCOVER_RESPONSE
        a.close()
    finally:
        t.stop()


def test_routing_requires_sender_registration():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        b = _connect_registered(port, "device-b")
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s, "device-a")
        # device-a authenticated but never registered.
        _send_msg(
            s,
            Message(
                source="device-a",
                destination="device-b",
                message_type="APP_DATA",
                payload={"hi": 1},
                identity_id="device-a",
            ),
        )
        err = _recv_msg(s)
        assert err.message_type == DEVICE_ERROR
        assert err.payload["error"] == DEVICE_NOT_REGISTERED
        s.close()
        b.close()
    finally:
        t.stop()


def test_source_mismatch_rejected():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        a = _connect_registered(port, "device-a")
        b = _connect_registered(port, "device-b")
        before = t.device_routing_failures()
        # Authenticated as device-a but claiming source device-b.
        _send_msg(
            a,
            Message(
                source="device-b",
                destination="device-b",
                message_type="APP_DATA",
                payload={},
                identity_id="device-a",
            ),
        )
        _expect_close(a)
        assert t.device_routing_failures() >= before
        a.close()
        b.close()
    finally:
        t.stop()


def test_error_envelope_format():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        a = _connect_registered(port, "device-a")
        msg = _route_from_a(port, a, "device-ghost")
        err = _recv_msg(a)
        assert err.message_type == DEVICE_ERROR
        assert set(("error", "message", "request_id")) <= set(err.payload)
        assert err.payload["error"] in (
            DEVICE_NOT_FOUND,
            DEVICE_UNAVAILABLE,
            DEVICE_NOT_REGISTERED,
            DEVICE_ALREADY_REGISTERED,
            DEVICE_REGISTRATION_FAILED,
            INVALID_DESTINATION,
            "COMMUNICATION_ERROR",
            DEVICE_ERROR,
        )
        assert err.request_id == msg.message_id
        a.close()
    finally:
        t.stop()


def test_malformed_message_no_traceback_leak():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        a = _connect_registered(port, "device-a")
        _route_from_a(port, a, "device-ghost")
        err = _recv_msg(a)
        assert "Traceback" not in err.payload["message"]
        assert "File \"" not in err.payload["message"]
        a.close()
    finally:
        t.stop()


def test_shutdown_marks_devices_offline():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        a = _connect_registered(port, "device-a")
        assert t.online_devices() == 1
        a.close()
    finally:
        t.stop()
    assert t.online_devices() == 0
    assert t.registered_devices() == 1
    assert t.device_registry.get("device-a").status == "offline"
