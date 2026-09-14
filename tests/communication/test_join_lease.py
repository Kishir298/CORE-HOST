"""Device join names, token logging, and 24-hour connection leases.

Live localhost sockets for protocol paths; injected fake clocks for lease
boundary timing (no real 24h waits). Deterministic test-only credentials.
"""

import socket
import struct
import time

import pytest

from core.communication import Message
from core.communication.connection import ConnectionSession
from core.communication.devices import DeviceRegistry, derive_join_name
from core.communication.protocol import validate_registration_payload
from core.communication.serializer import MessageSerializer
from core.communication.tcp import CONNECTION_LEASE_SECONDS, TcpTransport
from core.configuration import Configuration
from core.configuration.validator import ConfigurationValidator
from core.rescs.adapter import InMemoryRescsAdapter
from core.security import SecurityManager
from core.security.models import Identity, IdentityType, Permission
from core.security.provider import TokenAuthenticationProvider

TOKEN = "secret-a"
LEASE = 24 * 60 * 60


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
            metadata={"token": TOKEN},
        )
    )
    return sm


class _StubLogger:
    def __init__(self):
        self.infos = []
        self.warnings = []
        self.errors = []

    def debug(self, message):
        pass

    def info(self, message):
        self.infos.append(str(message))

    def warning(self, message):
        self.warnings.append(str(message))

    def error(self, message):
        self.errors.append(str(message))

    def critical(self, message):
        self.errors.append(str(message))


def _make_transport(port, sm=None, **kwargs):
    params = {"host": "127.0.0.1", "port": port,
              "security_manager": sm or _make_security()}
    params.update(kwargs)
    t = TcpTransport(**params)
    t.register(
        "service:echo",
        lambda m: m.create_response(source="service:echo", payload={"echo": m.payload}),
    )
    return t


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


def _expect_close(sock, timeout=3.0):
    sock.settimeout(timeout)
    try:
        data = sock.recv(4)
    except (socket.timeout, OSError):
        return
    assert data == b"" or len(data) < 4


def _handshake(sock, device_id="device-a", credential=TOKEN, join_name=None,
               protocol_version="0.3.0"):
    payload = {
        "identity_id": device_id,
        "credential": credential,
        "protocol_version": protocol_version,
    }
    if join_name is not None:
        payload["join_name"] = join_name
    _send_msg(
        sock,
        Message(
            source=device_id,
            destination="core",
            message_type="CORE_HANDSHAKE",
            payload=payload,
            identity_id=device_id,
        ),
    )
    return _recv_msg(sock)


def _register(sock, device_id="device-a", name="Device A", join_name=None,
              **overrides):
    payload = {
        "device_id": device_id,
        "device_name": name,
        "device_type": "generic",
        "platform": "test",
        "capabilities": [],
        "protocol_version": "0.3.0",
    }
    if join_name is not None:
        payload["join_name"] = join_name
    payload.update(overrides)
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
    return _recv_msg(sock)


def _wait_status(transport, device_id, status, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            record = transport.device_registry.get(device_id)
        except KeyError:
            record = None
        if record is not None and record.status == status:
            return record
        time.sleep(0.05)
    raise AssertionError(f"{device_id} never became {status}")


# ---- join_name derivation + validation ----


def test_derive_join_name_format():
    assert derive_join_name("MacBook", "mac-01") == "MacBook-mac-01"


def test_join_name_optional_in_registration_payload():
    code, _ = validate_registration_payload({
        "device_id": "d", "device_name": "D", "device_type": "g",
        "platform": "p", "capabilities": [], "protocol_version": "0.3.0",
    })
    assert code is None
    code, _ = validate_registration_payload({
        "device_id": "d", "device_name": "D", "device_type": "g",
        "platform": "p", "capabilities": [], "protocol_version": "0.3.0",
        "join_name": "D-d",
    })
    assert code is None


def test_join_name_invalid_values_rejected():
    base = {
        "device_id": "d", "device_name": "D", "device_type": "g",
        "platform": "p", "capabilities": [], "protocol_version": "0.3.0",
    }
    for bad in ("", "   ", 123, ["x"], "y" * 65):
        payload = dict(base, join_name=bad)
        code, _ = validate_registration_payload(payload)
        assert code is not None


# ---- join_name over the wire: persist, restore, reconnect ----


def test_register_with_join_name_echoed_and_stored():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        hs = _handshake(s, join_name="Device-A-device-a")
        assert hs.payload["authenticated"] is True
        assert hs.payload["lease_duration_seconds"] == LEASE
        assert hs.payload["connected_at"]
        assert hs.payload["lease_expires_at"]
        resp = _register(s, join_name="Device-A-device-a")
        assert resp.message_type == "DEVICE_REGISTER_RESPONSE"
        assert resp.payload["join_name"] == "Device-A-device-a"
        record = t.device_registry.get("device-a")
        assert record.join_name == "Device-A-device-a"
        s.close()
    finally:
        t.stop()


def test_register_without_join_name_derives_fallback():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s)
        resp = _register(s, name="Device A")
        assert resp.payload["join_name"] == "Device-A-device-a"
        assert t.device_registry.get("device-a").join_name == "Device-A-device-a"
        s.close()
    finally:
        t.stop()


