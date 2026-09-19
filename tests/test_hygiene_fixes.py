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
