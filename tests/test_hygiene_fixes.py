"""Regressions for audit fixes: validator, registry, adapter, dispatcher, CLI."""

import argparse

import pytest

from core.cli.main import execute
from core.communication import Message
from core.configuration import Configuration, ConfigurationValidator
from core.rescs.adapter import InMemoryRescsAdapter
from core.resources import Resource, ResourceRegistry
from core.runtime import Runtime
from core.services import Service, ServiceDispatcher, ServiceManager


def _config(data):
    return Configuration(data=data, environment="development")


def test_agent_auto_assign_dict_form_single_error():
    config = _config(
        {
            "core": {"name": "C.O.R.E.", "version": "0.3.0"},
            "agent": {"auto_assign": {"profile_id": 123}},
        }
    )
    with pytest.raises(ValueError) as excinfo:
        ConfigurationValidator().validate(config)
    message = str(excinfo.value)
    assert "agent.auto_assign.profile_id must be a string." in message
    # Dict form must not also raise the boolean error (was double-error).
    assert "agent.auto_assign must be a boolean." not in message


def test_agent_auto_assign_dict_form_valid():
    config = _config(
        {
            "core": {"name": "C.O.R.E.", "version": "0.3.0"},
            "agent": {"auto_assign": {"profile_id": "default"}},
        }
    )
    assert ConfigurationValidator().is_valid(config) is True


def test_agent_auto_assign_bool_form_valid():
    config = _config(
        {
            "core": {"name": "C.O.R.E.", "version": "0.3.0"},
            "agent": {"auto_assign": True},
        }
    )
    assert ConfigurationValidator().is_valid(config) is True


def test_agent_auto_assign_string_form_single_error():
    config = _config(
        {
            "core": {"name": "C.O.R.E.", "version": "0.3.0"},
            "agent": {"auto_assign": "yes"},
        }
    )
    with pytest.raises(ValueError) as excinfo:
        ConfigurationValidator().validate(config)
    assert "agent.auto_assign must be a boolean." in str(excinfo.value)
    assert "profile_id" not in str(excinfo.value)


def test_registry_discover_category_detached_returns_empty():
    registry = ResourceRegistry()
    registry.register(
        Resource(
            resource_id="d1",
            name="D1",
            resource_type="hardware",
        )
    )
    assert registry.discover(category="anything") == []
    assert registry.discover(category="sensor", owner="x") == []


def test_inmemory_adapter_persist_isolates_copies():
    adapter = InMemoryRescsAdapter()
    resource = Resource(
        resource_id="d1",
        name="D1",
        resource_type="hardware",
        metadata={"k": "v"},
    )
    adapter.persist_resource(resource)
    resource.metadata["k"] = "MUTATED"
    resource.status = "online"
    stored = adapter.fetch_resource("d1")
    assert stored is not None
    assert stored.metadata == {"k": "v"}
    assert stored.status == "offline"


def test_inmemory_adapter_fetch_isolates_copies():
    adapter = InMemoryRescsAdapter()
    adapter.persist_resource(
        Resource(
            resource_id="d1",
            name="D1",
            resource_type="hardware",
            metadata={"k": "v"},
        )
    )
    fetched = adapter.fetch_resource("d1")
    assert fetched is not None
    fetched.metadata["k"] = "MUTATED"
    fetched.status = "online"
    refetched = adapter.fetch_resource("d1")
    assert refetched is not None
    assert refetched.metadata == {"k": "v"}
    assert refetched.status == "offline"
    listed = adapter.list_resources()
    listed[0].metadata["k"] = "MUTATED"
    assert adapter.fetch_resource("d1") is not None
    assert adapter.fetch_resource("d1").metadata == {"k": "v"}


def test_dispatcher_reply_targets_requester():
    manager = ServiceManager()
    manager.register(Service(service_id="echo", name="Echo", version="0.1.0"))
    manager.start("echo")
    manager.register_handler("echo", "ping", lambda **kwargs: {"ok": True})
    dispatcher = ServiceDispatcher(manager)

    request = Message(
        source="mac-01",
        destination="service:echo",
        message_type="SERVICE_REQUEST",
        payload={"operation": "ping"},
        request_id="r-1",
    )
    reply = dispatcher.handle(request)
    assert reply.message_type == "SERVICE_RESPONSE"
    assert reply.destination == "mac-01"
    assert reply.source == "service:echo"
    assert reply.payload["success"] is True


