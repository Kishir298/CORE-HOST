"""Persistent device registration tests (localhost only).

Verifies registered device identities survive disconnections AND
C.O.R.E. process restarts via R.E.S.C.S. persistence, and that
reconnect restores exactly one online record with a new connection.
"""

import socket
import struct
import time

import pytest
import yaml

from core.application import CoreApplication
from core.communication import Message
from core.communication.devices import DeviceRecord, DeviceRegistry
from core.communication.protocol import (
    DEVICE_DISCOVER,
    DEVICE_DISCOVER_RESPONSE,
    DEVICE_ERROR,
    DEVICE_REGISTER,
    DEVICE_REGISTER_RESPONSE,
)
from core.communication.serializer import MessageSerializer
from core.communication.tcp import TcpTransport
from core.rescs import FileRescsAdapter, InMemoryRescsAdapter
from core.security import SecurityManager
from core.security.models import Identity, IdentityType, Permission
from core.security.provider import TokenAuthenticationProvider

CREDENTIALS = {"device-a": "secret-a", "device-b": "secret-b", "device-c": "secret-c"}


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
                permissions=frozenset({Permission.READ, Permission.WRITE}),
                metadata={"token": CREDENTIALS[device_id]},
            )
        )
    return sm


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


def _register(sock, device_id):
    msg = Message(
        source=device_id,
        destination="core",
        message_type=DEVICE_REGISTER,
        payload={
            "device_id": device_id,
            "device_name": f"Device {device_id[-1].upper()}",
            "device_type": "generic",
            "platform": "test",
            "capabilities": ["sense"],
            "protocol_version": "0.3.0",
        },
        identity_id=device_id,
    )
    _send_msg(sock, msg)
    return _recv_msg(sock), msg


def _connect_registered(port, device_id):
    s = socket.socket()
    s.settimeout(3)
    s.connect(("127.0.0.1", port))
    _handshake(s, device_id)
    resp, _ = _register(s, device_id)
    assert resp.message_type == DEVICE_REGISTER_RESPONSE
    return s


def _expect_close(sock):
    sock.settimeout(2.0)
    try:
        data = sock.recv(4)
    except (socket.timeout, OSError):
        return
    assert data == b"" or len(data) < 4


