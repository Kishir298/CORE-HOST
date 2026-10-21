"""Protocol-version contract: package 0.4.0 vs wire compat with 0.3.0.

Localhost only, no sockets. Locks in the 0.4.0 bump decision:
- 0.4.0 accepted (client default since CORE-CLIENT 0.4.0).
- 0.3.0 stays accepted (existing fleet + all wire fixtures).
- garbage/empty rejected; SUPPORTED_PROTOCOL_VERSION tracks package.
"""

from core.communication.protocol import (
    SUPPORTED_PROTOCOL_VERSION,
    is_supported_protocol_version,
    validate_registration_payload,
)
from core.version import __version__ as CORE_VERSION


def _register_payload(protocol_version):
    return {
        "device_id": "mac-01",
        "device_name": "MacBook",
        "device_type": "laptop",
        "platform": "macos",
        "capabilities": [],
        "protocol_version": protocol_version,
    }


def test_supported_constant_tracks_package():
    assert SUPPORTED_PROTOCOL_VERSION == CORE_VERSION == "0.4.0"


def test_current_and_previous_wire_versions_accepted():
    assert is_supported_protocol_version("0.4.0") is True
    assert is_supported_protocol_version("0.3.0") is True


def test_garbage_versions_rejected():
    assert is_supported_protocol_version("9.9.9") is False
    assert is_supported_protocol_version("") is False
    assert is_supported_protocol_version(None) is False
    assert is_supported_protocol_version("bad") is False


def test_register_accepts_0_4_0_and_0_3_0():
    assert validate_registration_payload(_register_payload("0.4.0")) == (None, None)
    assert validate_registration_payload(_register_payload("0.3.0")) == (None, None)


def test_register_rejects_unsupported_version():
    code, _ = validate_registration_payload(_register_payload("9.9.9"))
    assert code is not None
