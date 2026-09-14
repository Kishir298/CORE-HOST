"""End-to-end data distribution tests (localhost only).

Full path per request::

    socket -> C.O.R.E. TCP -> authentication -> device registration
    -> DATA_REQUEST -> data organization -> R.E.S.C.S. HTTP fixture
    -> normalization -> DATA_RESPONSE -> C.O.R.E. routing
    -> destination socket

Devices: device-a / device-b / device-c on 127.0.0.1, protocol 0.3.0.
The R.E.S.C.S. fixture is pre-seeded deterministically (see
tests/data/rescs_http_fixture.py); no real installation is used.
"""

import socket
import struct
import threading
import time
import uuid

import pytest

from core.communication import Message
from core.communication.protocol import (
    DATA_ERROR,
    DATA_REQUEST,
    DATA_RESPONSE,
)
from core.communication.serializer import MessageSerializer
from core.communication.tcp import TcpTransport
from core.data.organizer import DataOrganizer
from core.data.rescs_reader import HttpDataReader
from core.events import EventBus
from core.security import SecurityManager
from core.security.models import Identity, IdentityType, Permission
from core.security.provider import TokenAuthenticationProvider

from tests.data.rescs_http_fixture import RescsFixture

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
    """One simulated device: own socket, identity, registration."""

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
        return resp

    def register(self):
        _send_msg(
            self.sock,
            Message(
                source=self.device_id,
                destination="core",
                message_type="DEVICE_REGISTER",
                payload={
                    "device_id": self.device_id,
                    "device_name": NAMES[self.device_id],
                    "device_type": "generic",
                    "platform": "test",
                    "capabilities": [],
                    "protocol_version": "0.3.0",
                },
                identity_id=self.device_id,
            ),
        )
        resp = _recv_msg(self.sock)
        assert resp.message_type == "DEVICE_REGISTER_RESPONSE"
        return resp

    def data_request(self, payload, request_id=None):
        request_id = request_id or str(uuid.uuid4())
        msg = Message(
            source=self.device_id,
            destination="core",
            message_type=DATA_REQUEST,
            payload=payload,
            message_id=str(uuid.uuid4()),
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


def _start_stack(event_bus=None):
    """Start fixture + C.O.R.E. TCP server wired to it. Returns (t, port, fx)."""
    fx = RescsFixture().start()
    endpoint = fx.url  # capture eagerly: config holds a URL string, not a live object
    organizer = DataOrganizer(
        reader_factory=lambda scope, cross=False: HttpDataReader(
            endpoint=endpoint, owner_scope=scope, allow_cross_owner=cross, timeout=2.0
        ),
        security_manager=_make_security(),
        event_bus=event_bus,
    )
    port = _free_port()
    t = TcpTransport(
        host="127.0.0.1",
        port=port,
        security_manager=organizer._security,
        device_registry=None,
        event_bus=event_bus,
        data_organizer=organizer,
    )
    # Share the organizer's registry/security with the transport path by
    # pointing the organizer at the transport's authoritative registry.
    organizer._devices = t.device_registry
    t.start()
    time.sleep(0.2)
    return t, port, fx, organizer


def _stop_stack(t, fx):
    try:
        t.stop()
    finally:
        fx.stop()


def _wait_for(condition, timeout=5.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return condition()


# -- primary 40-step end-to-end ---------------------------------------------------


def test_primary_data_search_self_and_distribution():
    bus = EventBus()
    t, port, fx, organizer = _start_stack(event_bus=bus)
    try:
        # 3-5. Device A online.
        a = DeviceClient("device-a")
        a.connect(port)
        a.handshake()
        a.register()
        # 6-8. Device B online.
        b = DeviceClient("device-b")
        b.connect(port)
        b.handshake()
        b.register()
        # 9. Deterministic fixture records are present.
        assert ("core.memory", "flight-plan") in fx.records
        # 10-11. A sends a record search.
        out = a.data_request(
            {
                "request_type": "record_search",
                "namespace": "core.memory",
                "query": "pilot",
                "limit": 20,
                "offset": 0,
            },
            request_id="data-req-1",
        )
        # 12-22. C.O.R.E. authenticates, validates, authorizes, retrieves,
        # normalizes, orders, paginates, packages, size-checks, routes.
        resp = a.recv()
        # 23. A receives the response.
        assert resp.message_type == DATA_RESPONSE
        # 24. request_id preserved (payload) and message correlation.
        assert resp.payload["data_type"] == "records"
        assert resp.request_id == out.message_id
        # 25. Destination correct.
        assert resp.source == "core"
        assert resp.destination == "device-a"
        assert resp.identity_id == "device-a"
        # 26-27. Contents correct, deterministic order, no foreign owners.
        keys = [item["key"] for item in resp.payload["items"]]
        assert keys == ["flight-plan", "pilot-notes"]
        assert resp.payload["total"] == 2
        assert {item["owner"] for item in resp.payload["items"]} == {"device-a"}
        assert all(set(item) >= {"id", "namespace", "key", "value", "metadata",
                                 "owner", "version", "etag", "created_at", "updated_at"}
                   for item in resp.payload["items"])
        # 28-30. A requests data for B; C.O.R.E. retrieves + routes to B.
        out2 = a.data_request(
            {
                "request_type": "record_search",
                "namespace": "core.memory",
                "query": "pilot",
                "limit": 20,
                "offset": 0,
                "destination_device_id": "device-b",
            },
            request_id="data-req-2",
        )
        # 31-32. B receives it (A receives nothing further).
        got_b = b.recv()
        assert got_b.message_type == DATA_RESPONSE
        assert got_b.destination == "device-b"
        assert got_b.payload["data_type"] == "records"
        assert {item["owner"] for item in got_b.payload["items"]} == {"device-a"}
        assert [item["key"] for item in got_b.payload["items"]] == ["flight-plan", "pilot-notes"]
        # 33. Disconnect B.
        b_cid = t.device_registry.get("device-b").connection_id
        b.close()
        assert _wait_for(lambda: t.online_devices() == 1)
        # 34-35. A requests for offline B -> DESTINATION_UNAVAILABLE to A.
        a.data_request(
            {
                "request_type": "record_get",
                "namespace": "core.memory",
                "key": "flight-plan",
                "destination_device_id": "device-b",
            },
            request_id="data-req-3",
        )
        err = a.recv()
        assert err.message_type == DATA_ERROR
        assert err.payload["error"] == "DESTINATION_UNAVAILABLE"
        assert err.payload["request_id"] == "data-req-3"
        assert err.destination == "device-a"
        # 36-37. Reconnect + register B (new connection, one record).
        b2 = DeviceClient("device-b")
        b2.connect(port)
        b2.handshake()
        b2.register()
        assert _wait_for(lambda: t.online_devices() == 2)
        assert t.registered_devices() == 2
        assert t.device_registry.get("device-b").connection_id != b_cid
        # 38-39. A requests for B again; B receives it.
        a.data_request(
            {
                "request_type": "record_get",
                "namespace": "core.memory",
                "key": "flight-plan",
                "destination_device_id": "device-b",
            },
            request_id="data-req-4",
        )
        got_b2 = b2.recv()
        assert got_b2.message_type == DATA_RESPONSE
        assert got_b2.payload["data_type"] == "record"
        assert got_b2.payload["item"]["key"] == "flight-plan"
        # Metrics + events observed end to end.
        metrics = organizer.data_metrics()
        assert metrics["data_requests"] >= 4
        assert metrics["data_requests_successful"] >= 3
        assert metrics["data_messages_sent"] >= 3
        a.close()
        b2.close()
        # 40. Clean shutdown below.
    finally:
        _stop_stack(t, fx)


# -- operation coverage over the wire -----------------------------------------------


def test_wire_record_get_list_search_metadata_download():
    t, port, fx, organizer = _start_stack()
    try:
        a = DeviceClient("device-a")
        a.connect(port)
        a.handshake()
        a.register()

        a.data_request({"request_type": "record_get", "namespace": "core.memory", "key": "shopping"},
                       request_id="w-get")
        got = a.recv()
        assert got.payload["data_type"] == "record"
        assert got.payload["item"]["value"] == {"topic": "groceries", "index": 11}

        a.data_request({"request_type": "record_list", "namespace": "core.memory", "limit": 2, "offset": 1},
                       request_id="w-list")
        got = a.recv()
        assert got.payload["data_type"] == "records"
        assert got.payload["total"] == 3
        assert [i["key"] for i in got.payload["items"]] == ["shopping", "pilot-notes"]

        a.data_request({"request_type": "record_search", "namespace": "core.memory", "query": "groceries"},
                       request_id="w-search")
        got = a.recv()
        assert got.payload["total"] == 1

        a.data_request({"request_type": "file_metadata", "file_id": "manual"},
                       request_id="w-meta")
        got = a.recv()
        assert got.payload["data_type"] == "file_metadata"
        assert got.payload["item"]["filename"] == "manual.bin"
        assert "content_base64" not in got.payload

        a.data_request({"request_type": "file_download", "file_id": "manual"},
                       request_id="w-dl")
        got = a.recv()
        assert got.payload["data_type"] == "file_content"
        import base64

        assert base64.b64decode(got.payload["content_base64"]) == b"pilot-manual-bytes"

        # Large file is not embedded.
        a.data_request({"request_type": "file_download", "file_id": "big-blob"},
                       request_id="w-big")
        got = a.recv()
        assert got.message_type == DATA_ERROR
        assert got.payload["error"] == "FILE_TRANSFER_REQUIRED"

        # Unknown type + bad pagination over the wire.
        a.data_request({"request_type": "nope"}, request_id="w-unknown")
        assert a.recv().payload["error"] == "INVALID_DATA_REQUEST"
        a.data_request({"request_type": "record_list", "limit": 999}, request_id="w-pag")
        assert a.recv().payload["error"] == "INVALID_PAGINATION"
        a.close()
    finally:
        _stop_stack(t, fx)


def test_wire_error_mapping_not_found_unavailable():
    t, port, fx, organizer = _start_stack()
    try:
        a = DeviceClient("device-a")
        a.connect(port)
        a.handshake()
        a.register()
        a.data_request({"request_type": "record_get", "namespace": "core.memory", "key": "ghost"},
                       request_id="e-404")
        err = a.recv()
        assert err.payload["error"] == "DATA_NOT_FOUND"
        assert "Traceback" not in err.payload["message"]
        a.close()
    finally:
        _stop_stack(t, fx)
    # Unavailable R.E.S.C.S. (fixture down) -> DATA_SOURCE_UNAVAILABLE.
    t2, port2, fx2, organizer2 = _start_stack()
    try:
        a = DeviceClient("device-a")
        a.connect(port2)
        a.handshake()
        a.register()
        fx2.stop()  # kill R.E.S.C.S. mid-test
        a.data_request({"request_type": "record_list", "namespace": "core.memory"},
                       request_id="e-down")
        err = a.recv(timeout=8.0)
        assert err.payload["error"] == "DATA_SOURCE_UNAVAILABLE"
        a.close()
    finally:
        _stop_stack(t2, fx2)


def test_wire_unauthorized_fixture_maps_to_denied():
    t, port, fx, organizer = _start_stack()
    try:
        a = DeviceClient("device-a")
        a.connect(port)
        a.handshake()
        a.register()
        fx.fail_mode = "unauthorized"
        a.data_request({"request_type": "record_list", "namespace": "core.memory"},
                       request_id="e-403")
        err = a.recv()
        assert err.payload["error"] == "DATA_ACCESS_DENIED"
        fx.fail_mode = None
        a.close()
    finally:
        _stop_stack(t, fx)


# -- distribution ring -----------------------------------------------------------------


def test_distribution_ring_a_b_c():
    t, port, fx, organizer = _start_stack()
    clients = {}
    try:
        for device_id in DEVICE_IDS:
            c = DeviceClient(device_id)
            c.connect(port)
            c.handshake()
            c.register()
            clients[device_id] = c
        # A -> CORE -> RESCS -> CORE -> B
        clients["device-a"].data_request(
            {"request_type": "record_get", "namespace": "core.memory",
             "key": "flight-plan", "destination_device_id": "device-b"},
            request_id="ring-1",
        )
        got = clients["device-b"].recv()
        assert got.payload["item"]["key"] == "flight-plan"
        assert got.destination == "device-b"
        # B -> CORE -> RESCS -> CORE -> C
        clients["device-b"].data_request(
            {"request_type": "record_search", "namespace": "core.memory",
             "query": "hours", "destination_device_id": "device-c"},
            request_id="ring-2",
        )
        got = clients["device-c"].recv()
        assert got.payload["total"] == 1
        assert got.payload["items"][0]["owner"] == "device-b"
        # C -> CORE -> RESCS -> CORE -> A (device-c owns no fixture records).
        clients["device-c"].data_request(
            {"request_type": "record_list", "namespace": "core.memory",
             "destination_device_id": "device-a"},
            request_id="ring-3",
        )
        got = clients["device-a"].recv()
        assert got.payload["total"] == 0
        assert got.payload["items"] == []
    finally:
        for c in clients.values():
            c.close()
        _stop_stack(t, fx)


# -- concurrency ------------------------------------------------------------------------------


def test_concurrent_data_requests_three_devices():
    t, port, fx, organizer = _start_stack()
    clients = {}
    try:
        for device_id in DEVICE_IDS:
            c = DeviceClient(device_id)
            c.connect(port)
            c.handshake()
            c.register()
            clients[device_id] = c
        barrier = threading.Barrier(12)
        results = {}
        lock = threading.Lock()
        device_locks = {device_id: threading.Lock() for device_id in DEVICE_IDS}

        def worker(device_id, index):
            client = clients[device_id]
            rid = f"conc-{device_id}-{index}"
            if index % 3 == 0:
                payload = {"request_type": "record_list", "namespace": "core.memory",
                           "limit": 10, "offset": 0}
            elif index % 3 == 1:
                payload = {"request_type": "record_search", "namespace": "core.memory",
                           "query": "pilot", "limit": 10, "offset": 0}
            else:
                payload = {"request_type": "file_metadata", "file_id": "manual"}
            barrier.wait(timeout=15)
            # One socket per device: serialize send/recv so frames never
            # interleave; responses arrive in send order per connection.
            with device_locks[device_id]:
                client.data_request(payload, request_id=rid)
                resp = client.recv(timeout=15.0)
            with lock:
                results[rid] = resp

        threads = []
        for device_id in DEVICE_IDS:
            for index in range(4):  # 12 concurrent operations
                threads.append(threading.Thread(target=worker, args=(device_id, index)))
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=30)
        assert len(results) == 12
        assert set(results) == {f"conc-{d}-{i}" for d in DEVICE_IDS for i in range(4)}
        for rid, resp in results.items():
            owner = "-".join(rid.split("-")[1:3])
            assert resp.message_type == DATA_RESPONSE, (rid, resp.payload)
            assert resp.destination == owner
            assert resp.identity_id == owner
            if resp.payload["data_type"] == "records":
                assert all(item["owner"] == owner for item in resp.payload["items"]), rid
    finally:
        for c in clients.values():
            c.close()
        _stop_stack(t, fx)


# -- offline / reconnect ----------------------------------------------------------------------------


def test_offline_destination_no_queue_no_block():
    t, port, fx, organizer = _start_stack()
    try:
        a = DeviceClient("device-a")
        a.connect(port)
        a.handshake()
        a.register()
        b = DeviceClient("device-b")
        b.connect(port)
        b.handshake()
        b.register()
        b.close()
        assert _wait_for(lambda: t.online_devices() == 1)
        a.data_request(
            {"request_type": "record_list", "namespace": "core.memory",
             "destination_device_id": "device-b"},
            request_id="off-1",
        )
        err = a.recv(timeout=5.0)
        assert err.message_type == DATA_ERROR
        assert err.payload["error"] == "DESTINATION_UNAVAILABLE"
        assert err.payload["request_id"] == "off-1"
        # Sender still usable: a direct request succeeds afterwards.
        a.data_request({"request_type": "record_list", "namespace": "core.memory"},
                       request_id="off-2")
        assert a.recv().message_type == DATA_RESPONSE
        a.close()
    finally:
        _stop_stack(t, fx)


def test_reconnect_preserves_identity_and_resumes():
    t, port, fx, organizer = _start_stack()
    try:
        b = DeviceClient("device-b")
        b.connect(port)
        b.handshake()
        b.register()
        first_cid = t.device_registry.get("device-b").connection_id
        b.close()
        assert _wait_for(lambda: t.online_devices() == 0)
        b2 = DeviceClient("device-b")
        b2.connect(port)
        b2.handshake()
        b2.register()
        assert _wait_for(lambda: t.online_devices() == 1)
        record = t.device_registry.get("device-b")
        assert record.device_id == "device-b"
        assert record.connection_id is not None and record.connection_id != first_cid
        assert t.registered_devices() == 1
        a = DeviceClient("device-a")
        a.connect(port)
        a.handshake()
        a.register()
        a.data_request(
            {"request_type": "record_get", "namespace": "core.memory",
             "key": "pilot-log", "destination_device_id": "device-b"},
            request_id="re-1",
        )
        got = a.recv()
        # device-a is not authorized for device-b's record -> denied AT
        # retrieval for explicitly foreign data? device-a requested without
        # owner override; scope is device-a so pilot-log is simply absent.
        # Retrieval failures are reported to the requester (device-a).
        assert got.message_type == DATA_ERROR
        assert got.payload["error"] == "DATA_NOT_FOUND"
        assert got.destination == "device-a"
        a.close()
        b2.close()
    finally:
        _stop_stack(t, fx)
