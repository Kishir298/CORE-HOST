import pytest

from core.application import CoreApplication
from core.communication import Message
from core.security import (
    Identity,
    IdentityType,
    Permission,
)


@pytest.fixture
def app() -> CoreApplication:
    application = CoreApplication()
    application.start()
    yield application
    application.stop()


def _register_identity(app, identity_id, permissions):
    app.security.register_identity(
        Identity(
            identity_id=identity_id,
            name=identity_id.title(),
            identity_type=IdentityType.SERVICE,
            permissions=frozenset(permissions),
        )
    )
    return identity_id


def _dispatch(app, operation, identity_id=None, **kwargs):
    return app.routing.route(
        Message(
            source="asis",
            destination="_",
            message_type="RESOURCES.LIST",
            payload={"operation": operation, **kwargs},
            identity_id=identity_id,
        )
    ).payload


def test_internal_flow_succeeds_when_enforcement_inactive(app):
    assert app.security_policy.enforced is False

    response = _dispatch(app, "list")

    assert response["success"] is True
    assert response["result"] == {"resources": []}


def test_enforcement_inactive_allows_missing_identity(app):
    app.security_policy.set_enforced(False)

    response = _dispatch(
        app,
        "register",
        resource_id="dev-1",
        name="Sensor",
        resource_type="device",
    )

    assert response["success"] is True


def test_authorized_operation_succeeds(app):
    app.security_policy.set_enforced(True)
    _register_identity(
        app,
        "driver",
        {Permission.READ, Permission.WRITE},
    )

    response = _dispatch(
        app,
        "register",
        identity_id="driver",
        resource_id="dev-1",
        name="Sensor",
        resource_type="device",
    )

    assert response["success"] is True
    assert response["result"]["resource"]["id"] == "dev-1"


def test_read_operation_requires_authentication_when_enforced(app):
    app.security_policy.set_enforced(True)
    _register_identity(
        app,
        "driver",
        {Permission.READ},
    )

    response = _dispatch(app, "list")

    assert response["success"] is False
    assert "Authentication is required" in response["error"]


def test_unauthorized_operation_denied(app):
    app.security_policy.set_enforced(True)
    _register_identity(
        app,
        "reader",
        {Permission.READ},
    )

    response = _dispatch(
        app,
        "register",
        identity_id="reader",
        resource_id="dev-1",
        name="Sensor",
        resource_type="device",
    )

    assert response["success"] is False
    assert "lacks permission" in response["error"]


def test_unknown_identity_denied_when_enforced(app):
    app.security_policy.set_enforced(True)

    response = _dispatch(
        app,
        "list",
        identity_id="unknown",
    )

    assert response["success"] is False


def test_denial_emits_security_access_denied_event(app):
    seen = []
    app.events.subscribe(
        "SECURITY_ACCESS_DENIED",
        lambda event: seen.append(event.payload),
    )

    app.security_policy.set_enforced(True)

    response = _dispatch(app, "list")

    assert response["success"] is False

    assert any(
        payload.get("service_id") == "resources"
        and payload.get("operation") == "list"
        for payload in seen
    )


def test_read_operation_authorized(app):
    app.security_policy.set_enforced(True)
    _register_identity(app, "observer", {Permission.READ})

    response = _dispatch(app, "list", identity_id="observer")

    assert response["success"] is True
    assert response["result"] == {"resources": []}


def test_denied_request_never_reaches_service(app):
    app.security_policy.set_enforced(True)

    response = _dispatch(
        app,
        "register",
        resource_id="ghost",
        name="Ghost",
        resource_type="device",
    )

    assert response["success"] is False

    app.security_policy.set_enforced(False)

    listing = _dispatch(app, "list")
    assert listing["success"] is True
    assert all(
        item["id"] != "ghost"
        for item in listing["result"]["resources"]
    )