def _wait_for(condition, timeout=5.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return condition()


# -- serialization ---------------------------------------------------------------


def test_record_dict_round_trip():
    registry = DeviceRegistry()
    record = registry.register(
        device_id="device-a",
        device_name="Device A",
        device_type="generic",
        platform="test",
        capabilities=["sense"],
        protocol_version="0.3.0",
        identity_id="device-a",
        connection_id="conn-1",
    )
    clone = DeviceRecord.from_dict(record.to_dict())
    assert clone == record
    assert clone.registered_at == record.registered_at
    assert clone.last_seen == record.last_seen


def test_from_dict_rejects_invalid():
    with pytest.raises(ValueError):
        DeviceRecord.from_dict({"device_name": "No ID"})
    with pytest.raises(ValueError):
        DeviceRecord.from_dict({"device_id": "x", "device_name": ""})
    with pytest.raises(ValueError):
        DeviceRecord.from_dict("not-a-dict")


def test_restore_forces_offline_no_connection():
    registry = DeviceRegistry()
    record = registry.register(
        device_id="device-a",
        device_name="Device A",
        identity_id="device-a",
        connection_id="conn-1",
    )
    snapshot = record.to_dict()
    assert snapshot["status"] == "online"
    fresh = DeviceRegistry()
    restored = fresh.restore(snapshot)
    assert restored.status == "offline"
    assert restored.connection_id is None
    assert restored.device_id == "device-a"
    assert restored.registered_at == record.registered_at


def test_restore_all_skips_corrupt():
    registry = DeviceRegistry()
    record = registry.register(device_id="device-a", device_name="Device A")
    count = DeviceRegistry().restore_all(
        [record.to_dict(), {"device_name": "bad"}, "garbage", None]
    )
    assert count == 1


# -- Test 1: persist + fresh restore ------------------------------------------------


def test_1_persist_restore_single_device():
    store = InMemoryRescsAdapter()
    registry = DeviceRegistry(device_store=store)
    record = registry.register(
        device_id="device-a",
        device_name="Device A",
        device_type="generic",
        platform="test",
        capabilities=["sense"],
        protocol_version="0.3.0",
        identity_id="device-a",
        connection_id="conn-1",
    )
    registry.persist_identity("device-a", token="secret-a", permissions=["read"])
    registry.mark_offline("device-a", "conn-1")

    fresh = DeviceRegistry(device_store=store)
    restored_count = fresh.restore_all(store.list_devices())
    assert restored_count == 1
    restored = fresh.get("device-a")
    assert restored.device_id == "device-a"
    assert restored.identity_id == "device-a"
    assert restored.device_name == "Device A"
    assert restored.capabilities == ["sense"]
    assert restored.registered_at == record.registered_at
    assert restored.status == "offline"
    assert restored.connection_id is None


# -- Test 2: three devices restart -----------------------------------------------------


def test_2_three_devices_restart_offline():
    store = InMemoryRescsAdapter()
    registry = DeviceRegistry(device_store=store)
    for device_id in ("device-a", "device-b", "device-c"):
        registry.register(
            device_id=device_id,
            device_name=device_id,
            identity_id=device_id,
            connection_id=f"conn-{device_id}",
        )
        registry.persist_identity(device_id, token=CREDENTIALS[device_id])
    assert registry.registered_count() == 3
    assert registry.online_count() == 3

    restarted = DeviceRegistry(device_store=store)
    restarted.restore_all(store.list_devices())
    assert restarted.registered_count() == 3
    assert restarted.online_count() == 0
    assert restarted.offline_count() == 3


# -- Test 3 + 4: restore + reconnect, repeatedly ------------------------------------------


def test_3_restore_reconnect_single_record():
    store = InMemoryRescsAdapter()
    registry = DeviceRegistry(device_store=store)
    registry.register(device_id="device-a", device_name="Device A",
                      identity_id="device-a", connection_id="c1")
    registry.persist_identity("device-a", token="secret-a")
    registry.mark_offline("device-a", "c1")

    restarted = DeviceRegistry(device_store=store)
    restarted.restore_all(store.list_devices())
    record = restarted.register(
        device_id="device-a", device_name="Device A",
        identity_id="device-a", connection_id="c2",
    )
    assert record.status == "online"
    assert record.connection_id == "c2"
    assert restarted.registered_count() == 1
    assert restarted.online_count() == 1
    assert restarted.offline_count() == 0


def test_4_repeated_reconnects_single_record():
    registry = DeviceRegistry()
    connection_ids = set()
    for i in range(5):
        if i > 0:
            registry.mark_offline("device-a", f"conn-{i - 1}")
        record = registry.register(
            device_id="device-a", device_name="Device A",
            identity_id="device-a", connection_id=f"conn-{i}",
        )
        connection_ids.add(record.connection_id)
        assert registry.registered_count() == 1
    assert len(connection_ids) == 5


# -- Test 5: stale close after reconnect -----------------------------------------------------


def test_5_stale_close_keeps_new_connection():
    store = InMemoryRescsAdapter()
    port = _free_port()
    t = TcpTransport(
        host="127.0.0.1", port=port,
        security_manager=_make_security(["device-a"]),
    )
    t.device_registry.set_device_store(store)
    t.start()
    time.sleep(0.2)
    try:
        old = _connect_registered(port, "device-a")
        old_cid = t.device_registry.get("device-a").connection_id
        old.close()
        new = _connect_registered(port, "device-a")
        new_cid = t.device_registry.get("device-a").connection_id
        assert new_cid != old_cid
        time.sleep(0.5)  # delayed old-connection cleanup runs here
        record = t.device_registry.get("device-a")
        assert record.status == "online"
        assert record.connection_id == new_cid
        assert t.device_registry.registered_count() == 1
        # Identity persisted with credentials for a future restart.
        stored = store.fetch_device("device-a")
        assert stored["token"] == "secret-a"
        new.close()
    finally:
        t.stop()


# -- Test 6 + 7: app shutdown/restart + discovery -----------------------------------------------


def _write_app_config(path, rescs_path):
    path.write_text(
        yaml.safe_dump(
            {
                "core": {"name": "C.O.R.E.", "version": "0.3.0"},
                "environment": "development",
                "network": {"enabled": False},
                "communication": {"enabled": True},
                "rescs": {"adapter": "file", "path": str(rescs_path)},
                "components": {},
            }
        )
    )


def test_6_app_restart_preserves_identities():
    import tempfile
    from pathlib import Path

    tmpdir = Path(tempfile.mkdtemp())
    cfg = tmpdir / "core.yaml"
    rescs_path = tmpdir / "rescs.json"
    _write_app_config(cfg, rescs_path)

    app = CoreApplication(config_path=cfg, environment="development")
    app.start()
    try:
        assert isinstance(app.rescs, FileRescsAdapter)
        for device_id in ("device-a", "device-b"):
            app.security.register_identity(
                Identity(
                    identity_id=device_id,
                    name=device_id,
                    identity_type=IdentityType.DEVICE,
                    permissions=frozenset({Permission.READ}),
                    metadata={"token": CREDENTIALS[device_id]},
                )
            )
            app.device_registry.register(
                device_id=device_id, device_name=device_id,
                identity_id=device_id, connection_id=f"conn-{device_id}",
            )
            app.device_registry.persist_identity(
                device_id, token=CREDENTIALS[device_id], permissions=["read"]
            )
        assert app.device_registry.online_count() == 2
    finally:
        app.stop()

    app2 = CoreApplication(config_path=cfg, environment="development")
    app2.start()
    try:
        # Records survive; all offline; identities re-provisioned.
        assert app2.device_registry.registered_count() == 2
        assert app2.device_registry.online_count() == 0
        assert app2.device_registry.offline_count() == 2
        identity = app2.security.get_identity("device-a")
        assert identity.metadata["token"] == "secret-a"
        # Resource mirror restored for discovery consistency.
        assert app2.resources.get("device-a").resource_type == "device"
    finally:
        app2.stop()


def test_7_discovery_after_restart_shows_offline():
    port = _free_port()
    store = InMemoryRescsAdapter()
    registry = DeviceRegistry(device_store=store)
    registry.register(device_id="device-a", device_name="Device A",
                      identity_id="device-a", connection_id="c1")
    registry.persist_identity("device-a", token="secret-a")
    registry.mark_offline("device-a", "c1")

    # Fresh transport restores the persisted identity (all offline).
    t = TcpTransport(
        host="127.0.0.1", port=port, security_manager=_make_security(["device-a"])
    )
    t.device_registry.set_device_store(store)
    t.device_registry.restore_all(store.list_devices())
    t.start()
    time.sleep(0.2)
    try:
        s = _connect_registered(port, "device-a")
        # Register a second device, then drop it to have an offline entry.
        t.device_registry.register(
            device_id="device-b", device_name="Device B",
            identity_id="device-b", connection_id="tmp",
        )
        t.device_registry.mark_offline("device-b", "tmp")
        _send_msg(
            s,
            Message(
                source="device-a", destination="core",
                message_type=DEVICE_DISCOVER, payload={}, identity_id="device-a",
            ),
        )
        resp = _recv_msg(s)
        assert resp.message_type == DEVICE_DISCOVER_RESPONSE
        by_id = {d["device_id"]: d for d in resp.payload["devices"]}
        assert by_id["device-a"]["status"] == "online"
        assert by_id["device-b"]["status"] == "offline"
        s.close()
    finally:
        t.stop()


# -- Test 8: invalid credentials ------------------------------------------------------


def test_8_invalid_credentials_rejected_for_known_device():
    store = InMemoryRescsAdapter()
    port = _free_port()
    t = TcpTransport(
        host="127.0.0.1", port=port, security_manager=_make_security(["device-a"])
    )
    t.device_registry.set_device_store(store)
    # Known persisted identity, currently offline.
    t.device_registry.restore(
        {
            "device_id": "device-a", "device_name": "Device A",
            "device_type": "generic", "platform": "test",
            "capabilities": [], "protocol_version": "0.3.0",
            "identity_id": "device-a",
        }
    )
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _send_msg(
            s,
            Message(
                source="device-a", destination="core",
                message_type="CORE_HANDSHAKE",
                payload={
                    "identity_id": "device-a",
                    "credential": "wrong-secret",
                    "protocol_version": "0.3.0",
                },
                identity_id="device-a",
            ),
        )
        _expect_close(s)
        s.close()
        # Attacker gained nothing: still offline, single record.
        assert t.device_registry.get("device-a").status == "offline"
        assert t.device_registry.registered_count() == 1
        assert t.authentication_failures() >= 1
    finally:
        t.stop()


# -- Test 9: cross-identity device_id claim ----------------------------------------------


def test_9_cross_identity_claim_rejected():
    store = InMemoryRescsAdapter()
    port = _free_port()
    sm = _make_security(["device-a", "device-b"])
    t = TcpTransport(host="127.0.0.1", port=port, security_manager=sm)
    t.device_registry.set_device_store(store)
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _handshake(s, "device-a")
        # Authenticated as device-a, attempts to claim device-b's id.
        _send_msg(
            s,
            Message(
                source="device-a", destination="core",
                message_type=DEVICE_REGISTER,
                payload={
                    "device_id": "device-b", "device_name": "Device B",
                    "device_type": "generic", "platform": "test",
                    "capabilities": [], "protocol_version": "0.3.0",
                },
                identity_id="device-a",
            ),
        )
        resp = _recv_msg(s)
        assert resp.message_type == DEVICE_ERROR
        _expect_close(s)
        s.close()
        assert not t.device_registry.has("device-b")
    finally:
        t.stop()


# -- reconnect over TCP after restore --------------------------------------------------------


def test_tcp_reconnect_after_restore_single_online_record():
    store = InMemoryRescsAdapter()
    registry = DeviceRegistry(device_store=store)
    registry.register(device_id="device-a", device_name="Device A",
                      identity_id="device-a", connection_id="old")
    registry.persist_identity("device-a", token="secret-a", permissions=["read", "write"])
    registry.mark_offline("device-a", "old")

    # "Restart": fresh registry + re-provisioned identity, same store.
    fresh_registry = DeviceRegistry(device_store=store)
    assert fresh_registry.restore_all(store.list_devices()) == 1
    sm = SecurityManager(provider=TokenAuthenticationProvider())
    stored = store.fetch_device("device-a")
    sm.register_identity(
        Identity(
            identity_id="device-a", name="Device A",
            identity_type=IdentityType.DEVICE,
            permissions=frozenset(
                Permission(p) for p in stored.get("permissions", ["read"])
            ),
            metadata={"token": stored["token"]},
        )
    )
    port = _free_port()
    t = TcpTransport(host="127.0.0.1", port=port, security_manager=sm)
    # Swap in the restored registry (same object the app would share).
    t._devices = fresh_registry
    t.start()
    time.sleep(0.2)
    try:
        s = _connect_registered(port, "device-a")
        assert t.device_registry.registered_count() == 1
        assert t.device_registry.online_count() == 1
        assert t.device_registry.offline_count() == 0
        record = t.device_registry.get("device-a")
        assert record.connection_id is not None
        s.close()
        assert _wait_for(lambda: t.device_registry.online_count() == 0)
        assert t.device_registry.registered_count() == 1
    finally:
        t.stop()
