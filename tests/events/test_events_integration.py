import pytest

from core.application import CoreApplication
from core.communication import Message
from core.events import Event, EventBus
from core.security import Identity, IdentityType, Permission


@pytest.fixture
def app() -> CoreApplication:
    application = CoreApplication()
    application.start()
    yield application
    application.stop()


def _dispatch(app, operation, **kwargs):
    return app.routing.route(
        Message(
            source="asis",
            destination="_",
            message_type="RESOURCES.LIST",
            payload={"operation": operation, **kwargs},
        )
    ).payload


def test_system_and_component_lifecycle_events_published(app):
    assert app.events.event_count() >= 12


def test_communication_event_published_on_message_delivery(app):
    before = app.events.event_count()

    _dispatch(app, "list")

    assert app.events.event_count() > before


def test_service_event_published_on_execution(app):
    seen = []
    app.events.subscribe("SERVICE_EXECUTED", lambda event: seen.append(event))

    _dispatch(app, "list")

    assert any(
        event.payload.get("service_id") == "resources"
        for event in seen
    )


def test_resource_event_published_on_registration(app):
    seen = []
    app.events.subscribe("RESOURCE_REGISTERED", lambda event: seen.append(event))

    _dispatch(
        app,
        "register",
        resource_id="dev-x",
        name="Device",
        resource_type="node",
    )

    assert any(
        event.payload.get("resource_id") == "dev-x"
        for event in seen
    )


def test_resource_removed_event_published(app):
    seen = []
    app.events.subscribe("RESOURCE_REMOVED", lambda event: seen.append(event))

    _dispatch(app, "register", resource_id="dev-y", name="Y", resource_type="node")
    _dispatch(app, "remove", resource_id="dev-y")

    assert any(
        event.payload.get("resource_id") == "dev-y"
        for event in seen
    )


def test_security_event_reaches_bus_consumer(app):
    seen = []
    app.events.subscribe("IDENTITY_REGISTERED", lambda event: seen.append(event))

    identity = Identity(
        identity_id="agent-7",
        name="Agent",
        identity_type=IdentityType.SERVICE,
        permissions=frozenset({Permission.EXECUTE}),
    )

    app.security.register_identity(identity)

    assert any(event.payload.get("identity_id") == "agent-7" for event in seen)


def test_security_auth_failure_publishes_event(app):
    seen = []
    app.events.subscribe("AUTHENTICATION_FAILED", lambda event: seen.append(event))

    identity = Identity(
        identity_id="agent-8",
        name="Agent",
        identity_type=IdentityType.SERVICE,
        permissions=frozenset(),
    )
    app.security.register_identity(identity)

    try:
        app.security.authenticate("missing-identity")
    except Exception:
        pass

    assert len(seen) == 1


def test_handler_failure_is_isolated():
    bus = EventBus()
    received = []

    def bad_handler(event):
        raise RuntimeError("boom")

    def good_handler(event):
        received.append(event)

    bus.subscribe("TEST", bad_handler)
    bus.subscribe("TEST", good_handler)

    bus.publish(Event(event_type="TEST", source="x"))

    assert len(received) == 1
    assert bus.failure_count() == 1


def test_service_failed_event_published(app):
    seen = []
    app.events.subscribe("SERVICE_FAILED", lambda event: seen.append(event))

    _dispatch(app, "nonexistent_operation")

    assert len(seen) == 1


def test_system_stopped_event_emitted_on_shutdown():
    application = CoreApplication()
    application.start()

    see_stopped = []
    application.events.subscribe(
        "SYSTEM_STOPPED",
        lambda event: see_stopped.append(event),
    )

    application.stop()

    assert len(see_stopped) == 1
