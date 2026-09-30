import pytest

from core.communication import LocalCommunication, Message
from core.errors import RoutingError
from core.routing import Router


def test_add_and_get_route():
    communication = LocalCommunication()
    router = Router(communication)

    router.add_route(
        "DATA_REQUEST",
        "rescs",
    )

    assert router.get_route("DATA_REQUEST") == "rescs"
    assert router.count() == 1


def test_missing_route():
    communication = LocalCommunication()
    router = Router(communication)

    with pytest.raises(RoutingError):
        router.get_route("UNKNOWN")


def test_remove_route():
    communication = LocalCommunication()
    router = Router(communication)

    router.add_route("DATA_REQUEST", "rescs")
    router.remove_route("DATA_REQUEST")

    with pytest.raises(RoutingError):
        router.get_route("DATA_REQUEST")

    assert router.count() == 0


def test_route_message():
    communication = LocalCommunication()
    router = Router(communication)

    received = []

    def handler(message):
        received.append(message)

    communication.register("rescs", handler)

    router.add_route(
        "DATA_REQUEST",
        "rescs",
    )

    message = Message(
        source="asis",
        destination="core",
        message_type="DATA_REQUEST",
        payload={"key": "memory"},
    )

    router.route(message)

    assert len(received) == 1
    assert received[0].source == "asis"
    assert received[0].destination == "rescs"
    assert received[0].message_type == "DATA_REQUEST"
    assert received[0].payload == {"key": "memory"}
    assert received[0].message_id == message.message_id


def test_route_preserves_request_id():
    communication = LocalCommunication()
    router = Router(communication)

    received = []

    def handler(message):
        received.append(message)

    communication.register("rescs", handler)

    router.add_route("RESPONSE", "rescs")

    message = Message(
        source="asis",
        destination="core",
        message_type="RESPONSE",
        request_id="request-123",
    )

    router.route(message)

    assert received[0].request_id == "request-123"


def test_route_missing_destination():
    communication = LocalCommunication()
    router = Router(communication)

    router.add_route(
        "DATA_REQUEST",
        "missing",
    )

    message = Message(
        source="asis",
        destination="core",
        message_type="DATA_REQUEST",
    )

    with pytest.raises(RoutingError):
        router.route(message)


def test_clear_routes():
    communication = LocalCommunication()
    router = Router(communication)

    router.add_route("TYPE_A", "service-a")
    router.add_route("TYPE_B", "service-b")

    router.clear()

    assert router.count() == 0

    with pytest.raises(RoutingError):
        router.get_route("TYPE_A")


def test_multiple_routes():
    communication = LocalCommunication()
    router = Router(communication)

    router.add_route("MEMORY_REQUEST", "rescs")
    router.add_route("AI_REQUEST", "asis")
    router.add_route("DEVICE_COMMAND", "hardware")

    assert router.get_route("MEMORY_REQUEST") == "rescs"
    assert router.get_route("AI_REQUEST") == "asis"
    assert router.get_route("DEVICE_COMMAND") == "hardware"
    assert router.count() == 3