def test_join_name_survives_reconnect_and_persistence(tmp_path):
    store = InMemoryRescsAdapter()
    registry = DeviceRegistry(device_store=store)
    record = registry.register_payload(
        {"device_id": "device-a", "device_name": "Device A",
         "device_type": "generic", "platform": "test", "capabilities": [],
         "protocol_version": "0.3.0", "join_name": "Device-A-device-a"},
        identity_id="device-a",
        connection_id="conn-1",
    )
    assert record.join_name == "Device-A-device-a"
    stored = store.fetch_device("device-a")
    assert stored["join_name"] == "Device-A-device-a"
    assert "connection_id" not in stored
    # Restart: fresh registry restores offline with the join name intact.
    revived = DeviceRegistry(device_store=store)
    assert revived.restore_all(store.list_devices()) == 1
    restored = revived.get("device-a")
    assert restored.status == "offline"
    assert restored.connection_id is None
    assert restored.join_name == "Device-A-device-a"
    # Reconnect without a join_name keeps the established one.
    revived.mark_offline("device-a", None)
    again = revived.register_payload(
        {"device_id": "device-a", "device_name": "Device A",
         "device_type": "generic", "platform": "test", "capabilities": [],
         "protocol_version": "0.3.0"},
        identity_id="device-a",
        connection_id="conn-2",
    )
    assert again.join_name == "Device-A-device-a"
    assert again.connection_id == "conn-2"


def test_join_name_does_not_override_identity_binding():
    port = _free_port()
    t = _make_transport(port)
    t.start()
    time.sleep(0.2)
    try:
        # Authenticated as device-a but registering another device_id fails,
        # even when reusing a foreign join_name.
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s, device_id="device-a", credential=TOKEN)
        _send_msg(
            s,
            Message(
                source="device-a",
                destination="core",
                message_type="DEVICE_REGISTER",
                payload={"device_id": "device-b", "device_name": "B",
                         "device_type": "generic", "platform": "test",
                         "capabilities": [], "protocol_version": "0.3.0",
                         "join_name": "Device-A-device-a"},
                identity_id="device-a",
            ),
        )
        resp = _recv_msg(s)
        assert resp.message_type == "DEVICE_ERROR"
        s.close()
    finally:
        t.stop()


# ---- token logging (opt-in, redacted by default) ----


def test_token_logging_disabled_redacts_by_default():
    logger = _StubLogger()
    port = _free_port()
    t = _make_transport(port, logger=logger)
    assert t._log_external_tokens is False
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s, join_name="Device-A-device-a")
        s.close()
        assert any("login attempt" in line for line in logger.infos)
        assert any("authenticated" in line for line in logger.infos)
        assert all(TOKEN not in line for line in logger.infos + logger.warnings)
        assert any("<REDACTED>" in line for line in logger.infos)
    finally:
        t.stop()


def test_token_logging_enabled_shows_token_on_attempt_and_failure():
    logger = _StubLogger()
    port = _free_port()
    t = _make_transport(port, logger=logger, log_external_tokens=True)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        try:
            _handshake(s, credential="wrong-token", join_name="Device-A-device-a")
        except ConnectionError:
            pass  # failed authentication terminates the connection
        s.close()
        assert any("wrong-token" in line for line in logger.infos + logger.warnings)
        assert any("authentication failed" in line for line in logger.warnings)
    finally:
        t.stop()