def test_deprecated_execute_unknown_command_returns_2(capsys):
    runtime = Runtime()
    with pytest.warns(DeprecationWarning):
        rc = execute(argparse.Namespace(command="frobnicate"), runtime)
    assert rc == 2
    assert "unknown command" in capsys.readouterr().out


def test_token_fallback_warns_but_passes_by_default():
    from core.security.models import Identity, IdentityType
    from core.security.provider import TokenAuthenticationProvider

    provider = TokenAuthenticationProvider()
    identity = Identity(identity_id="mac-01", name="Mac", identity_type=IdentityType.DEVICE, metadata={})
    with pytest.warns(UserWarning, match="existence-only"):
        assert provider.authenticate(identity, None) is True


def test_token_fallback_closed_when_opted_out():
    import warnings

    from core.security.models import Identity, IdentityType
    from core.security.provider import TokenAuthenticationProvider

    provider = TokenAuthenticationProvider(allow_insecure_fallback=False)
    identity = Identity(identity_id="mac-01", name="Mac", identity_type=IdentityType.DEVICE, metadata={})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert provider.authenticate(identity, None) is False


def test_token_match_still_exact():
    from core.security.models import Identity, IdentityType
    from core.security.provider import TokenAuthenticationProvider

    provider = TokenAuthenticationProvider(allow_insecure_fallback=False)
    identity = Identity(identity_id="mac-01", name="Mac", identity_type=IdentityType.DEVICE, metadata={"token": "s3cr3t"})
    assert provider.authenticate(identity, "s3cr3t") is True
    assert provider.authenticate(identity, "wrong") is False
    assert provider.authenticate(identity, None) is False


def test_loopback_transport_not_treated_as_external(tmp_path):
    from core.application import CoreApplication

    config = (
        "core:\n  name: C.O.R.E.\n  version: 0.3.0\nenvironment: development\n"
        "network:\n  enabled: true\n"
        "communication:\n  transport: loopback\n  host: 0.0.0.0\n  port: 0\n"
        "security:\n  provider: existence\n"
    )
    path = tmp_path / "core.yaml"
    path.write_text(config, encoding="utf-8")
    app = CoreApplication(config_path=str(path))
    try:
        app.start()
        assert app.configuration.get("communication.host") == "127.0.0.1"
    finally:
        app.stop()


def test_loopback_with_existence_provider_validates():
    config = _config(
        {
            "core": {"name": "C.O.R.E.", "version": "0.3.0"},
            "network": {"enabled": True},
            "communication": {"transport": "loopback", "host": "0.0.0.0"},
            "security": {"provider": "existence"},
        }
    )
    assert ConfigurationValidator().is_valid(config) is True


def test_tcp_with_existence_provider_still_rejected():
    config = _config(
        {
            "core": {"name": "C.O.R.E.", "version": "0.3.0"},
            "network": {"enabled": True},
            "communication": {"transport": "tcp", "host": "0.0.0.0"},
            "security": {"provider": "existence"},
        }
    )
    assert ConfigurationValidator().is_valid(config) is False


def test_empty_device_type_and_platform_rejected():
    from core.communication.protocol import (
        DEVICE_REGISTRATION_FAILED,
        validate_registration_payload,
    )

    base = {
        "device_id": "mac-01",
        "device_name": "MacBook",
        "device_type": "phone",
        "platform": "mac",
        "capabilities": [],
        "protocol_version": "0.3.0",
    }
    assert validate_registration_payload(dict(base)) == (None, None)
    bad_type = dict(base, device_type="   ")
    assert validate_registration_payload(bad_type)[0] == DEVICE_REGISTRATION_FAILED
    bad_platform = dict(base, platform="")
    assert validate_registration_payload(bad_platform)[0] == DEVICE_REGISTRATION_FAILED
    bad_kind = dict(base, device_type=123)
    assert validate_registration_payload(bad_kind)[0] == DEVICE_REGISTRATION_FAILED
