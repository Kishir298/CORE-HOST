"""R.E.S.C.S. -> Organization ingestion boundary tests.

Covers the defined contract using the actual adapter structures
(InMemoryRescsAdapter with real Resource objects, plus raw storage-shape
dicts carrying backend-specific extras): validation, normalization,
idempotent ingestion, updates, explicit deletion, backend-failure safety,
and discovery through the existing Organization APIs.
"""

import pytest

from core.errors import ResourceNotFound
from core.organization import OrganizationEngine
from core.organization.ingestion import (
    REQUIRED_RESOURCE_FIELDS,
    IngestionError,
    InvalidResourceData,
    RescsResourceNotFound,
    RescsUnavailable,
    ResourceIngestor,
    normalize_resource,
    validate_rescs_resource,
)
from core.rescs import InMemoryRescsAdapter
from core.resources import Resource, ResourceRegistry


def make_adapter(*resources: Resource) -> InMemoryRescsAdapter:
    adapter = InMemoryRescsAdapter()
    for resource in resources:
        adapter.persist_resource(resource)
    return adapter


def make_stack(adapter=None):
    adapter = adapter if adapter is not None else InMemoryRescsAdapter()
    registry = ResourceRegistry()
    engine = OrganizationEngine(registry=registry)
    registry.attach_organization(engine)
    return adapter, registry, engine, ResourceIngestor(adapter, registry, engine)


def storage_shape(**overrides):
    """Actual R.E.S.C.S.-side shape incl. storage-specific extras."""
    shape = {
        "resource_id": "abc123",
        "resource_type": "document",
        "name": "example.pdf",
        "owner": "rishik",
        "metadata": {"size": 123456, "mime_type": "application/pdf"},
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-02-01T00:00:00+00:00",
        "storage_location": "/var/rescs/blobs/abc123",
        "internal_database_id": 123,
    }
    shape.update(overrides)
    return shape


# -- contract ---------------------------------------------------------------


def test_contract_fields_defined():
    assert set(REQUIRED_RESOURCE_FIELDS) == {
        "resource_id",
        "resource_type",
        "name",
    }


def test_valid_resource_accepted():
    validated = validate_rescs_resource(storage_shape())
    assert validated["resource_id"] == "abc123"
    assert validated["resource_type"] == "document"
    assert validated["name"] == "example.pdf"
    assert validated["owner"] == "rishik"
    assert validated["metadata"] == {
        "size": 123456,
        "mime_type": "application/pdf",
    }


def test_valid_resource_object_accepted():
    validated = validate_rescs_resource(
        Resource(resource_id="r1", name="N", resource_type="node", owner="o")
    )
    assert validated["resource_id"] == "r1"
    assert validated["metadata"] == {}


def test_missing_resource_id_fails():
    shape = storage_shape()
    del shape["resource_id"]
    with pytest.raises(InvalidResourceData):
        validate_rescs_resource(shape)


@pytest.mark.parametrize("field", ["resource_id", "resource_type", "name"])
@pytest.mark.parametrize("bad", [None, "", 123, ["x"], {"k": "v"}])
def test_required_fields_must_be_non_empty_strings(field, bad):
    shape = storage_shape()
    shape[field] = bad
    with pytest.raises(InvalidResourceData):
        validate_rescs_resource(shape)


def test_invalid_metadata_fails():
    with pytest.raises(InvalidResourceData):
        validate_rescs_resource(storage_shape(metadata="not-a-dict"))
    with pytest.raises(InvalidResourceData):
        validate_rescs_resource(storage_shape(metadata=["size"]))


def test_non_object_fails():
    for bad in (None, "abc123", 42, ["resource_id"]):
        with pytest.raises(InvalidResourceData):
            validate_rescs_resource(bad)


def test_owner_absent_normalizes_to_none():
    shape = storage_shape()
    del shape["owner"]
    assert validate_rescs_resource(shape)["owner"] is None


def test_empty_owner_fails():
    with pytest.raises(InvalidResourceData):
        validate_rescs_resource(storage_shape(owner=""))


# -- normalization ------------------------------------------------------------


def test_normalization_produces_core_resource():
    resource = normalize_resource(storage_shape())
    assert isinstance(resource, Resource)
    assert resource.resource_id == "abc123"
    assert resource.resource_type == "document"
    assert resource.name == "example.pdf"
    assert resource.owner == "rishik"
    assert resource.metadata == {"size": 123456, "mime_type": "application/pdf"}


