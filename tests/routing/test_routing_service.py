import pytest

from core.application import CoreApplication
from core.communication import Message
from core.errors import RoutingError


@pytest.fixture
def app() -> CoreApplication:
    application = CoreApplication()
    application.start()
    yield application
    application.stop()


def test_router_executes_service_operation(app):
    response = app.routing.route(
        Message(
            source="asis",
            destination="_",
            message_type="RESOURCES.LIST",
            payload={"operation": "list"},
        )
    )

    assert response is not None
    assert response.payload["success"] is True
    assert response.payload["result"] == {"resources": []}


def test_router_propagates_request_id(app):
    message = Message(
        source="asis",
        destination="_",
        message_type="RESOURCES.LIST",
        payload={"operation": "list"},
    )

    response = app.routing.route(message)

    assert response.request_id == message.message_id


def test_router_register_returns_resource(app):
    response = app.routing.route(
        Message(
            source="asis",
            destination="_",
            message_type="RESOURCES.LIST",
            payload={
                "operation": "register",
                "resource_id": "dev-1",
                "name": "Sensor",
                "resource_type": "device",
                "metadata": {"floor": 2},
            },
        )
    )

    assert response.payload["success"] is True
    result = response.payload["result"]
    assert result["resource"]["id"] == "dev-1"
    assert result["resource"]["type"] == "device"


def test_router_get_returns_registered_resource(app):
    app.routing.route(
        Message(
            source="asis",
            destination="_",
            message_type="RESOURCES.LIST",
            payload={
                "operation": "register",
                "resource_id": "dev-2",
                "name": "Motor",
                "resource_type": "actuator",
            },
        )
    )

    response = app.routing.route(
        Message(
            source="asis",
            destination="_",
            message_type="RESOURCES.LIST",
            payload={"operation": "get", "resource_id": "dev-2"},
        )
    )

    assert response.payload["success"] is True
    assert response.payload["result"]["resource"]["id"] == "dev-2"
    assert response.payload["result"]["resource"]["type"] == "actuator"


def test_router_returns_error_for_unknown_service_operation(app):
    response = app.routing.route(
        Message(
            source="asis",
            destination="_",
            message_type="RESOURCES.LIST",
            payload={"operation": "does_not_exist"},
        )
    )

    assert response.payload["success"] is False
    assert "does_not_exist" in response.payload["error"]


def test_router_returns_error_for_missing_operation(app):
    response = app.routing.route(
        Message(
            source="asis",
            destination="_",
            message_type="RESOURCES.LIST",
            payload={},
        )
    )

    assert response.payload["success"] is False
    assert "operation" in response.payload["error"]


def test_router_returns_error_for_invalid_payload(app):
    response = app.routing.route(
        Message(
            source="asis",
            destination="_",
            message_type="RESOURCES.LIST",
            payload="not-a-dict",
        )
    )

    assert response.payload["success"] is False


def test_router_raises_for_unknown_route(app):
    with pytest.raises(RoutingError):
        app.routing.route(
            Message(
                source="asis",
                destination="_",
                message_type="UNKNOWN.ROUTE",
                payload={"operation": "list"},
            )
        )


def test_health_service_returns_overall_status(app):
    response = app.routing.route(
        Message(
            source="asis",
            destination="_",
            message_type="HEALTH.STATUS",
            payload={"operation": "status"},
        )
    )

    assert response.payload["success"] is True
    assert response.payload["result"]["overall"] == "healthy"


def test_service_endpoints_exposed_on_transport(app):
    assert app.communication.has_endpoint("service:resources")
    assert app.communication.has_endpoint("service:health")
    assert app.communication.has_endpoint("service:organization")
    assert app.communication.has_endpoint("service:routing")
    assert app.communication.has_endpoint("service:communication")
    assert app.communication.has_endpoint("service:events")


def test_service_routes_registered(app):
    assert app.routing.has_route("RESOURCES.LIST")
    assert app.routing.has_route("HEALTH.STATUS")
    assert app.routing.has_route("ORGANIZATION.LIST")
    assert app.routing.has_route("ROUTING.ROUTES")
    assert app.routing.has_route("COMMUNICATION.STATUS")
