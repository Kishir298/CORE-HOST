"""External authentication-provider enforcement tests (Objective 2).

Deterministic test-only credentials. 127.0.0.1 sockets only.
"""

import socket
import struct
import time

import pytest

from core.application import CoreApplication
from core.communication import Message
from core.communication.serializer import MessageSerializer
from core.communication.tcp import TcpTransport
from core.configuration import Configuration
from core.configuration.validator import ConfigurationValidator
from core.security import SecurityManager
from core.security.models import Identity, IdentityType, Permission
from core.security.provider import (
    ExistenceAuthenticationProvider,
    TokenAuthenticationProvider,
)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _token_security(with_token=True):
    sm = SecurityManager(provider=TokenAuthenticationProvider())
    meta = {"token": "test-token-123"} if with_token else {}
    sm.register_identity(
        Identity(
            identity_id="device-a",
            name="Device A",
            identity_type=IdentityType.DEVICE,
            permissions=frozenset({Permission.READ}),
            metadata=meta,
        )
    )
    return sm


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


def _expect_close(sock):
    sock.settimeout(2.0)
    try:
        data = sock.recv(4)
    except (TimeoutError, OSError):
        return
    assert data == b"" or len(data) < 4


def _external_config(provider=None):
    data = {
        "core": {"name": "C.O.R.E.", "version": "0.3.0"},
        "network": {"enabled": True},
        "communication": {
            "transport": "tcp",
            "host": "0.0.0.0",
            "port": 0,
        },
        "security": {},
    }
    if provider is not None:
        data["security"]["provider"] = provider
    return Configuration(data=data, environment="development")


# 1. External configuration automatically selects TokenAuthenticationProvider.
def test_external_config_auto_selects_token_provider(tmp_path):
    import yaml

    cfg = tmp_path / "core.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "core": {"name": "C.O.R.E.", "version": "0.3.0"},
                "environment": "development",
                "network": {"enabled": False},
                "communication": {"enabled": True},
                "components": {},
            }
        )
    )
    app = CoreApplication(config_path=cfg, environment="development")
    app.configuration.load(str(cfg), environment="development")
    # Simulate resolved external config values.
    app.configuration.set("network.enabled", True)
    app.configuration.set("communication.transport", "tcp")
    app.configuration.set("communication.host", "0.0.0.0")
    app._apply_security_policy()
    assert isinstance(app.security.provider, TokenAuthenticationProvider)


# 2. External configuration with explicit existence-only provider fails closed.
def test_external_config_explicit_existence_fails_closed(tmp_path):
    import yaml

    cfg = tmp_path / "core.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "core": {"name": "C.O.R.E.", "version": "0.3.0"},
                "environment": "development",
                "network": {"enabled": True},
                "communication": {
                    "transport": "tcp",
                    "host": "0.0.0.0",
                    "port": 0,
                },
                "security": {"provider": "existence"},
            }
        )
    )
    app = CoreApplication(config_path=cfg, environment="development")
    # Fail-closed: validator rejects at load AND security policy raises.
    # Either layer raising satisfies the requirement.
    try:
        app.configuration.load(str(cfg), environment="development")
    except ValueError:
        return
    with pytest.raises(ValueError):
        app._apply_security_policy()


# 3. Validator rejects existence-only external config (no silent fallback).
def test_validator_rejects_existence_external():
    validator = ConfigurationValidator()
    with pytest.raises(ValueError):
        validator.validate(_external_config(provider="existence"))


# 4. External identity with no token fails authentication over the wire.
def test_external_identity_without_token_fails():
    port = _free_port()
    t = TcpTransport(
        host="127.0.0.1", port=port, security_manager=_token_security(with_token=False)
    )
    t.register("service:echo", lambda m: m.create_response(source="s", payload={}))
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _send_msg(
            s,
            Message(
                source="device-a",
                destination="core",
                message_type="CORE_HANDSHAKE",
                payload={
                    "identity_id": "device-a",
                    "credential": "anything",
                    "protocol_version": "0.3.0",
                },
                identity_id="device-a",
            ),
        )
        _expect_close(s)
        s.close()
        assert t.authentication_failures() >= 1
    finally:
        t.stop()


# 5. Incorrect token fails.
def test_external_incorrect_token_fails():
    port = _free_port()
    t = TcpTransport(
        host="127.0.0.1", port=port, security_manager=_token_security()
    )
    t.register("service:echo", lambda m: m.create_response(source="s", payload={}))
    t.start()
    time.sleep(0.2)
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        _send_msg(
            s,
            Message(
                source="device-a",
                destination="core",
                message_type="CORE_HANDSHAKE",
                payload={
                    "identity_id": "device-a",
                    "credential": "wrong-token",
                    "protocol_version": "0.3.0",
                },
                identity_id="device-a",
            ),
        )
        _expect_close(s)
        s.close()
    finally:
        t.stop()


# 6. Correct token succeeds.
def test_external_correct_token_succeeds():
    port = _free_port()
    t = TcpTransport(
        host="127.0.0.1", port=port, security_manager=_token_security()
    )
    t.register(
        "service:echo",
        lambda m: m.create_response(source="service:echo", payload={"ok": True}),
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
                source="device-a",
                destination="core",
                message_type="CORE_HANDSHAKE",
                payload={
                    "identity_id": "device-a",
                    "credential": "test-token-123",
                    "protocol_version": "0.3.0",
                },
                identity_id="device-a",
            ),
        )
        resp = _recv_msg(s)
        assert resp.message_type == "CORE_HANDSHAKE_RESPONSE"
        assert resp.payload["authenticated"] is True
        s.close()
    finally:
        t.stop()


# 7. Localhost legacy may still use existence-only authentication.
def test_localhost_existence_construction_allowed():
    sm = SecurityManager(provider=ExistenceAuthenticationProvider())
    t = TcpTransport(host="127.0.0.1", port=0, security_manager=sm)
    assert t.hardened is True  # handshake active, localhost tolerated


# 8. External transport refuses existence-only security manager at construction.
def test_external_construction_with_existence_fails():
    sm = SecurityManager(provider=ExistenceAuthenticationProvider())
    with pytest.raises(ValueError):
        TcpTransport(host="0.0.0.0", port=0, use_tls=False, security_manager=sm)


# 9. enforce_authorization=false does NOT disable external authentication.
def test_enforce_authorization_false_still_requires_auth(tmp_path):
    import yaml

    cfg = tmp_path / "core.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "core": {"name": "C.O.R.E.", "version": "0.3.0"},
                "environment": "development",
                "network": {"enabled": True},
                "communication": {
                    "transport": "tcp",
                    "host": "0.0.0.0",
                    "port": 0,
                },
                "security": {"enforce_authorization": False},
            }
        )
    )
    app = CoreApplication(config_path=cfg, environment="development")
    app.configuration.load(str(cfg), environment="development")
    # Must not raise (auto-selects token) and provider must be token-based.
    app._apply_security_policy()
    assert isinstance(app.security.provider, TokenAuthenticationProvider)
    assert app.security_policy.enforced is False
