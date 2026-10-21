"""Organization metadata preservation tests."""

from core.organization import OrganizationEngine
from core.resources import Resource


def make_resource(**overrides):
    base = {
        "resource_id": "dev-1",
        "name": "Sensor",
        "resource_type": "device",
        "owner": "rishik",
        "source": "esp32",
        "capabilities": ["temp", "wifi"],
        "metadata": {"floor": 1, "nested": {"a": [1, 2]}},
        "connection_info": {"protocol": "tcp"},
        "status": "online",
    }
    base.update(overrides)
    return Resource(**base)


def test_full_metadata_preservation():
    engine = OrganizationEngine()
    entry = engine.categorize_resource(make_resource())
    assert entry.entry_id == "resource:dev-1"
    assert entry.category == "device"
    assert entry.name == "Sensor"
    assert entry.resource_id == "dev-1"
    assert entry.metadata["resource_type"] == "device"
    assert entry.metadata["owner"] == "rishik"
    assert entry.metadata["source"] == "esp32"
    assert entry.metadata["status"] == "online"
    assert entry.metadata["capabilities"] == ["temp", "wifi"]
    assert entry.metadata["metadata"] == {"floor": 1, "nested": {"a": [1, 2]}}
    assert entry.metadata["connection_info"] == {"protocol": "tcp"}


def test_nested_metadata_preservation():
    engine = OrganizationEngine()
    entry = engine.categorize_resource(
        make_resource(metadata={"l1": {"l2": {"l3": [1, {"x": "y"}]}}})
    )
    assert entry.metadata["metadata"] == {"l1": {"l2": {"l3": [1, {"x": "y"}]}}}


def test_capabilities_preservation():
    engine = OrganizationEngine()
    entry = engine.categorize_resource(make_resource(capabilities=["a", "b"]))
    assert entry.metadata["capabilities"] == ["a", "b"]


def test_source_preservation():
    engine = OrganizationEngine()
    entry = engine.categorize_resource(make_resource(source="lan"))
    assert entry.metadata["source"] == "lan"


def test_owner_preservation():
    engine = OrganizationEngine()
    entry = engine.categorize_resource(make_resource(owner="owner-x"))
    assert entry.metadata["owner"] == "owner-x"
    assert entry.metadata["resource_type"] == "device"


def test_metadata_refresh_after_resource_update():
    engine = OrganizationEngine()
    resource = make_resource()
    engine.categorize_resource(resource)
    resource.name = "Sensor v2"
    resource.owner = "other"
    resource.source = "lan"
    resource.capabilities = ["camera"]
    resource.metadata = {"floor": 2}
    resource.connection_info = {"protocol": "http"}
    resource.status = "offline"
    entry = engine.categorize_resource(resource)
    assert entry.name == "Sensor v2"
    assert entry.metadata["owner"] == "other"
    assert entry.metadata["source"] == "lan"
    assert entry.metadata["capabilities"] == ["camera"]
    assert entry.metadata["metadata"] == {"floor": 2}
    assert entry.metadata["connection_info"] == {"protocol": "http"}
    assert entry.metadata["status"] == "offline"
    # Old values must not survive.
    assert entry.metadata["metadata"] != {"floor": 1, "nested": {"a": [1, 2]}}
    assert engine.count() == 1


def test_no_mutation_of_original_resource():
    engine = OrganizationEngine()
    resource = make_resource()
    snapshot = dict(resource.metadata)
    engine.categorize_resource(resource)
    entry = engine.get("resource:dev-1")
    entry.metadata["metadata"]["floor"] = 999
    entry.metadata["capabilities"].append("evil")
    assert resource.metadata == snapshot
    assert resource.capabilities == ["temp", "wifi"]


def test_no_shared_mutable_metadata():
    engine = OrganizationEngine()
    resource = make_resource()
    entry = engine.categorize_resource(resource)
    resource.metadata["floor"] = 999
    resource.metadata["nested"]["a"].append(3)
    resource.capabilities.append("evil")
    resource.connection_info["protocol"] = "evil"
    assert entry.metadata["metadata"] == {"floor": 1, "nested": {"a": [1, 2]}}
    assert entry.metadata["capabilities"] == ["temp", "wifi"]
    assert entry.metadata["connection_info"] == {"protocol": "tcp"}


def test_resource_type_change_updates_category_without_duplicates():
    engine = OrganizationEngine()
    engine.categorize_resource(make_resource())
    entry = engine.categorize_resource(make_resource(resource_type="agent"))
    assert entry.entry_id == "resource:dev-1"
    assert entry.category == "agent"
    assert engine.by_category("device") == []
    assert [e.resource_id for e in engine.by_category("agent")] == ["dev-1"]
    assert engine.count() == 1


def test_organize_resource_alias_is_idempotent():
    engine = OrganizationEngine()
    first = engine.organize_resource(make_resource())
    second = engine.organize_resource(make_resource())
    assert first is second
    assert engine.count() == 1
