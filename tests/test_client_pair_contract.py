"""Exercise both real repositories' wire code using an in-memory transport.

Extract CORE-HOST and CORE-CLIENT beside one another to enable these checks.
This does not test the OS listener, TLS negotiation, or the firewall.
"""
import json
from pathlib import Path
import struct
import sys
from unittest.mock import patch

import pytest

client_repo = Path(__file__).resolve().parents[2] / "CORE-CLIENT"
if not (client_repo / "client").is_dir():
    pytest.skip("CORE-CLIENT sibling checkout required", allow_module_level=True)
sys.path.insert(0, str(client_repo))

from client.core_device_client import CoreDeviceClient, DeviceClientError
from core.communication.connection import ConnectionSession
from core.communication.serializer import MessageSerializer
from core.communication.tcp import TcpTransport, HANDSHAKE_TYPE
from core.security import Identity, IdentityType, Permission, SecurityManager
from core.security.context import authenticated_transport
from core.security.provider import TokenAuthenticationProvider


class InMemoryConnection:
    def __init__(self, transport):
        self.transport = transport
        self.session = ConnectionSession()
        self.reply = bytearray()
        self.timeout = 5.0
        self.closed = False

    def gettimeout(self): return self.timeout
    def settimeout(self, value): self.timeout = value
    def shutdown(self, how): pass
    def close(self):
        self.transport._cleanup_device_binding(self.session)
        self.session.clear_session_token()
        self.closed = True

    def recv(self, size):
        if not self.reply: return b""
        # Force fragmentation across reads to check framing on both sides.
        size = min(size, 7)
        out = bytes(self.reply[:size])
        del self.reply[:size]
        return out

    def sendall(self, data):
        if self.closed: raise ConnectionResetError()
        length = struct.unpack("!I", data[:4])[0]
        assert length == len(data) - 4
        message = MessageSerializer.deserialize(data[4:].decode())
        if message.message_type == HANDSHAKE_TYPE:
            assert self.transport._process_handshake(self, self.session, message)
            return
        self.transport._validate_message_shape(message)
        self.transport._enforce_identity(message, self.session)
        message.payload.pop("_session_token", None)
        disposition = self.transport._dispatch_device_message(self, self.session, message)
        if disposition is None:
            with authenticated_transport(self.session.identity_id):
                response = self.transport.send(message)
            if response is not None:
                self.transport._send_frame(self, response)


@pytest.fixture()
def pair():
    security = SecurityManager(TokenAuthenticationProvider())
    security.register_identity(Identity("alice", "alice", IdentityType.DEVICE,
                                        frozenset({Permission.READ}), {"token": "secret"}))
    transport = TcpTransport(security_manager=security)
    connection = InMemoryConnection(transport)
    # Server replies are byte buffers, while client sendall dispatches to host.
    original = transport._send_frame
    def capture(sock, message):
        payload = MessageSerializer.serialize(message).encode()
        sock.reply.extend(struct.pack("!I", len(payload)) + payload)
    transport._send_frame = capture
    c = CoreDeviceClient("127.0.0.1", 5000, "alice", use_tls=False)
    c.login("secret")
    with patch("client.core_device_client.socket.create_connection", return_value=connection):
        c.connect()
        c.register()
    try:
        yield c, transport, connection
    finally:
        c.shutdown()
        transport._send_frame = original


def test_pair_handshake_registration_discovery(pair):
    c, host, connection = pair
    assert c.session_token == connection.session.session_token
    result = c.discover()
    assert result["payload"]["devices"][0]["device_id"] == "alice"
    assert host.online_devices() == 1
    assert "secret" not in json.dumps(result)


def test_pair_host_error_preserves_live_session(pair):
    c, host, connection = pair
    with pytest.raises(DeviceClientError, match="DEVICE_NOT_FOUND"):
        c.device_info("missing")
    assert c.discover()["payload"]["devices"][0]["status"] == "online"


def test_pair_logout_marks_host_offline(pair):
    c, host, connection = pair
    c.shutdown()
    assert host.offline_devices() == 1
    assert c.session_token is None and connection.session.session_token is None
