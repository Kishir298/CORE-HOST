from datetime import datetime, timezone

import pytest

from core.errors import (
    ResourceAlreadyRegistered,
    ResourceNotFound,
)
from core.resources import Resource, ResourceRegistry


def create_resource(
    resource_id: str = "test-device",
) -> Resource:
    return Resource(
        resource_id=resource_id,
        name="Test Device",
        resource_type="hardware",
        capabilities=["sensor", "communication"],
        metadata={"location": "test"},
        connection_info={"protocol": "local"},
    )


def test_resource_defaults():
    resource = create_resource()

    assert resource.resource_id == "test-device"
    assert resource.name == "Test Device"
    assert resource.resource_type == "hardware"
    assert resource.status == "offline"
    assert resource.capabilities == ["sensor", "communication"]
    assert resource.last_seen is None
    assert isinstance(resource.registered_at, datetime)
    assert resource.registered_at.tzinfo == timezone.utc


def test_resource_mark_seen():
    resource = create_resource()

    resource.mark_seen()

    assert resource.status == "online"
    assert resource.last_seen is not None
    assert resource.last_seen.tzinfo == timezone.utc


def test_register_resource():
    registry = ResourceRegistry()
    resource = create_resource()

    registered = registry.register(resource)

    assert registered is resource
    assert registry.get("test-device") is resource
    assert registry.count() == 1


def test_register_duplicate_resource():
    registry = ResourceRegistry()
    resource = create_resource()

    registry.register(resource)

    with pytest.raises(ResourceAlreadyRegistered):
        registry.register(resource)


def test_get_missing_resource():
    registry = ResourceRegistry()

    with pytest.raises(ResourceNotFound):
        registry.get("missing")


def test_unregister_resource():
    registry = ResourceRegistry()
    resource = create_resource()

    registry.register(resource)
    removed = registry.unregister("test-device")

    assert removed is resource
    assert registry.count() == 0

    with pytest.raises(ResourceNotFound):
        registry.get("test-device")


def test_update_resource():
    registry = ResourceRegistry()
    registry.register(create_resource())

    updated = registry.update(
        "test-device",
        name="Updated Device",
        status="online",
        capabilities=["camera"],
        metadata={"location": "lab"},
        connection_info={"protocol": "http"},
    )

    assert updated.name == "Updated Device"
    assert updated.status == "online"
    assert updated.capabilities == ["camera"]
    assert updated.metadata == {"location": "lab"}
    assert updated.connection_info == {"protocol": "http"}


def test_update_partial_resource():
    registry = ResourceRegistry()
    registry.register(create_resource())

    registry.update(
        "test-device",
        status="online",
    )

    resource = registry.get("test-device")

    assert resource.status == "online"
    assert resource.name == "Test Device"
    assert resource.capabilities == ["sensor", "communication"]


def test_list_resources():
    registry = ResourceRegistry()

    registry.register(create_resource("device-1"))
    registry.register(create_resource("device-2"))

    resources = registry.list()

    assert len(resources) == 2
    assert resources[0].resource_id == "device-1"
    assert resources[1].resource_id == "device-2"


def test_clear_registry():
    registry = ResourceRegistry()

    registry.register(create_resource("device-1"))
    registry.register(create_resource("device-2"))

    registry.clear()

    assert registry.count() == 0
    assert registry.list() == []


def test_registry_iteration():
    registry = ResourceRegistry()

    registry.register(create_resource("device-1"))
    registry.register(create_resource("device-2"))

    ids = [resource.resource_id for resource in registry]

    assert ids == ["device-1", "device-2"]
