"""Reconciliation + dedicated organization API tests."""

import pytest

from core.errors import ResourceNotFound
from core.organization import OrganizationEngine
from core.organization.engine import OrganizationError
from core.organization.ingestion import (
    RescsUnavailable,
    ResourceIngestor,
)
from core.rescs import InMemoryRescsAdapter
from core.resources import Resource, ResourceRegistry


def make_stack(*resources):
    adapter = InMemoryRescsAdapter()
    for resource in resources:
        adapter.persist_resource(resource)
    registry = ResourceRegistry()
    engine = OrganizationEngine(registry=registry)
    registry.attach_organization(engine)
    ingestor = ResourceIngestor(adapter, registry, engine)
    return adapter, registry, engine, ingestor


def res(resource_id, **overrides):
    base = {"resource_id": resource_id, "name": resource_id.upper(),
            "resource_type": "device"}
    base.update(overrides)
    return Resource(**base)


def test_reconcile_adds_new_resource():
    adapter, registry, engine, ingestor = make_stack(res("a"))
    result = ingestor.reconcile()
    assert result["added"] == ["a"]
    assert result["updated"] == []
    assert result["removed"] == []
    assert registry.get("a").name == "A"
    assert engine.get("resource:a").category == "device"


def test_reconcile_updates_changed_resource():
    adapter, registry, engine, ingestor = make_stack(res("a", name="Old"))
    ingestor.reconcile()
    adapter.persist_resource(res("a", name="New", owner="o",
                                 metadata={"k": 1}))
    result = ingestor.reconcile()
    assert result["updated"] == ["a"]
    assert registry.get("a").name == "New"
    assert engine.get("resource:a").metadata["owner"] == "o"
    assert engine.get("resource:a").metadata["metadata"] == {"k": 1}


def test_reconcile_removes_absent_resource():
    adapter, registry, engine, ingestor = make_stack(res("a"), res("b"))
    ingestor.reconcile()
    assert engine.count() == 2
    adapter.delete_resource("b")
    result = ingestor.reconcile()
    assert result["removed"] == ["b"]
    assert registry.count() == 1
    assert engine.by_resource("b") == []
    with pytest.raises(ResourceNotFound):
        registry.get("b")


def test_reconcile_empty_authoritative_clears_to_zero():
    adapter, registry, engine, ingestor = make_stack(res("a"), res("b"))
    ingestor.reconcile()
    adapter.delete_resource("a")
    adapter.delete_resource("b")
    result = ingestor.reconcile()
    assert result["removed"] == ["a", "b"]
    assert registry.count() == 0
    assert engine.count() == 0


class BrokenAdapter(InMemoryRescsAdapter):
    def list_resources(self):
        raise ConnectionError("down")


def test_reconcile_backend_failure_preserves_state():
    adapter, registry, engine, ingestor = make_stack(res("a"))
    ingestor.reconcile()
    ingestor._adapter = BrokenAdapter()
    with pytest.raises(RescsUnavailable):
        ingestor.reconcile()
    assert registry.count() == 1
    assert engine.get("resource:a").name == "A"


def test_reconcile_invalid_resource_reported_without_deletion():
    adapter, registry, engine, ingestor = make_stack(res("good"))
    ingestor.reconcile()
    original = adapter.list_resources

    def listing():
        return original() + [{"name": "no-id"}]

    adapter.list_resources = listing
    result = ingestor.reconcile()
    assert result["failed"] == ["<unknown>"]
    assert "<unknown>" in result["errors"]
    assert registry.count() == 1
    assert engine.count() == 1
    assert result["unchanged"] == ["good"]


def test_reconcile_idempotent_and_deterministic():
    adapter, registry, engine, ingestor = make_stack(res("b"), res("a"))
    first = ingestor.reconcile()
    assert first["added"] == ["a", "b"]
    second = ingestor.reconcile()
    assert second == {"added": [], "updated": [], "removed": [],
                      "unchanged": ["a", "b"], "failed": [],
                      "errors": {}}
    third = ingestor.reconcile()
    assert third == second
    assert registry.count() == 2
    assert engine.count() == 2


def test_organization_api_single_bulk_reconcile_lookup_forget():
    adapter, registry, engine, ingestor = make_stack(
        res("a", resource_type="sensor"), res("b", resource_type="actuator"))
    # Single ingestion through engine facade.
    engine.ingest_resource("a")
    assert engine.get("resource:a").category == "sensor"
    # Bulk ingestion through engine facade.
    engine.ingest_all()
    assert engine.count() == 2
    # Discovery.
    assert {e.resource_id for e in engine.by_category("sensor")} == {"a"}
    assert len(engine.by_resource("b")) == 1
    assert engine.resource("b").resource_id == "b"
    # Reconciliation through engine facade.
    adapter.delete_resource("b")
    result = engine.reconcile()
    assert result["removed"] == ["b"]
    # Forget/remove through engine facade.
    engine.forget_resource("a")
    assert registry.count() == 0
    assert engine.count() == 0


def test_organization_api_without_ingestor_raises():
    engine = OrganizationEngine()
    with pytest.raises(OrganizationError):
        engine.ingest_resource("x")
    with pytest.raises(OrganizationError):
        engine.ingest_all()
    with pytest.raises(OrganizationError):
        engine.reconcile()


def test_ingestor_auto_attaches_to_organization():
    adapter = InMemoryRescsAdapter()
    registry = ResourceRegistry()
    engine = OrganizationEngine(registry=registry)
    registry.attach_organization(engine)
    assert engine._ingestor is None
    ResourceIngestor(adapter, registry, engine)
    assert engine._ingestor is not None
    adapter.persist_resource(res("z"))
    engine.ingest_resource("z")
    assert engine.get("resource:z").name == "Z"
