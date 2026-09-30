from collections import defaultdict
from collections.abc import Callable
from threading import RLock

from .models import Event

EventHandler = Callable[[Event], None]


class EventBus:
    """
    Central event bus for C.O.R.E.

    EventBus provides synchronous publish/subscribe communication between
    C.O.R.E. components. Handlers are isolated from the internal subscriber
    registry so subscriptions can safely be modified while events are being
    dispatched.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)
        self._lock = RLock()
        self._active = True
        self._events_published = 0
        self._handler_failures = 0

    def start(self) -> None:
        """Start the event bus."""

        with self._lock:
            if self._active:
                return

            self._active = True

    def stop(self) -> None:
        """Stop the event bus without removing subscriptions."""

        with self._lock:
            self._active = False

    @property
    def is_running(self) -> bool:
        """Return whether the event bus is active."""

        with self._lock:
            return self._active

    def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
    ) -> None:
        """Subscribe a handler to an event type."""

        if not event_type:
            raise ValueError("Event type cannot be empty.")

        if not callable(handler):
            raise TypeError("Event handler must be callable.")

        with self._lock:
            if handler not in self._subscribers[event_type]:
                self._subscribers[event_type].append(handler)

    def unsubscribe(
        self,
        event_type: str,
        handler: EventHandler,
    ) -> None:
        """Remove a handler from an event type."""

        with self._lock:
            subscribers = self._subscribers.get(event_type)

            if not subscribers:
                return

            if handler in subscribers:
                subscribers.remove(handler)

            if not subscribers:
                del self._subscribers[event_type]

    def publish(self, event: Event) -> None:
        """
        Publish an event to all subscribed handlers.

        A snapshot of the handlers is taken before dispatch so handlers may
        safely subscribe or unsubscribe while an event is being processed.

        Handler failures are isolated: a failing subscriber is recorded as
        a delivery failure and does not prevent other subscribers from
        receiving the event. This keeps a single misbehaving consumer from
        breaking the rest of C.O.R.E.
        """

        if not isinstance(event, Event):
            raise TypeError("EventBus can only publish Event instances.")

        with self._lock:
            if not self._active:
                raise RuntimeError("Event bus is not running.")

            handlers = list(
                self._subscribers.get(event.event_type, [])
            )

            self._events_published += 1

        for handler in handlers:
            try:
                handler(event)
            except Exception:
                with self._lock:
                    self._handler_failures += 1

    def emit(
        self,
        event_type: str,
        source: str,
        payload: dict | None = None,
    ) -> Event:
        """
        Create and publish an event.

        Returns the Event instance after successful publication.
        """

        event = Event(
            event_type=event_type,
            source=source,
            payload=payload or {},
        )

        self.publish(event)
        return event

    def has_subscribers(self, event_type: str) -> bool:
        """Return whether an event type has at least one subscriber."""

        with self._lock:
            return bool(self._subscribers.get(event_type))

    def clear(self) -> None:
        """Remove all event subscriptions."""

        with self._lock:
            self._subscribers.clear()

    def subscriber_count(self, event_type: str | None = None) -> int:
        """
        Return the number of subscribers.

        If event_type is supplied, return the number subscribed to that
        specific event type. Otherwise return the total number of handlers.
        """

        with self._lock:
            if event_type is not None:
                return len(self._subscribers.get(event_type, []))

            return sum(
                len(subscribers)
                for subscribers in self._subscribers.values()
            )

    def event_count(self) -> int:
        """Return the number of successfully dispatched events."""

        with self._lock:
            return self._events_published

    def failure_count(self) -> int:
        """Return the number of handler failures."""

        with self._lock:
            return self._handler_failures