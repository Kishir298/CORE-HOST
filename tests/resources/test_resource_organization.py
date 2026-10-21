import pytest

from core.application import CoreApplication
from core.communication import Message
from core.organization import OrganizationEngine
from core.resources import Resource, ResourceRegistry


@pytest.fixture
def app() -> CoreApplication:
    application = CoreApplication()
    application.start()
    yield application
    application.stop()


def _dispatch(app, operation, **kwargs):
    response = app.routing.route(
        Message(
            source="asis",
            destination="_",
            message_type="RESOURCES.LIST",
            payload={"operation": operation, **kwargs},
        )
    )
    return response.payload


def test_register_resource_auto_categorizes(app):
    result = _dispatch(
        app,
        "register",
        resource_id="dev-1",
        name="TempSensor",
        resource_type="sensor",
        owner="ris",
        source="esp32",
        metadata={"floor": 1},
    )

    assert result["success"] is True

    entries = app.organization.by_resource("dev-1")
    assert len(entries) == 1
    assert entries[0].category == "sensor"
    assert entries[0].resource_id == "dev-1"


def test_discover_by_type_and_owner(app):
    _dispatch(app, "register", resource_id="a", name="A", resource_type="sensor", owner="ris")
    _dispatch(app, "register", resource_id="b", name="B", resource_type="actuator", owner="ris")
    _dispatch(app, "register", resource_id="c", name="C", resource_type="sensor", owner="other")

    by_type = _dispatch(app, "discover", resource_type="sensor")["result"]["resources"]
    ids = {resource["id"] for resource in by_type}
    assert ids == {"a", "c"}

    by_owner = _dispatch(app, "discover", owner="ris")["result"]["resources"]
    owner_ids = {resource["id"] for resource in by_owner}
    assert owner_ids == {"a", "b"}


def test_discover_by_category_resolves_through_organization(app):
    _dispatch(app, "register", resource_id="a", name="A", resource_type="sensor", owner="ris")
    _dispatch(app, "register", resource_id="b", name="B", resource_type="actuator", owner="ris")

    by_category = _dispatch(app, "discover", category="sensor")["result"]["resources"]
    ids = {resource["id"] for resource in by_category}
    assert ids == {"a"}


def test_organization_relationship_discovery(app):
    _dispatch(app, "register", resource_id="dev-9", name="Motor", resource_type="actuator")

    resource = app.organization.resource("dev-9")
    assert resource.resource_id == "dev-9"
    assert resource.name == "Motor"

    entries = app.organization.by_resource("dev-9")
    assert len(entries) == 1


def test_update_resource_status(app):
    _dispatch(app, "register", resource_id="dev-5", name="Led", resource_type="lights")

    result = _dispatch(app, "update", resource_id="dev-5", status="online")
    assert result["result"]["resource"]["status"] == "online"
    assert app.resources.get("dev-5").status == "online"


def test_remove_resource_cleans_organization_entries(app):
    _dispatch(app, "register", resource_id="dev-6", name="Camera", resource_type="camera")

    assert len(app.organization.by_resource("dev-6")) == 1

    result = _dispatch(app, "remove", resource_id="dev-6")
    assert result["result"]["removed"] == "dev-6"

    assert app.resources.count() == 0
    assert len(app.organization.by_resource("dev-6")) == 0


def test_resource_registry_has_list_resources(app):
    _dispatch(app, "register", resource_id="x", name="X", resource_type="node")

    resources = app.resources.list_resources()
    assert len(resources) == 1
    assert resources[0].resource_id == "x"


def test_resource_can_be_wired_without_organization():
    registry = ResourceRegistry()
    resource = Resource(
        resource_id="standalone",
        name="Standalone",
        resource_type="node",
    )

    registry.register(resource)

    assert registry.count() == 1


def test_organization_created_with_registry_for_discovery():
    registry = ResourceRegistry()
    engine = OrganizationEngine(registry=registry)
    registry.attach_organization(engine)

    registry.register(
        Resource(
            resource_id="linked",
            name="Linked",
            resource_type="node",
        )
    )

    assert engine.resource("linked").resource_id == "linked"
    assert len(engine.by_resource("linked")) == 1


def test_registry_clear_cascades_to_organization():
    registry = ResourceRegistry()
    engine = OrganizationEngine(registry=registry)
    registry.attach_organization(engine)

    registry.register(
        Resource(
            resource_id="to-clear",
            name="Temporary",
            resource_type="node",
        )
    )
    assert engine.count() == 1

    registry.clear()

    assert registry.count() == 0
    assert engine.count() == 0
    assert engine.by_resource("to-clear") == []
