import pytest

from core.version import (
    LEGACY_VERSIONS,
    SUPPORTED_VERSIONS,
    SemanticVersion,
    is_legacy,
    is_supported,
    legacy_payload_adapter,
    negotiate,
    supports_auto_assign,
    supports_tls,
)
from core.version import __version__


def test_version_is_0_4_0():
    assert __version__ == "0.4.0"


def test_supported_versions_include_legacy_and_current():
    assert "0.2.1" in SUPPORTED_VERSIONS
    assert "0.3.0" in SUPPORTED_VERSIONS
    assert "0.4.0" in SUPPORTED_VERSIONS
    assert "0.2.1" in LEGACY_VERSIONS
    assert "0.3.0" not in LEGACY_VERSIONS
    assert "0.4.0" not in LEGACY_VERSIONS


def test_semantic_parse_and_compare():
    v = SemanticVersion.parse("0.3.0")
    assert str(v) == "0.3.0"
    assert SemanticVersion.parse("0.2.1") < v
    assert SemanticVersion.parse("v0.3.0") == v
    assert SemanticVersion.parse("0.3") == v
    with pytest.raises(ValueError):
        SemanticVersion.parse("bad")


def test_is_supported_and_legacy():
    assert is_supported("0.2.1")
    assert is_supported("0.3.0")
    assert is_supported("0.4.0")
    assert not is_supported("9.9.9")
    assert is_legacy("0.2.1") is True
    assert is_legacy("0.3.0") is False
    assert is_legacy("0.4.0") is False
    assert is_legacy("0.1.0") is True


def test_negotiate_preserves_legacy():
    assert negotiate(None) == "0.2.1"
    assert negotiate("") == "0.2.1"
    assert negotiate("0.2.0") == "0.2.0"
    assert negotiate("0.2.1") == "0.2.1"
    assert negotiate("0.3.0") == "0.3.0"
    assert negotiate("0.4.0") == "0.4.0"
    # Higher clamps to latest
    assert negotiate("0.9.0") == "0.4.0"
    # Invalid falls back to legacy
    assert negotiate("invalid") == "0.2.1"


def test_supports_features():
    assert supports_tls("0.2.1") is False
    assert supports_tls("0.3.0") is True
    assert supports_tls("0.4.0") is True
    assert supports_tls(None) is False
    assert supports_auto_assign("0.2.1") is False
    assert supports_auto_assign("0.3.0") is True
    assert supports_auto_assign("0.4.0") is True


def test_legacy_payload_adapter_strips_version_keys_for_legacy():
    payload = {"operation": "assign", "device_id": "d1", "api_version": "0.3.0", "client_version": "0.3.0"}
    legacy = legacy_payload_adapter(payload, target_version="0.2.1")
    assert "api_version" not in legacy
    assert legacy["operation"] == "assign"
    # For 0.3.0, preserve
    cur = legacy_payload_adapter(payload, target_version="0.3.0")
    assert cur["api_version"] == "0.3.0"


def test_runtime_version_service_negotiates():
    from core.application import CoreApplication
    from core.communication import Message

    app = CoreApplication()
    app.start()
    try:
        # Legacy client without version → 0.2.1
        legacy = app.communication.send(
            Message(source="t", destination="service:runtime", message_type="ANY", payload={"operation": "version"})
        )
        assert legacy.payload["success"] is True
        assert legacy.payload["result"]["negotiated"] == "0.2.1"
        assert legacy.payload["result"]["version"] == "0.4.0"

        # 0.3.0 client → negotiated 0.3.0
        cur = app.communication.send(
            Message(source="t", destination="service:runtime", message_type="ANY", payload={"operation": "version", "client_version": "0.3.0"})
        )
        assert cur.payload["result"]["negotiated"] == "0.3.0"
        assert cur.payload["result"]["is_legacy"] is False

        # 0.4.0 client → negotiated 0.4.0
        cur4 = app.communication.send(
            Message(source="t", destination="service:runtime", message_type="ANY", payload={"operation": "version", "client_version": "0.4.0"})
        )
        assert cur4.payload["result"]["negotiated"] == "0.4.0"
        assert cur4.payload["result"]["is_legacy"] is False

        # Legacy explicit
        old = app.communication.send(
            Message(source="t", destination="service:runtime", message_type="ANY", payload={"operation": "version", "client_version": "0.2.0"})
        )
        assert old.payload["result"]["negotiated"] == "0.2.0"
        assert old.payload["result"]["is_legacy"] is True
    finally:
        app.stop()
