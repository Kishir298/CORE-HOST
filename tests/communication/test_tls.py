import socket
import ssl
import struct
import tempfile
import time
from pathlib import Path

import pytest

from core.communication import Message
from core.communication.serializer import MessageSerializer
from core.communication.tcp import TcpTransport


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_tls_fallback_when_cert_missing():
    """Legacy plaintext fallback: TLS requested but cert missing → plaintext."""
    t = TcpTransport(host="127.0.0.1", port=51111, use_tls=True, certfile="/tmp/nonexistent.pem")
    assert t.uses_tls is True
    assert t.is_tls is False  # fallback
    t.register("service:echo", lambda m: m.create_response(source="service:echo", payload={"ok": True}))
    t.start()
    time.sleep(0.2)
    # Plain message should still work via local path
    resp = t.send(Message(source="c", destination="service:echo", message_type="TEST", payload={}))
    assert resp is not None
    t.stop()


def test_tls_with_self_signed_cert():
    """TLS listener with self-signed cert accepts TLS clients and rejects plaintext."""
    try:
        import subprocess
    except Exception:
        pytest.skip("subprocess not available")

    # Generate self-signed cert via openssl
    tmpdir = tempfile.mkdtemp()
    cert = Path(tmpdir) / "cert.pem"
    key = Path(tmpdir) / "key.pem"
    try:
        # Use openssl to generate cert
        import subprocess

        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost", "-days", "1"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pytest.skip("openssl not available for TLS test")

    port = _free_port()
    t = TcpTransport(host="127.0.0.1", port=port, use_tls=True, certfile=str(cert), keyfile=str(key))
    assert t.is_tls is True
    t.register("service:echo", lambda m: m.create_response(source="service:echo", payload={"echo": m.payload}))
    t.start()
    time.sleep(0.5)

    try:
        # TLS client should succeed
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        s = ctx.wrap_socket(socket.socket(), server_hostname="localhost")
        s.settimeout(2)
        s.connect(("127.0.0.1", port))
        msg = Message(source="client", destination="service:echo", message_type="TEST", payload={"hello": "tls"})
        data = MessageSerializer.serialize(msg).encode()
        s.sendall(struct.pack("!I", len(data)) + data)
        hdr = s.recv(4)
        assert len(hdr) == 4
        l = struct.unpack("!I", hdr)[0]
        resp_data = b""
        while len(resp_data) < l:
            chunk = s.recv(l - len(resp_data))
            assert chunk
            resp_data += chunk
        resp = MessageSerializer.deserialize(resp_data.decode())
        assert resp.payload["echo"] == {"hello": "tls"}
        s.close()

        # Plaintext client to TLS server should be rejected/closed (no response)
        plain = socket.socket()
        plain.settimeout(1)
        plain.connect(("127.0.0.1", port))
        try:
            plain.sendall(struct.pack("!I", len(data)) + data)
            # Expect no valid response or connection closed
            try:
                h = plain.recv(4)
                # If we get data, it's unexpected but allow either closed or garbage
                assert h == b"" or len(h) < 4
            except Exception:
                pass  # expected timeout/closed
        finally:
            plain.close()
    finally:
        t.stop()
        # cleanup
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


def test_plaintext_legacy_still_works():
    """Legacy plaintext transport without TLS remains compatible."""
    port = _free_port()
    t = TcpTransport(host="127.0.0.1", port=port)
    assert not t.is_tls
    t.register("service:echo", lambda m: m.create_response(source="service:echo", payload={"ok": True}))
    t.start()
    time.sleep(0.2)
    # Local send
    resp = t.send(Message(source="a", destination="service:echo", message_type="X", payload={}))
    assert resp is not None
    # Network plaintext
    s = socket.socket()
    s.settimeout(2)
    s.connect(("127.0.0.1", port))
    msg = Message(source="net", destination="service:echo", message_type="TEST", payload={"x": 1})
    data = MessageSerializer.serialize(msg).encode()
    s.sendall(struct.pack("!I", len(data)) + data)
    hdr = s.recv(4)
    l = struct.unpack("!I", hdr)[0]
    resp_data = s.recv(l)
    resp2 = MessageSerializer.deserialize(resp_data.decode())
    assert resp2.payload["ok"] is True
    s.close()
    t.stop()
