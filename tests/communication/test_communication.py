from datetime import timezone

import pytest

from core.communication import (
    LocalCommunication,
    Message,
    MessageSerializer,
)
from core.errors import MessageError


def test_message_creation():
    message = Message(
        source="asis",
        destination="core",
        message_type="DATA_REQUEST",
        payload={"key": "value"},
    )

    assert message.source == "asis"
    assert message.destination == "core"
    assert message.message_type == "DATA_REQUEST"
    assert message.payload == {"key": "value"}
    assert message.message_id
    assert message.request_id is None
    assert message.timestamp.tzinfo == timezone.utc


def test_message_response():
    request = Message(
        source="asis",
        destination="rescs",
        message_type="DATA_REQUEST",
        payload={"key": "memory"},
    )

    response = request.create_response(
        source="rescs",
        payload={"value": "test"},
    )

    assert response.source == "rescs"
    assert response.destination == "asis"
    assert response.message_type == "RESPONSE"
    assert response.payload == {"value": "test"}
    assert response.request_id == request.message_id


def test_message_serialization():
    message = Message(
        source="asis",
        destination="core",
        message_type="TEST",
        payload={"value": 42},
    )

    serialized = MessageSerializer.serialize(message)

    assert isinstance(serialized, str)
    assert message.message_id in serialized


def test_message_deserialization():
    message = Message(
        source="asis",
        destination="core",
        message_type="TEST",
        payload={"value": 42},
    )

    serialized = MessageSerializer.serialize(message)
    restored = MessageSerializer.deserialize(serialized)

    assert restored.message_id == message.message_id
    assert restored.source == message.source
    assert restored.destination == message.destination
    assert restored.message_type == message.message_type
    assert restored.payload == message.payload
    assert restored.timestamp == message.timestamp


def test_local_communication():
    communication = LocalCommunication()
    received = []

    def handler(message):
        received.append(message)
        return message.create_response(
            source="service-b",
            payload={"received": True},
        )

    communication.register("service-b", handler)

    message = Message(
        source="service-a",
        destination="service-b",
        message_type="TEST",
    )

    response = communication.send(message)

    assert received == [message]
    assert response is not None
    assert response.source == "service-b"
    assert response.destination == "service-a"
    assert response.payload == {"received": True}


def test_local_communication_missing_endpoint():
    communication = LocalCommunication()

    message = Message(
        source="service-a",
        destination="missing",
        message_type="TEST",
    )

    with pytest.raises(MessageError):
        communication.send(message)


def test_duplicate_endpoint():
    communication = LocalCommunication()

    def handler(message):
        return None

    communication.register("service", handler)

    with pytest.raises(MessageError):
        communication.register("service", handler)


def test_unregister_endpoint():
    communication = LocalCommunication()

    def handler(message):
        return None

    communication.register("service", handler)

    assert communication.has_endpoint("service")

    communication.unregister("service")

    assert not communication.has_endpoint("service")
    assert communication.endpoint_count() == 0
