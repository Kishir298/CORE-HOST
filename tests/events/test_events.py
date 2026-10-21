from datetime import datetime, timezone

from core.events import Event, EventBus


def test_event_defaults():
    event = Event(
        event_type="RESOURCE_CONNECTED",
        source="test",
    )

    assert event.event_type == "RESOURCE_CONNECTED"
    assert event.source == "test"
    assert event.payload == {}
    assert event.event_id
    assert isinstance(event.timestamp, datetime)
    assert event.timestamp.tzinfo == timezone.utc


def test_event_payload():
    event = Event(
        event_type="DATA_RECEIVED",
        source="device",
        payload={"value": 42},
    )

    assert event.payload == {"value": 42}


def test_event_bus_subscribe_and_publish():
    bus = EventBus()
    received = []

    def handler(event):
        received.append(event)

    bus.subscribe("TEST_EVENT", handler)

    event = Event(
        event_type="TEST_EVENT",
        source="test",
    )

    bus.publish(event)

    assert received == [event]


def test_event_bus_unsubscribe():
    bus = EventBus()
    received = []

    def handler(event):
        received.append(event)

    bus.subscribe("TEST_EVENT", handler)
    bus.unsubscribe("TEST_EVENT", handler)

    bus.publish(
        Event(
            event_type="TEST_EVENT",
            source="test",
        )
    )

    assert received == []


def test_event_bus_does_not_duplicate_subscriber():
    bus = EventBus()

    def handler(event):
        pass

    bus.subscribe("TEST_EVENT", handler)
    bus.subscribe("TEST_EVENT", handler)

    assert bus.subscriber_count("TEST_EVENT") == 1


def test_event_bus_emit():
    bus = EventBus()
    received = []

    def handler(event):
        received.append(event)

    bus.subscribe("DEVICE_CONNECTED", handler)

    event = bus.emit(
        event_type="DEVICE_CONNECTED",
        source="arduino",
        payload={"device_id": "arduino-01"},
    )

    assert event.event_type == "DEVICE_CONNECTED"
    assert event.source == "arduino"
    assert event.payload == {"device_id": "arduino-01"}
    assert received == [event]


def test_event_bus_subscriber_count():
    bus = EventBus()

    def handler_one(event):
        pass

    def handler_two(event):
        pass

    bus.subscribe("TEST_EVENT", handler_one)
    bus.subscribe("TEST_EVENT", handler_two)
    bus.subscribe("OTHER_EVENT", handler_one)

    assert bus.subscriber_count("TEST_EVENT") == 2
    assert bus.subscriber_count("OTHER_EVENT") == 1
    assert bus.subscriber_count() == 3


def test_event_bus_clear():
    bus = EventBus()

    def handler(event):
        pass

    bus.subscribe("TEST_EVENT", handler)
    bus.subscribe("OTHER_EVENT", handler)

    bus.clear()

    assert bus.subscriber_count() == 0
