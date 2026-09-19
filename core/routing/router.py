from threading import RLock

from core.communication import Message, Transport
from core.errors import RoutingError


class Router:
    """
    Routes C.O.R.E. messages to registered destinations.

    Router is responsible for deciding where a message should go based on
    its message type. Actual message delivery is delegated to the
    communication layer.

    Device-aware routing coexists with static message-type routes: when a
    device registry is attached, :meth:`route_to_device` resolves the
    destination through the registry (existence / online / connection
    validation) and preserves message identity while delegating delivery
    to the transport. Static :meth:`route` behavior is unchanged.
    """

    def __init__(self, communication: Transport, device_registry=None) -> None:
        self._communication = communication
        self._routes: dict[str, str] = {}
        self._lock = RLock()
        self._active = True
        self._messages_routed = 0
        self._routing_failures = 0
        self._devices = device_registry
        self._device_messages_routed = 0
        self._device_routing_failures = 0

    def start(self) -> None:
        """Start the router."""

        with self._lock:
            if self._active:
                return

            self._active = True

    def stop(self) -> None:
        """Stop the router."""

        with self._lock:
            self._active = False

    @property
    def is_running(self) -> bool:
        """Return whether the router is active."""

        with self._lock:
            return self._active

    def add_route(self, message_type: str, destination: str) -> None:
        """Route a message type to a destination."""

        if not message_type:
            raise RoutingError(
                "Message type cannot be empty."
            )

        if not destination:
            raise RoutingError(
                "Route destination cannot be empty."
            )

        with self._lock:
            self._routes[message_type] = destination

    def remove_route(self, message_type: str) -> None:
        """Remove a message-type route."""

        with self._lock:
            self._routes.pop(message_type, None)

    def has_route(self, message_type: str) -> bool:
        """Return whether a route exists for a message type."""

        with self._lock:
            return message_type in self._routes

    def list_routes(self) -> dict[str, str]:
        """Return a snapshot of the static route table (read-only copy)."""

        with self._lock:
            return dict(self._routes)

    def get_route(self, message_type: str) -> str:
        """Return the destination for a message type."""

        with self._lock:
            try:
                return self._routes[message_type]
            except KeyError as exc:
                raise RoutingError(
                    f"No route configured for message type: {message_type}"
                ) from exc

    def route(self, message: Message) -> Message | None:
        """
        Route a message to its configured destination.

        The original message identity fields are preserved while the
        destination is replaced with the configured route destination.
        """

        if not isinstance(message, Message):
            raise RoutingError(
                "Router can only route Message instances."
            )

        with self._lock:
            if not self._active:
                raise RoutingError(
                    "Router is not running."
                )

        destination = self.get_route(message.message_type)

        routed_message = Message(
            source=message.source,
            destination=destination,
            message_type=message.message_type,
            payload=message.payload,
            message_id=message.message_id,
            timestamp=message.timestamp,
            request_id=message.request_id,
            identity_id=message.identity_id,
        )

        try:
            response = self._communication.send(routed_message)
        except Exception as exc:
            with self._lock:
                self._routing_failures += 1

            raise RoutingError(
                f"Failed to route message to: {destination}"
            ) from exc

        with self._lock:
            self._messages_routed += 1

        return response

    def set_transport(self, communication: Transport) -> None:
        """Replace the underlying transport (used for config-driven selection)."""

        with self._lock:
            self._communication = communication

    def set_device_registry(self, device_registry) -> None:
        """Attach the authoritative device registry for device routing."""

        with self._lock:
            self._devices = device_registry

    @property
    def device_registry(self):
        """Return the attached device registry, if any."""

        with self._lock:
            return self._devices

    def has_device(self, device_id: str) -> bool:
        """Return whether a device is registered and online."""

        registry = self.device_registry
        if registry is None or not device_id:
            return False
        try:
            record = registry.get(device_id)
        except Exception:
            return False
        return record.status == "online" and record.connection_id is not None

    def route_to_device(self, message: Message) -> Message | None:
        """Route a device-to-device message via the device registry.

        Validates destination existence, online presence and an active
        connection binding, preserves ``message_id`` / ``request_id`` /
        ``identity_id`` / source / destination, and delegates delivery to
        the transport. Raises :class:`RoutingError` deterministically when
        validation fails. Static routes are not consulted.
        """

        if not isinstance(message, Message):
            raise RoutingError("Router can only route Message instances.")

        with self._lock:
            if not self._active:
                raise RoutingError("Router is not running.")
            registry = self._devices

        if registry is None:
            with self._lock:
                self._device_routing_failures += 1
            raise RoutingError("No device registry attached for device routing.")

        destination = message.destination
        if not isinstance(destination, str) or not destination.strip():
            with self._lock:
                self._device_routing_failures += 1
            raise RoutingError("INVALID_DESTINATION: destination must not be empty.")

        try:
            record = registry.get(destination)
        except Exception as exc:
            with self._lock:
                self._device_routing_failures += 1
            raise RoutingError(
                f"DEVICE_NOT_FOUND: unknown destination {destination!r}."
            ) from exc

        if record.status != "online" or record.connection_id is None:
            with self._lock:
                self._device_routing_failures += 1
            raise RoutingError(
                f"DEVICE_UNAVAILABLE: destination {destination!r} is offline."
            )

        device_message = Message(
            source=message.source,
            destination=destination,
            message_type=message.message_type,
            payload=message.payload,
            message_id=message.message_id,
            timestamp=message.timestamp,
            request_id=message.request_id,
            identity_id=message.identity_id,
        )

        # Prefer transport-native device delivery (e.g. TcpTransport socket
        # forwarding) when available; otherwise fall back to endpoint send.
        sender = getattr(self._communication, "deliver_to_device", None)
        try:
            if callable(sender):
                response = sender(device_message)
            else:
                response = self._communication.send(device_message)
        except Exception as exc:
            with self._lock:
                self._device_routing_failures += 1
            raise RoutingError(
                f"Failed to route device message to: {destination}"
            ) from exc

        with self._lock:
            self._device_messages_routed += 1

        return response

    def device_routed_count(self) -> int:
        """Return the number of successfully device-routed messages."""

        with self._lock:
            return self._device_messages_routed

    def device_failure_count(self) -> int:
        """Return the number of failed device routing attempts."""

        with self._lock:
            return self._device_routing_failures

    def clear(self) -> None:
        """Remove all configured routes."""

        with self._lock:
            self._routes.clear()

    def count(self) -> int:
        """Return the number of configured routes."""

        with self._lock:
            return len(self._routes)

    def routed_count(self) -> int:
        """Return the number of successfully routed messages."""

        with self._lock:
            return self._messages_routed

    def failure_count(self) -> int:
        """Return the number of failed routing attempts."""

        with self._lock:
            return self._routing_failures