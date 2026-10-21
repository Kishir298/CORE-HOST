from core.communication import Message
from core.services import Service, ServiceDispatcher, ServiceManager


def make_services() -> tuple[ServiceManager, ServiceDispatcher]:
    manager = ServiceManager()

    service = Service(
        service_id="demo",
        name="Demo",
        version="0.1.0",
    )

    manager.register(service)
    manager.start("demo")

    manager.register_handler(
        "demo",
        "echo",
        lambda **kwargs: kwargs,
    )

    return manager, ServiceDispatcher(manager)


def test_endpoint_naming():
    _, dispatcher = make_services()

    assert dispatcher.endpoint_for("demo") == "service:demo"
    assert dispatcher.is_service_endpoint("service:demo") is True
    assert dispatcher.is_service_endpoint("other") is False
    assert dispatcher.service_id_for("service:demo") == "demo"


def test_to_request_extracts_operation_and_kwargs():
    _, dispatcher = make_services()

    request = dispatcher.to_request(
        Message(
            source="client",
            destination="service:demo",
            message_type="DEMO.ECHO",
            payload={
                "operation": "echo",
                "value": 42,
            },
            request_id="req-1",
        )
    )

    assert request.service_id == "demo"
    assert request.operation == "echo"
    assert request.payload == {"value": 42}
    assert request.request_id == "req-1"


def test_to_request_missing_operation_raises():
    _, dispatcher = make_services()

    try:
        dispatcher.to_request(
            Message(
                source="client",
                destination="service:demo",
                message_type="DEMO.ECHO",
                payload={"value": 42},
            )
        )
        raised = False
    except ValueError:
        raised = True

    assert raised is True


def test_dispatch_success():
    from core.services import ServiceRequest

    _, dispatcher = make_services()

    response = dispatcher.dispatch(
        ServiceRequest(
            service_id="demo",
            operation="echo",
            payload={"value": 7},
            request_id="req-2",
        )
    )

    assert response.success is True
    assert response.payload == {"value": 7}


def test_dispatch_failure_captured_as_error_response():
    from core.services import ServiceRequest

    _, dispatcher = make_services()

    response = dispatcher.dispatch(
        ServiceRequest(
            service_id="demo",
            operation="missing",
            payload={},
            request_id="req-3",
        )
    )

    assert response.success is False
    assert "missing" in response.error


def test_service_request_response_contract():
    from core.services.models import ServiceRequest, ServiceResponse

    request = ServiceRequest(
        service_id="demo",
        operation="echo",
        payload={"value": 1},
        request_id="r",
    )

    response = ServiceResponse(
        service_id="demo",
        operation="echo",
        payload={"value": 1},
        success=True,
        request_id="r",
    )

    assert request.request_id == response.request_id
    assert request.service_id == response.service_id


def test_open_operation_without_requirement_not_gated_when_enforced():
    from core.security import (
        Identity,
        IdentityType,
        Permission,
        SecurityManager,
        SecurityPolicy,
    )

    manager = ServiceManager()
    service = Service(
        service_id="demo",
        name="Demo",
        version="0.1.0",
    )
    manager.register(service)
    manager.start("demo")
    manager.register_handler("demo", "echo", lambda **kwargs: kwargs)

    policy = SecurityPolicy()
    policy.grant("demo", "guarded", Permission.READ)
    policy.set_enforced(True)

    security = SecurityManager()
    security.register_identity(
        Identity(
            identity_id="caller",
            name="Caller",
            identity_type=IdentityType.SERVICE,
            permissions=frozenset({Permission.READ}),
        )
    )

    dispatcher = ServiceDispatcher(
        manager,
        security=security,
        policy=policy,
    )

    response = dispatcher.handle(
        Message(
            source="client",
            destination="service:demo",
            message_type="DEMO.ECHO",
            payload={"operation": "echo", "value": 42},
            identity_id="caller",
        )
    )

    assert response.payload["success"] is True


def test_unguarded_operation_denied_without_identity_when_required():
    from core.security import (
        Permission,
        SecurityManager,
        SecurityPolicy,
    )

    manager = ServiceManager()
    service = Service(
        service_id="demo",
        name="Demo",
        version="0.1.0",
    )
    manager.register(service)
    manager.start("demo")
    manager.register_handler("demo", "echo", lambda **kwargs: kwargs)

    policy = SecurityPolicy()
    policy.grant("demo", "echo", Permission.READ)
    policy.set_enforced(True)

    dispatcher = ServiceDispatcher(
        manager,
        security=SecurityManager(),
        policy=policy,
    )

    response = dispatcher.handle(
        Message(
            source="client",
            destination="service:demo",
            message_type="DEMO.ECHO",
            payload={"operation": "echo", "value": 42},
        )
    )

    assert response.payload["success"] is False
