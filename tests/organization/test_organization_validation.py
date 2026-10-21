"""Organization boundary validation tests."""

import pytest

from core.organization import OrganizationEngine, OrganizationEntry
from core.organization.engine import (
    OrganizationError,
    OrganizationValidationError,
    validate_organization_entry,
)


def valid_entry(**overrides):
    base = {
        "entry_id": "e1",
        "category": "device",
        "name": "Device One",
        "resource_id": "r1",
        "metadata": {"k": "v"},
    }
    base.update(overrides)
    return OrganizationEntry(**base)


def test_valid_entry_accepted():
    engine = OrganizationEngine()
    entry = valid_entry()
    assert engine.add(entry) is entry
    assert engine.get("e1") is entry


def test_validation_error_hierarchy():
    assert issubclass(OrganizationValidationError, OrganizationError)


@pytest.mark.parametrize("bad", [None, "", "   ", 123, ["x"], {"k": "v"}])
def test_invalid_entry_id_rejected(bad):
    engine = OrganizationEngine()
    with pytest.raises(OrganizationValidationError):
        engine.add(valid_entry(entry_id=bad))


@pytest.mark.parametrize("bad", [None, "", "   ", 42, ["c"]])
def test_invalid_category_rejected(bad):
    engine = OrganizationEngine()
    with pytest.raises(OrganizationValidationError):
        engine.add(valid_entry(category=bad))


@pytest.mark.parametrize("bad", [None, "", "   ", 42, ["n"]])
def test_invalid_name_rejected(bad):
    engine = OrganizationEngine()
    with pytest.raises(OrganizationValidationError):
        engine.add(valid_entry(name=bad))


@pytest.mark.parametrize("bad", ["", "   ", 123, ["r"], {"r": 1}])
def test_invalid_resource_id_rejected(bad):
    engine = OrganizationEngine()
    with pytest.raises(OrganizationValidationError):
        engine.add(valid_entry(resource_id=bad))


def test_none_resource_id_allowed():
    engine = OrganizationEngine()
    entry = valid_entry(resource_id=None)
    engine.add(entry)
    assert engine.get("e1").resource_id is None


@pytest.mark.parametrize("bad", ["meta", ["m"], 42])
def test_invalid_metadata_rejected(bad):
    engine = OrganizationEngine()
    with pytest.raises(OrganizationValidationError):
        engine.add(valid_entry(metadata=bad))


def test_missing_metadata_normalized():
    entry = OrganizationEntry(entry_id="e1", category="c", name="N")
    assert entry.metadata == {}
    engine = OrganizationEngine()
    engine.add(entry)
    assert engine.get("e1").metadata == {}


def test_none_metadata_normalized():
    entry = valid_entry(metadata=None)
    validate_organization_entry(entry)
    assert entry.metadata == {}


def test_non_entry_rejected():
    with pytest.raises(OrganizationValidationError):
        validate_organization_entry({"entry_id": "x"})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"category": ""},
        {"category": "   "},
        {"category": 123},
        {"name": ""},
        {"name": "  "},
        {"name": 42},
        {"resource_id": ""},
        {"resource_id": "  "},
        {"resource_id": 99},
        {"metadata": "nope"},
        {"metadata": ["x"]},
    ],
)
def test_invalid_update_values_rejected(kwargs):
    engine = OrganizationEngine()
    engine.add(valid_entry())
    with pytest.raises(OrganizationValidationError):
        engine.update("e1", **kwargs)
    # State unchanged after rejected update.
    entry = engine.get("e1")
    assert entry.category == "device"
    assert entry.name == "Device One"


def test_valid_update_values_accepted():
    engine = OrganizationEngine()
    engine.add(valid_entry())
    updated = engine.update(
        "e1",
        category="agent",
        name="Renamed",
        resource_id="r2",
        metadata={"v": 2},
    )
    assert updated.category == "agent"
    assert updated.name == "Renamed"
    assert updated.resource_id == "r2"
    assert updated.metadata == {"v": 2}


def test_categorize_rejects_malformed_resource():
    from core.resources import Resource

    engine = OrganizationEngine()
    with pytest.raises(OrganizationValidationError):
        engine.categorize_resource(
            Resource(resource_id="", name="N", resource_type="device")
        )
    with pytest.raises(OrganizationValidationError):
        engine.categorize_resource(
            Resource(resource_id="r", name="", resource_type="device")
        )
    with pytest.raises(OrganizationValidationError):
        engine.categorize_resource(
            Resource(resource_id="r", name="N", resource_type="")
        )
    assert engine.count() == 0