def test_token_logging_enabled_success_omits_token_shows_connection():
    logger = _StubLogger()
    port = _free_port()
    t = _make_transport(port, logger=logger, log_external_tokens=True)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        hs = _handshake(s, join_name="Device-A-device-a")
        assert hs.payload["authenticated"] is True
        conn_id = hs.payload["connection_id"]
        s.close()
        success = [line for line in logger.infos if "authenticated" in line]
        assert success and conn_id in success[0]
        assert all(TOKEN not in line for line in success)
    finally:
        t.stop()


def test_failed_attempt_does_not_persist_foreign_credential():
    store = InMemoryRescsAdapter()
    registry = DeviceRegistry(device_store=store)
    registry.register_payload(
        {"device_id": "device-a", "device_name": "Device A",
         "device_type": "generic", "platform": "test", "capabilities": [],
         "protocol_version": "0.3.0", "join_name": "Device-A-device-a"},
        identity_id="device-a",
        connection_id="conn-1",
    )
    registry.persist_identity("device-a", token=TOKEN, permissions=["read"])
    logger = _StubLogger()
    port = _free_port()
    t = TcpTransport(host="127.0.0.1", port=port, security_manager=_make_security(),
                     device_registry=registry, logger=logger)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        try:
            hs = _handshake(s, credential="wrong-token")
            assert hs.payload["authenticated"] is False
        except ConnectionError:
            pass  # failed authentication terminates the connection
        s.close()
        assert store.fetch_device("device-a")["token"] == TOKEN
        assert all("wrong-token" not in line for line in logger.infos + logger.warnings)
    finally:
        t.stop()


# ---- configuration surface ----


def test_lease_constant_is_24h_single_default():
    assert CONNECTION_LEASE_SECONDS == 24 * 60 * 60
    t = TcpTransport(host="127.0.0.1", port=0)
    assert t._lease_seconds == CONNECTION_LEASE_SECONDS


def test_lease_seconds_configurable_invalid_falls_back():
    assert TcpTransport(host="127.0.0.1", port=0,
                        connection_lease_seconds=60)._lease_seconds == 60
    for bad in (0, -5, "soon", None):
        t = TcpTransport(host="127.0.0.1", port=0, connection_lease_seconds=bad)
        assert t._lease_seconds == CONNECTION_LEASE_SECONDS


def test_validator_accepts_new_keys_rejects_bad_types():
    validator = ConfigurationValidator()
    good = Configuration(data={
        "core": {"name": "C.O.R.E.", "version": "0.3.0"},
        "security": {"log_external_device_tokens": True},
        "communication": {"connection_lease_seconds": 3600},
    }, environment="development")
    assert validator.validate(good) is True
    bad_flag = Configuration(data={
        "core": {"name": "C.O.R.E.", "version": "0.3.0"},
        "security": {"log_external_device_tokens": "yes"},
    }, environment="development")
    with pytest.raises(ValueError):
        validator.validate(bad_flag)
    bad_lease = Configuration(data={
        "core": {"name": "C.O.R.E.", "version": "0.3.0"},
        "communication": {"connection_lease_seconds": -1},
    }, environment="development")
    with pytest.raises(ValueError):
        validator.validate(bad_lease)


def test_configuration_dot_paths_for_new_keys():
    config = Configuration(data={
        "core": {"name": "C.O.R.E.", "version": "0.3.0"},
        "security": {"log_external_device_tokens": True},
        "communication": {"connection_lease_seconds": 3600},
    }, environment="development")
    assert config.get("security.log_external_device_tokens") is True
    assert config.get("communication.connection_lease_seconds") == 3600


# ---- 24h lease: session-level boundary (fake clock, exact) ----


def test_lease_boundary_23_59_59_active_24_00_00_expired():
    session = ConnectionSession(remote_address="x")
    session.mark_authenticated("device-a", 1000.0)
    session.start_lease(LEASE, 1000.0)
    assert session.is_lease_expired(1000.0 + LEASE - 1) is False
    assert session.lease_remaining(1000.0 + LEASE - 1) == 1
    assert session.is_lease_expired(1000.0 + LEASE) is True


