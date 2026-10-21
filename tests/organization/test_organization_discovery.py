"""Dedicated organization API tests: discover + facade-only lifecycle.

Proves a normal caller can drive the full resource lifecycle through
``OrganizationEngine`` alone (``ingest_resource / ingest_all /
reconcile / discover / resource / forget_resource``) without touching
``ResourceRegistry``, adapters, or ``ResourceIngestor`` internals.
"""

import pytest

from core.errors import ResourceNotFound
from core.organization import OrganizationEngine
from core.organization.ingestion import (
    RescsResourceNotFound,
    RescsUnavailable,
    ResourceIngestor,
)
from core.rescs import InMemoryRescsAdapter
from core.resources import Resource, ResourceRegistry


def make_stack(*resources):
    """Build adapter/registry/engine/ingestor; return (adapter, engine)."""
    adapter = InMemoryRescsAdapter()
    for resource in resources:
        adapter.persist_resource(resource)
    registry = ResourceRegistry()
    engine = OrganizationEngine(registry=registry)
    registry.attach_organization(engine)
    ResourceIngestor(adapter, registry, engine)
    return adapter, engine


def res(resource_id, **overrides):
    base = {
        "resource_id": resource_id,
        "name": resource_id.upper(),
        "resource_type": "device",
    }
    base.update(overrides)
    return Resource(**base)


def test_discover_all_categories_and_resources():
    _, engine = make_stack(
        res("a", resource_type="sensor"),
        res("b", resource_type="actuator"),
    )
    engine.ingest_all()
    assert {e.resource_id for e in engine.discover()} == {"a", "b"}
    assert {e.resource_id for e in engine.discover(category="sensor")} == {"a"}
    assert len(engine.discover(resource_id="b")) == 1
    assert engine.discover(category="sensor", resource_id="a")[0].entry_id == (
        "resource:a"
    )
    assert engine.discover(category="actuator", resource_id="a") == []
    assert engine.discover(category="nope") == []
    assert engine.discover(resource_id="ghost") == []


def test_discover_returns_safe_snapshot():
    _, engine = make_stack(res("a", resource_type="sensor"))
    engine.ingest_all()
    snapshot = engine.discover(category="sensor")
    snapshot.clear()
    snapshot.append("junk")
    assert len(engine.discover(category="sensor")) == 1
    assert engine.discover(category="sensor")[0].resource_id == "a"


def test_discover_without_ingestor_still_works():
    registry = ResourceRegistry()
    engine = OrganizationEngine(registry=registry)
    registry.attach_organization(engine)
    registry.register(res("a", resource_type="sensor"))
    assert {e.resource_id for e in engine.discover()} == {"a"}
    assert engine.discover(category="sensor")[0].resource_id == "a"


def test_facade_only_full_lifecycle():
    adapter, engine = make_stack(res("r1", resource_type="sensor"))
    # Single ingestion through the facade only.
    ingested = engine.ingest_resource("r1")
    assert ingested.resource_id == "r1"
    assert engine.discover(category="sensor")[0].resource_id == "r1"
    assert engine.resource("r1").name == "R1"
    # Update flows through reconcile.
    adapter.persist_resource(res("r1", resource_type="sensor", name="R1X"))
    assert engine.reconcile()["updated"] == ["r1"]
    assert engine.resource("r1").name == "R1X"
    # Forget is C.O.R.E.-side only.
    engine.forget_resource("r1")
    assert engine.discover() == []
    assert adapter.fetch_resource("r1") is not None
    # Re-ingest restores from authoritative state.
    engine.ingest_resource("r1")
    assert engine.discover(resource_id="r1")[0].name == "R1X"


def test_facade_missing_and_backend_errors():
    adapter, engine = make_stack()
    with pytest.raises(RescsResourceNotFound):
        engine.ingest_resource("ghost")

    class Broken(InMemoryRescsAdapter):
        def fetch_resource(self, resource_id):
            raise ConnectionError("down")

        def list_resources(self):
            raise TimeoutError("down")

    engine._ingestor._adapter = Broken()
    with pytest.raises(RescsUnavailable):
        engine.ingest_resource("r1")
    with pytest.raises(RescsUnavailable):
        engine.reconcile()

    with pytest.raises(ResourceNotFound):
        engine.forget_resource("ghost")
    with pytest.raises(ResourceNotFound):
        engine.resource("ghost")