def test_normalization_drops_storage_extras():
    resource = normalize_resource(storage_shape())
    dumped = resource.to_dict()
    assert "storage_location" not in dumped
    assert "internal_database_id" not in dumped
    assert "created_at" not in dumped
    assert "updated_at" not in dumped


def test_normalization_is_deterministic():
    first = normalize_resource(storage_shape())
    second = normalize_resource(storage_shape())
    assert first.resource_id == second.resource_id
    assert first.resource_type == second.resource_type
    assert first.name == second.name
    assert first.owner == second.owner
    assert first.metadata == second.metadata


# -- ingestion ------------------------------------------------------------------


def test_ingest_valid_resource_organizes_it():
    adapter = make_adapter(
        Resource(
            resource_id="abc123",
            name="example.pdf",
            resource_type="document",
            owner="rishik",
            metadata={"mime_type": "application/pdf"},
        )
    )
    _, registry, engine, ingestor = make_stack(adapter)
    resource = ingestor.ingest_resource("abc123")
    assert resource.resource_id == "abc123"
    entry = engine.get("resource:abc123")
    assert entry.category == "document"
    assert entry.name == "example.pdf"
    assert entry.resource_id == "abc123"


def test_ingest_requires_non_empty_id():
    _, _, _, ingestor = make_stack()
    for bad in ("", None, 123):
        with pytest.raises(InvalidResourceData):
            ingestor.ingest_resource(bad)


def test_ingest_unknown_id_reports_missing():
    _, registry, engine, ingestor = make_stack()
    with pytest.raises(RescsResourceNotFound):
        ingestor.ingest_resource("ghost")
    assert registry.count() == 0
    assert engine.count() == 0


def test_duplicate_retrieval_is_idempotent():
    adapter = make_adapter(
        Resource(resource_id="abc123", name="Doc", resource_type="document")
    )
    _, registry, engine, ingestor = make_stack(adapter)
    for _ in range(10):
        ingestor.ingest_resource("abc123")
    assert registry.count() == 1
    assert engine.count() == 1
    assert engine.get("resource:abc123").name == "Doc"


def test_resource_update_refreshes_entry():
    adapter = make_adapter(
        Resource(resource_id="abc123", name="old.pdf", resource_type="document")
    )
    _, registry, engine, ingestor = make_stack(adapter)
    ingestor.ingest_resource("abc123")
    adapter.persist_resource(
        Resource(resource_id="abc123", name="new.pdf", resource_type="document")
    )
    ingestor.ingest_resource("abc123")
    assert registry.count() == 1
    assert engine.count() == 1
    assert registry.get("abc123").name == "new.pdf"
    assert engine.get("resource:abc123").name == "new.pdf"


def test_resource_type_change_recategorizes():
    adapter = make_adapter(
        Resource(resource_id="abc123", name="Doc", resource_type="document")
    )
    _, registry, engine, ingestor = make_stack(adapter)
    ingestor.ingest_resource("abc123")
    adapter.persist_resource(
        Resource(resource_id="abc123", name="Doc", resource_type="archive")
    )
    ingestor.ingest_resource("abc123")
    assert registry.count() == 1
    assert engine.get("resource:abc123").category == "archive"
    assert engine.by_category("document") == []


def test_explicit_deletion_removes_entry():
    adapter = make_adapter(
        Resource(resource_id="abc123", name="Doc", resource_type="document")
    )
    _, registry, engine, ingestor = make_stack(adapter)
    ingestor.ingest_resource("abc123")
    adapter.delete_resource("abc123")
    with pytest.raises(RescsResourceNotFound):
        ingestor.ingest_resource("abc123")
    assert registry.count() == 0
    assert engine.by_resource("abc123") == []


def test_forget_resource_removes_entry_only():
    adapter = make_adapter(
        Resource(resource_id="abc123", name="Doc", resource_type="document")
    )
    _, registry, engine, ingestor = make_stack(adapter)
    ingestor.ingest_resource("abc123")
    removed = ingestor.forget_resource("abc123")
    assert removed.resource_id == "abc123"
    assert registry.count() == 0
    assert engine.by_resource("abc123") == []
    # Storage authority untouched.
    assert adapter.fetch_resource("abc123") is not None
    with pytest.raises(ResourceNotFound):
        ingestor.forget_resource("abc123")


class ExplodingAdapter(InMemoryRescsAdapter):
    def fetch_resource(self, resource_id):
        raise ConnectionError("backend down")

    def list_resources(self):
        raise TimeoutError("backend timeout")