def test_no_lease_no_expiry_legacy_sessions_unaffected():
    session = ConnectionSession(remote_address="x")
    assert session.is_lease_expired(10 ** 12) is False
    assert session.lease_remaining(10 ** 12) is None


def test_wait_readable_refuses_expired_lease_fake_clock():
    now = [5000.0]
    t = TcpTransport(host="127.0.0.1", port=0, time_func=lambda: now[0])
    session = ConnectionSession(remote_address="x", connected_at=now[0],
                                last_activity=now[0])
    session.mark_authenticated("device-a", now[0])
    session.start_lease(60.0, now[0])
    a, b = socket.socketpair()
    try:
        b.settimeout(0.2)
        now[0] += 59.0
        session.touch(now[0])  # recent activity: idle is fine, lease alive
        a.sendall(b"ping")
        assert t._wait_readable(b, session) is True
        assert t._lease_expirations == 0
        now[0] += 1.0  # T+60: lease expired
        assert t._wait_readable(b, session) is False
        assert t._lease_expirations >= 1
    finally:
        a.close()
        b.close()


# ---- 24h lease: live enforcement, stale safety, reconnect ----


def test_live_lease_expiry_closes_connection_and_marks_offline():
    now = [8000.0]
    port = _free_port()
    t = _make_transport(port, time_func=lambda: now[0],
                        connection_lease_seconds=5)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        hs = _handshake(s, join_name="Device-A-device-a")
        assert hs.payload["authenticated"] is True
        assert hs.payload["lease_duration_seconds"] == 5
        resp = _register(s, join_name="Device-A-device-a")
        assert resp.payload["status"] == "online"
        assert resp.payload["join_name"] == "Device-A-device-a"
        now[0] += 6.0  # past the 5s lease: host tears the connection down
        _expect_close(s, timeout=5.0)
        record = _wait_status(t, "device-a", "offline")
        assert record.connection_id is None
        assert record.join_name == "Device-A-device-a"
    finally:
        t.stop()


def test_stale_expired_connection_cannot_offline_new_session():
    port = _free_port()
    t = _make_transport(port, connection_lease_seconds=3600)
    t.start()
    time.sleep(0.2)
    try:
        first = socket.socket()
        first.settimeout(3)
        first.connect(("127.0.0.1", port))
        hs1 = _handshake(first, join_name="Device-A-device-a")
        assert hs1.payload["authenticated"] is True
        assert _register(first).payload["status"] == "online"
        old_session = t._connections[hs1.payload["connection_id"]]
        old_id = hs1.payload["connection_id"]
        first.close()
        _wait_status(t, "device-a", "offline")
        second = socket.socket()
        second.settimeout(3)
        second.connect(("127.0.0.1", port))
        hs2 = _handshake(second, join_name="Device-A-device-a")
        assert _register(second).payload["status"] == "online"
        assert hs2.payload["connection_id"] != old_id
        # The stale session's cleanup must not touch the live binding.
        t._cleanup_device_binding(old_session)
        live = t.device_registry.get("device-a")
        assert live.status == "online"
        assert live.connection_id == hs2.payload["connection_id"]
        second.close()
    finally:
        t.stop()


def test_reconnect_after_expiry_gets_new_lease_same_identity():
    now = [9000.0]
    port = _free_port()
    t = _make_transport(port, time_func=lambda: now[0],
                        connection_lease_seconds=5)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        hs1 = _handshake(s, join_name="Device-A-device-a")
        _register(s, join_name="Device-A-device-a")
        first_id = hs1.payload["connection_id"]
        first_expiry = hs1.payload["lease_expires_at"]
        now[0] += 6.0
        _expect_close(s, timeout=5.0)
        _wait_status(t, "device-a", "offline")
        s.close()
        again = socket.socket()
        again.settimeout(3)
        again.connect(("127.0.0.1", port))
        hs2 = _handshake(again, join_name="Device-A-device-a")
        assert hs2.payload["authenticated"] is True
        assert hs2.payload["connection_id"] != first_id
        assert hs2.payload["lease_expires_at"] != first_expiry
        resp = _register(again, join_name="Device-A-device-a")
        assert resp.payload["status"] == "online"
        record = t.device_registry.get("device-a")
        assert record.device_id == "device-a"
        assert record.identity_id == "device-a"
        assert record.join_name == "Device-A-device-a"
        again.close()
    finally:
        t.stop()
