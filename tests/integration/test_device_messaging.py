"""Multi-device localhost simulation for C.O.R.E. device communication.

Each simulated device owns an independent TCP socket to the same C.O.R.E.
server on 127.0.0.1. Tests exercise the full path::

    socket -> framing -> TCP transport -> handshake -> authentication
    -> registration -> routing -> destination socket

No physical devices. No internet.
"""

import socket
import struct
import threading
import time
import uuid

import pytest

from core.communication import Message
from core.communication.protocol import (
    DEVICE_DISCOVER,
    DEVICE_DISCOVER_RESPONSE,
    DEVICE_ERROR,
    DEVICE_REGISTER,
    DEVICE_REGISTER_RESPONSE,
    DEVICE_UNAVAILABLE,
)
from core.communication.serializer import MessageSerializer
from core.communication.tcp import TcpTransport
from core.errors import RoutingError
from core.events import EventBus
from core.routing import Router
from core.security import SecurityManager
from core.security.models import Identity, IdentityType, Permission
from core.security.provider import TokenAuthenticationProvider

DEVICE_IDS = ("device-a", "device-b", "device-c")
CREDENTIALS = {"device-a": "secret-a", "device-b": "secret-b", "device-c": "secret-c"}
NAMES = {"device-a": "Device A", "device-b": "Device B", "device-c": "Device C"}


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _make_security():
    sm = SecurityManager(provider=TokenAuthenticationProvider())
    for device_id in DEVICE_IDS:
        sm.register_identity(
            Identity(
                identity_id=device_id,
                name=NAMES[device_id],
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


def _recv_msg(sock, timeout=5.0):
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


class DeviceClient:
    """One simulated device: its own socket, identity and registration."""

    def __init__(self, device_id):
        self.device_id = device_id
        self.sock = socket.socket()
        self.sock.settimeout(5)

    def connect(self, port):
        self.sock.connect(("127.0.0.1", port))

    def handshake(self):
        _send_msg(
            self.sock,
            Message(
                source=self.device_id,
                destination="core",
                message_type="CORE_HANDSHAKE",
                payload={
                    "identity_id": self.device_id,
                    "credential": CREDENTIALS[self.device_id],
                    "protocol_version": "0.3.0",
                },
                identity_id=self.device_id,
            ),
        )
        resp = _recv_msg(self.sock)
        assert resp.message_type == "CORE_HANDSHAKE_RESPONSE"
        assert resp.payload["authenticated"] is True
        return resp

    def register(self):
        msg = Message(
            source=self.device_id,
            destination="core",
            message_type=DEVICE_REGISTER,
            payload={
                "device_id": self.device_id,
                "device_name": NAMES[self.device_id],
                "device_type": "generic",
                "platform": "test",
                "capabilities": [],
                "protocol_version": "0.3.0",
            },
            identity_id=self.device_id,
        )
        _send_msg(self.sock, msg)
        resp = _recv_msg(self.sock)
        assert resp.message_type == DEVICE_REGISTER_RESPONSE
        assert resp.payload["registered"] is True
        assert resp.payload["device_id"] == self.device_id
        assert resp.payload["status"] == "online"
        assert resp.payload["join_name"] == (
            NAMES[self.device_id].replace(" ", "-") + "-" + self.device_id
        )
        assert resp.payload["lease_duration_seconds"] == 24 * 60 * 60
        assert resp.payload["connected_at"]
        assert resp.payload["lease_expires_at"]
        assert resp.request_id == msg.message_id
        return resp

    def send_to(self, destination, mtype="TEST_MESSAGE", payload=None,
                message_id=None, request_id=None):
        msg = Message(
            source=self.device_id,
            destination=destination,
            message_type=mtype,
            payload=payload or {},
            message_id=message_id or str(uuid.uuid4()),
            request_id=request_id,
            identity_id=self.device_id,
        )
        _send_msg(self.sock, msg)
        return msg

    def recv(self, timeout=5.0):
        return _recv_msg(self.sock, timeout=timeout)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def _start_server(event_bus=None):
    port = _free_port()
    t = TcpTransport(
        host="127.0.0.1",
        port=port,
        security_manager=_make_security(),
        event_bus=event_bus,
    )
    t.start()
    time.sleep(0.2)
    return t, port


def _wait_for(condition, timeout=5.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return condition()


# -- primary acceptance sequence (spec section 29) ----------------------------


def test_primary_device_a_b_lifecycle():
    t, port = _start_server()
    try:
        # 2-3. Authenticated clients.
        a = DeviceClient("device-a")
        b = DeviceClient("device-b")
        a.connect(port)
        b.connect(port)
        # 4-5. Handshakes.
        a.handshake()
        b.handshake()
        # 6-7. Registrations.
        a.register()
        b.register()
        # 8. Both online.
        assert t.registered_devices() == 2
        assert t.online_devices() == 2
        # 9-11. A sends an application message to B.
        out = a.send_to(
            "device-b",
            payload={"text": "hello-b"},
            message_id="mid-a-b-1",
            request_id="req-a-b-1",
        )
        # 12. B receives it.
        got = b.recv()
        # 13-17. Identity preserved end to end.
        assert got.message_id == "mid-a-b-1"
        assert got.request_id == "req-a-b-1"
        assert got.source == "device-a"
        assert got.destination == "device-b"
        assert got.identity_id == "device-a"
        assert got.payload == {"text": "hello-b"}
        # 18-19. B responds; A receives.
        back = b.send_to(
            "device-a",
            payload={"text": "hello-a"},
            message_id="mid-b-a-1",
            request_id=got.message_id,
        )
        got_back = a.recv()
        # 20. Request/response correlation.
        assert got_back.message_id == "mid-b-a-1"
        assert got_back.request_id == "mid-a-b-1"
        assert got_back.source == "device-b"
        assert got_back.destination == "device-a"
        assert got_back.identity_id == "device-b"
        # 21-22. Disconnect B; B becomes offline, record preserved.
        b_cid = t.device_registry.get("device-b").connection_id
        assert b_cid is not None
        b.close()
        assert _wait_for(lambda: t.online_devices() == 1)
        assert t.device_registry.get("device-b").status == "offline"
        assert t.device_registry.get("device-b").connection_id is None
        assert t.registered_devices() == 2
        # 23-24. A messages offline B -> DEVICE_UNAVAILABLE, id preserved.
        before = t.device_routing_failures()
        probe = a.send_to("device-b", payload={"text": "are-you-there"})
        err = a.recv()
        assert err.message_type == DEVICE_ERROR
        assert err.payload["error"] == DEVICE_UNAVAILABLE
        assert err.payload["request_id"] == (probe.request_id or probe.message_id)
        assert err.request_id == probe.message_id
        assert t.device_routing_failures() > before
        # 25-27. Reconnect B with a new socket + new connection.
        b2 = DeviceClient("device-b")
        b2.connect(port)
        b2.handshake()
        b2.register()
        # 28. B online again with a NEW connection id, one record.
        assert _wait_for(lambda: t.online_devices() == 2)
        assert t.registered_devices() == 2
        assert t.device_registry.get("device-b").status == "online"
        assert t.device_registry.get("device-b").connection_id != b_cid
        # 29-30. Traffic resumes.
        out2 = a.send_to("device-b", payload={"text": "welcome-back"})
        got2 = b2.recv()
        assert got2.message_id == out2.message_id
        assert got2.source == "device-a"
        assert _wait_for(lambda: t.device_messages_routed() >= 3)
        a.close()
        b2.close()
        # 31. Shutdown marks everything offline, records preserved.
    finally:
        t.stop()
    assert t.online_devices() == 0
    assert t.registered_devices() == 2


# -- multi-device ---------------------------------------------------------------


def test_abc_simultaneous_registration_and_ring():
    t, port = _start_server()
    clients = {}
    try:
        for device_id in DEVICE_IDS:
            c = DeviceClient(device_id)
            c.connect(port)
            c.handshake()
            c.register()
            clients[device_id] = c
        assert t.online_devices() == 3
        # A -> B, B -> C, C -> A ring.
        clients["device-a"].send_to("device-b", payload={"for": "b"})
        clients["device-b"].send_to("device-c", payload={"for": "c"})
        clients["device-c"].send_to("device-a", payload={"for": "a"})
        got_b = clients["device-b"].recv()
        got_c = clients["device-c"].recv()
        got_a = clients["device-a"].recv()
        assert (got_b.source, got_b.destination, got_b.payload) == (
            "device-a", "device-b", {"for": "b"})
        assert (got_c.source, got_c.destination, got_c.payload) == (
            "device-b", "device-c", {"for": "c"})
        assert (got_a.source, got_a.destination, got_a.payload) == (
            "device-c", "device-a", {"for": "a"})
        assert got_b.identity_id == "device-a"
        assert got_c.identity_id == "device-b"
        assert got_a.identity_id == "device-c"
    finally:
        for c in clients.values():
            c.close()
        t.stop()


def test_simultaneous_bidirectional_messages():
    t, port = _start_server()
    try:
        a = DeviceClient("device-a")
        b = DeviceClient("device-b")
        a.connect(port)
        b.connect(port)
        a.handshake()
        b.handshake()
        a.register()
        b.register()
        results = {}

        def send_a():
            results["a"] = a.send_to("device-b", payload={"from": "a"})

        def send_b():
            results["b"] = b.send_to("device-a", payload={"from": "b"})

        th1 = threading.Thread(target=send_a)
        th2 = threading.Thread(target=send_b)
        th1.start()
        th2.start()
        th1.join(timeout=10)
        th2.join(timeout=10)
        got_b = b.recv()
        got_a = a.recv()
        assert got_b.payload == {"from": "a"}
        assert got_a.payload == {"from": "b"}
        assert got_b.message_id == results["a"].message_id
        assert got_a.message_id == results["b"].message_id
        a.close()
        b.close()
    finally:
        t.stop()


def test_concurrent_registration_distinct_devices():
    t, port = _start_server()
    barrier = threading.Barrier(3)
    outcomes = {}
    clients = {}
    lock = threading.Lock()
    try:

        def worker(device_id):
            c = DeviceClient(device_id)
            try:
                c.connect(port)
                c.handshake()
                barrier.wait(timeout=10)
                c.register()
                with lock:
                    outcomes[device_id] = "ok"
                    clients[device_id] = c
            except Exception as exc:  # pragma: no cover - diagnostic
                with lock:
                    outcomes[device_id] = f"FAIL:{exc}"

        threads = [
            threading.Thread(target=worker, args=(d,)) for d in DEVICE_IDS
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=20)
        assert outcomes == {d: "ok" for d in DEVICE_IDS}
        assert t.online_devices() == 3
    finally:
        for c in clients.values():
            c.close()
        t.stop()


def test_concurrent_disconnect_reconnect():
    t, port = _start_server()
    try:
        a = DeviceClient("device-a")
        b = DeviceClient("device-b")
        a.connect(port)
        b.connect(port)
        a.handshake()
        b.handshake()
        a.register()
        b.register()
        assert t.online_devices() == 2

        def drop(client):
            client.close()

        ths = [threading.Thread(target=drop, args=(c,)) for c in (a, b)]
        for th in ths:
            th.start()
        for th in ths:
            th.join(timeout=10)
        assert _wait_for(lambda: t.online_devices() == 0)
        assert t.registered_devices() == 2

        a2 = DeviceClient("device-a")
        b2 = DeviceClient("device-b")
        a2.connect(port)
        b2.connect(port)
        a2.handshake()
        b2.handshake()
        a2.register()
        b2.register()
        assert _wait_for(lambda: t.online_devices() == 2)
        assert t.registered_devices() == 2
        # Communication resumes for both.
        a2.send_to("device-b", payload={"ping": 1})
        assert b2.recv().payload == {"ping": 1}
        b2.send_to("device-a", payload={"pong": 1})
        assert a2.recv().payload == {"pong": 1}
        a2.close()
        b2.close()
    finally:
        t.stop()


def test_stale_close_after_reconnect_keeps_new_binding():
    t, port = _start_server()
    try:
        old = DeviceClient("device-a")
        old.connect(port)
        old.handshake()
        old.register()
        old_cid = t.device_registry.get("device-a").connection_id
        old.close()
        # Reconnect immediately; the server-side cleanup of the old socket
        # may still be in flight.
        new = DeviceClient("device-a")
        new.connect(port)
        new.handshake()
        new.register()
        new_cid = t.device_registry.get("device-a").connection_id
        assert new_cid is not None and new_cid != old_cid
        time.sleep(0.5)  # let any delayed old-connection cleanup run
        record = t.device_registry.get("device-a")
        assert record.status == "online"
        assert record.connection_id == new_cid
        new.close()
    finally:
        t.stop()


# -- router / events / metrics integration ---------------------------------------


def test_router_device_routing_coexists_with_static_routes():
    from core.communication import LocalTransport

    transport = LocalTransport()
    router = Router(transport)
    router.add_route("ALERT", "service:alerts")
    assert router.has_route("ALERT")
    assert router.get_route("ALERT") == "service:alerts"

    from core.communication.devices import DeviceRegistry

    registry = DeviceRegistry()
    registry.register(
        device_id="device-a",
        device_name="Device A",
        device_type="generic",
        platform="test",
        capabilities=[],
        protocol_version="0.3.0",
        identity_id="device-a",
        connection_id="conn-1",
    )
    router.set_device_registry(registry)
    assert router.has_device("device-a") is True
    assert router.has_device("device-ghost") is False

    delivered = {}

    class _FakeDeviceTransport(LocalTransport):
        def deliver_to_device(self, message):
            delivered["msg"] = message
            return None

    router.set_transport(_FakeDeviceTransport())
    msg = Message(
        source="device-b",
        destination="device-a",
        message_type="APP_DATA",
        payload={"x": 1},
        message_id="m-1",
        request_id="r-1",
        identity_id="device-b",
    )
    router.route_to_device(msg)
    assert delivered["msg"].message_id == "m-1"
    assert delivered["msg"].request_id == "r-1"
    assert delivered["msg"].identity_id == "device-b"
    assert router.device_routed_count() == 1

    with pytest.raises(RoutingError):
        router.route_to_device(
            Message(
                source="device-b",
                destination="device-ghost",
                message_type="APP_DATA",
                payload={},
                identity_id="device-b",
            )
        )
    with pytest.raises(RoutingError):
        router.route_to_device(
            Message(
                source="device-b",
                destination="",
                message_type="APP_DATA",
                payload={},
                identity_id="device-b",
            )
        )
    # Static routing still works.
    assert router.has_route("ALERT")


def test_device_lifecycle_events_emitted():
    bus = EventBus()
    seen = []
    bus.subscribe("DEVICE_CONNECTED", lambda e: seen.append(("connected", e.payload)))
    bus.subscribe("DEVICE_DISCONNECTED", lambda e: seen.append(("gone", e.payload)))
    t, port = _start_server(event_bus=bus)
    try:
        a = DeviceClient("device-a")
        a.connect(port)
        a.handshake()
        a.register()
        assert _wait_for(lambda: any(k == "connected" for k, _ in seen))
        assert seen[0][1]["device_id"] == "device-a"
        a.close()
        assert _wait_for(lambda: any(k == "gone" for k, _ in seen))
    finally:
        t.stop()


def test_device_metrics_snapshot():
    t, port = _start_server()
    try:
        a = DeviceClient("device-a")
        b = DeviceClient("device-b")
        a.connect(port)
        b.connect(port)
        a.handshake()
        b.handshake()
        a.register()
        b.register()
        a.send_to("device-b", payload={"n": 1})
        b.recv()
        assert _wait_for(lambda: t.device_messages_routed() >= 1)
        metrics = t.device_metrics()
        assert metrics["registered_devices"] == 2
        assert metrics["online_devices"] == 2
        assert metrics["offline_devices"] == 0
        assert metrics["device_messages_routed"] >= 1
        # Existing TCP metrics intact.
        assert t.active_connections() == 2
        assert t.total_connections() >= 2
        assert t.protocol_failures() == 0
        a.close()
        b.close()
    finally:
        t.stop()


def test_discovery_lists_online_and_offline():
    t, port = _start_server()
    try:
        a = DeviceClient("device-a")
        b = DeviceClient("device-b")
        a.connect(port)
        b.connect(port)
        a.handshake()
        b.handshake()
        a.register()
        b.register()
        b.close()
        assert _wait_for(lambda: t.offline_devices() == 1)
        _send_msg(
            a.sock,
            Message(
                source="device-a",
                destination="core",
                message_type=DEVICE_DISCOVER,
                payload={},
                identity_id="device-a",
            ),
        )
        resp = a.recv()
        assert resp.message_type == DEVICE_DISCOVER_RESPONSE
        by_id = {d["device_id"]: d for d in resp.payload["devices"]}
        assert by_id["device-a"]["status"] == "online"
        assert by_id["device-b"]["status"] == "offline"
        a.close()
    finally:
        t.stop()
