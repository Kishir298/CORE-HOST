"""Validation for the security PDP configuration keys (security.mode etc.)."""

from __future__ import annotations

from core.configuration import Configuration, ConfigurationValidator


def _base_config(**security: object) -> Configuration:
    data: dict = {
        "core": {"name": "C.O.R.E.", "version": "0.1"},
    }
    if security:
        data["security"] = security
    return Configuration(data=data, environment="development")


def test_security_section_absent_is_valid():
    assert ConfigurationValidator().is_valid(_base_config()) is True


def test_valid_pdp_modes():
    validator = ConfigurationValidator()
    assert validator.is_valid(_base_config(mode="embedded")) is True
    assert validator.is_valid(_base_config(mode="daemon")) is True


def test_invalid_pdp_mode_rejected():
    config = _base_config(mode="banana")
    validator = ConfigurationValidator()
    assert validator.is_valid(config) is False
    try:
        validator.validate(config)
    except ValueError as exc:
        assert "security.mode" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_non_string_pdp_mode_rejected():
    assert (
        ConfigurationValidator().is_valid(_base_config(mode=True)) is False
    )


def test_pdp_socket_must_be_string():
    validator = ConfigurationValidator()
    assert validator.is_valid(_base_config(pdp_socket="/tmp/pdp.sock")) is True
    assert validator.is_valid(_base_config(pdp_socket=1234)) is False


def test_pdp_timeout_must_be_positive_number():
    validator = ConfigurationValidator()
    assert validator.is_valid(_base_config(pdp_timeout_s=2.5)) is True
    assert validator.is_valid(_base_config(pdp_timeout_s=0)) is False
    assert validator.is_valid(_base_config(pdp_timeout_s=-1)) is False
    assert validator.is_valid(_base_config(pdp_timeout_s="fast")) is False
    assert validator.is_valid(_base_config(pdp_timeout_s=True)) is False


def test_existing_security_keys_still_valid():
    config = _base_config(
        enforce_authorization=True,
        provider="token",
        log_external_device_tokens=False,
        mode="daemon",
        pdp_socket="/run/tiviss/security.sock",
        pdp_timeout_s=5.0,
    )
    assert ConfigurationValidator().is_valid(config) is True