def test_backend_failure_is_not_deletion():
    adapter = make_adapter(
        Resource(resource_id="abc123", name="Doc", resource_type="document")
    )
    _, registry, engine, ingestor = make_stack(adapter)
    ingestor.ingest_resource("abc123")
    ingestor._adapter = ExplodingAdapter()
    with pytest.raises(RescsUnavailable):
        ingestor.ingest_resource("abc123")
    # Existing index data survives the outage.
    assert registry.count() == 1
    assert engine.get("resource:abc123").name == "Doc"
    with pytest.raises(RescsUnavailable):
        ingestor.ingest_all()
    assert registry.count() == 1


def test_error_types_preserve_boundaries():
    assert issubclass(InvalidResourceData, IngestionError)
    assert issubclass(RescsUnavailable, IngestionError)
    assert issubclass(RescsResourceNotFound, IngestionError)


# -- discovery --------------------------------------------------------------------


def test_resource_lookup_resolves_through_registry():
    adapter = make_adapter(
        Resource(resource_id="abc123", name="Doc", resource_type="document")
    )
    _, _, engine, ingestor = make_stack(adapter)
    ingestor.ingest_resource("abc123")
    assert engine.resource("abc123").name == "Doc"


def test_multiple_resources_become_separate_entries():
    adapter = make_adapter(
        Resource(resource_id="a", name="A", resource_type="sensor", owner="ris"),
        Resource(resource_id="b", name="B", resource_type="actuator", owner="ris"),
        Resource(resource_id="c", name="C", resource_type="sensor", owner="other"),
    )
    _, registry, engine, ingestor = make_stack(adapter)
    summary = ingestor.ingest_all()
    assert summary == {"ingested": 3, "updated": 0, "failed": 0, "errors": {}}
    assert registry.count() == 3
    assert engine.count() == 3


def test_category_discovery_after_ingest():
    adapter = make_adapter(
        Resource(resource_id="a", name="A", resource_type="sensor"),
        Resource(resource_id="b", name="B", resource_type="actuator"),
    )
    _, _, engine, ingestor = make_stack(adapter)
    ingestor.ingest_all()
    assert {entry.resource_id for entry in engine.by_category("sensor")} == {"a"}
    assert {entry.resource_id for entry in engine.by_category("actuator")} == {"b"}


def test_resource_discovery_after_ingest():
    adapter = make_adapter(
        Resource(resource_id="a", name="A", resource_type="sensor")
    )
    _, _, engine, ingestor = make_stack(adapter)
    ingestor.ingest_all()
    entries = engine.by_resource("a")
    assert len(entries) == 1
    assert entries[0].entry_id == "resource:a"


def test_ingest_all_collects_invalid_without_aborting():
    adapter = InMemoryRescsAdapter()
    adapter.persist_resource(
        Resource(resource_id="good", name="Good", resource_type="sensor")
    )
    _, registry, engine, ingestor = make_stack(adapter)
    # Corrupt the backend listing with a non-conforming entry.
    original = adapter.list_resources

    def listing():
        return original() + [{"name": "no-id"}]

    adapter.list_resources = listing
    summary = ingestor.ingest_all()
    assert summary["ingested"] == 1
    assert summary["failed"] == 1
    assert registry.count() == 1
    assert engine.count() == 1


def test_ingest_all_is_deterministic():
    adapter = make_adapter(
        Resource(resource_id="b", name="B", resource_type="node"),
        Resource(resource_id="a", name="A", resource_type="node"),
    )
    _, _, _, ingestor = make_stack(adapter)
    first = ingestor.ingest_all()
    second = ingestor.ingest_all()
    assert first["ingested"] == 2
    assert second == {"ingested": 0, "updated": 2, "failed": 0, "errors": {}}


def test_ingestor_requires_adapter_and_registry():
    with pytest.raises(ValueError):
        ResourceIngestor(None)
    ingestor = ResourceIngestor(InMemoryRescsAdapter())
    with pytest.raises(IngestionError):
        ingestor.ingest_resource("abc123")


def test_broken_attach_ingestor_surfaces_instead_of_silently_detaching():
    class BrokenOrg:
        def attach_ingestor(self, ingestor):
            raise RuntimeError("wiring broken")

    with pytest.raises(RuntimeError, match="wiring broken"):
        ResourceIngestor(InMemoryRescsAdapter(), None, BrokenOrg())


def test_organization_without_attach_method_still_constructs():
    class MinimalOrg:
        pass

    ingestor = ResourceIngestor(
        InMemoryRescsAdapter(), None, MinimalOrg()
    )
    assert ingestor.adapter is not None
