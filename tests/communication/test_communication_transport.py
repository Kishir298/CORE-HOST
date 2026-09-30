import pytest

from core.communication import (
    LocalTransport,
    Message,
    Transport,
)
from core.communication.transport import MessageHandler
from core.errors import MessageError
from core.routing import Router


def test_transport_is_abstract():
    with pytest.raises(TypeError):
        Transport()  # type: ignore[abstract]


def test_local_transport_implements_transport_interface():
    transport = LocalTransport()

    assert isinstance(transport, Transport)


def test_transport_delivers_message_via_interface():
    transport: Transport = LocalTransport()
    received = []

    def handler(message: Message) -> Message | None:
        received.append(message)
        return message.create_response(
            source="service-b",
            payload={"received": True},
        )

    transport.register("service-b", handler)

    response = transport.send(
        Message(
            source="service-a",
            destination="service-b",
            message_type="TEST",
        )
    )

    assert received
    assert response is not None
    assert response.request_id == received[0].message_id


def test_transport_preserves_request_identity():
    transport: Transport = LocalTransport()

    def handler(message: Message) -> Message | None:
        return message.create_response(source=message.destination)

    transport.register("echo", handler)

    request = Message(
        source="client",
        destination="echo",
        message_type="ECHO",
        payload={"n": 1},
    )

    response = transport.send(request)

    assert response.request_id == request.message_id
    assert response.destination == request.source
    assert response.source == "echo"


def test_transport_endpoint_lifecycle():
    transport: Transport = LocalTransport()

    handler: MessageHandler = lambda message: None

    transport.register("a", handler)
    transport.register("b", handler)

    assert transport.endpoint_count() == 2
    assert transport.count() == 2
    assert transport.has_endpoint("a")

    transport.unregister("a")

    assert not transport.has_endpoint("a")
    assert transport.endpoint_count() == 1


def test_transport_routes_messages_without_assuming_local_delivery():
    """
    Routing must work against the abstract Transport contract so C.O.R.E.
    does not know whether delivery is local, serial, TCP, or network.
    """

    transport: Transport = LocalTransport()
    router = Router(transport)
    router.start()

    router.add_route("DATA_REQUEST", "storage")

    def storage_handler(message: Message) -> Message | None:
        return message.create_response(
            source="storage",
            payload={"stored": True},
        )

    transport.register("storage", storage_handler)

    response = router.route(
        Message(
            source="client",
            destination="unused",
            message_type="DATA_REQUEST",
            payload={"key": "k"},
        )
    )

    assert response is not None
    assert response.source == "storage"
    assert response.payload == {"stored": True}


def test_transport_error_when_destination_missing():
    transport: Transport = LocalTransport()

    with pytest.raises(MessageError):
        transport.send(
            Message(
                source="a",
                destination="does-not-exist",
                message_type="TEST",
            )
        )


def test_local_communication_alias_is_local_transport():
    from core.communication import LocalCommunication

    assert LocalCommunication is LocalTransport


def test_transport_is_restarted_after_stop():
    transport: Transport = LocalTransport()

    transport.stop()
    assert transport.is_running is False

    transport.register(
        "endpoint",
        lambda message: message.create_response(
            source="endpoint",
            payload={},
        ),
    )

    with pytest.raises(MessageError):
        transport.send(
            Message(source="a", destination="endpoint", message_type="T")
        )

    transport.start()
    assert transport.is_running is True
