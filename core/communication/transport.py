from abc import ABC, abstractmethod
from collections.abc import Callable
from threading import RLock

from core.errors import MessageError

from .models import Message

MessageHandler = Callable[[Message], Message | None]


class Transport(ABC):
    """
    Abstract message transport for C.O.R.E.

    A Transport owns the physical or logical delivery of messages between
    endpoints. It is responsible for sending, receiving, connection
    lifecycle, endpoint delivery, and transport-level errors. The
    communication layer above it is responsible for message validation,
    request/response semantics, and message identity.

    C.O.R.E. subsystems depend on this interface and must never assume a
    concrete transport implementation. Delivery may be local, serial, TCP,
    network, or any future transport.
    """

    @abstractmethod
    def start(self) -> None:
        """Start the transport and accept endpoint delivery."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the transport while retaining registered endpoints."""

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """Return whether the transport is active."""

    @abstractmethod
    def register(self, endpoint: str, handler: MessageHandler) -> None:
        """Register a message handler for an endpoint."""

    @abstractmethod
    def unregister(self, endpoint: str) -> None:
        """Remove a registered endpoint."""

    @abstractmethod
    def has_endpoint(self, endpoint: str) -> bool:
        """Return whether an endpoint is registered."""

    @abstractmethod
    def endpoint_count(self) -> int:
        """Return the number of registered endpoints."""

    @abstractmethod
    def send(self, message: Message) -> Message | None:
        """Deliver a message to its destination and return any response."""

    @abstractmethod
    def request(
        self,
        source: str,
        destination: str,
        message_type: str,
        payload: dict | None = None,
    ) -> Message | None:
        """Create and deliver a request message."""

    @abstractmethod
    def message_count(self) -> int:
        """Return the number of messages delivered."""

    @abstractmethod
    def response_count(self) -> int:
        """Return the number of completed endpoint dispatches."""

    @abstractmethod
    def clear(self) -> None:
        """Remove all endpoints and reset transport state."""

    @abstractmethod
    def count(self) -> int:
        """Return the number of registered endpoints."""


class LocalTransport(Transport):
    """
    In-process message transport for C.O.R.E.

    LocalTransport delivers messages synchronously within the current
    process. It is the default transport and the reference implementation
    of the Transport contract, used by C.O.R.E. and by deterministic
    hardware simulators.
    """

    def __init__(
        self,
        on_delivery: Callable[[Message], None] | None = None,
    ) -> None:
        self._handlers: dict[str, MessageHandler] = {}
        self._lock = RLock()

        self._active = True
        self._messages_sent = 0
        self._messages_received = 0
        self._on_delivery = on_delivery

    def start(self) -> None:
        """Start the transport."""

        with self._lock:
            if self._active:
                return

            self._active = True

    def stop(self) -> None:
        """
        Stop the transport.

        Registered endpoints are retained so the transport can be restarted
        without requiring every component to register again.
        """

        with self._lock:
            self._active = False

    @property
    def is_running(self) -> bool:
        """Return whether the transport is active."""

        with self._lock:
            return self._active

    def register(self, endpoint: str, handler: MessageHandler) -> None:
        """Register a message handler for an endpoint."""

        if not endpoint:
            raise MessageError(
                "Communication endpoint cannot be empty."
            )

        if not callable(handler):
            raise MessageError(
                f"Handler for endpoint '{endpoint}' is not callable."
            )

        with self._lock:
            if endpoint in self._handlers:
                raise MessageError(
                    f"Endpoint already registered: {endpoint}"
                )

            self._handlers[endpoint] = handler

    def unregister(self, endpoint: str) -> None:
        """Remove a registered endpoint."""

        with self._lock:
            self._handlers.pop(endpoint, None)

    def has_endpoint(self, endpoint: str) -> bool:
        """Return whether an endpoint is registered."""

        with self._lock:
            return endpoint in self._handlers

    def endpoint_count(self) -> int:
        """Return the number of registered endpoints."""

        with self._lock:
            return len(self._handlers)

    def send(self, message: Message) -> Message | None:
        """
        Deliver a message to its destination endpoint.

        The destination handler is executed synchronously and may return
        a response Message.
        """

        if not isinstance(message, Message):
            raise MessageError(
                "Communication can only send Message instances."
            )

        with self._lock:
            if not self._active:
                raise MessageError(
                    "Communication layer is not running."
                )

            handler = self._handlers.get(message.destination)

            if handler is None:
                raise MessageError(
                    f"Destination not registered: {message.destination}"
                )

            self._messages_sent += 1

        try:
            response = handler(message)
        except Exception as exc:
            raise MessageError(
                f"Message handling failed for destination: "
                f"{message.destination}"
            ) from exc

        with self._lock:
            self._messages_received += 1

        if response is not None and not isinstance(response, Message):
            raise MessageError(
                "Communication handlers must return a Message or None."
            )

        if self._on_delivery is not None:
            try:
                self._on_delivery(message)
            except Exception:
                pass

        return response

    def request(
        self,
        source: str,
        destination: str,
        message_type: str,
        payload: dict | None = None,
    ) -> Message | None:
        """
        Create and deliver a request message.

        This provides a higher-level request API while keeping Message as
        the standard communication object.
        """

        message = Message(
            source=source,
            destination=destination,
            message_type=message_type,
            payload=payload or {},
        )

        return self.send(message)

    def message_count(self) -> int:
        """Return the total number of successfully delivered messages."""

        with self._lock:
            return self._messages_sent

    def response_count(self) -> int:
        """Return the number of completed handler dispatches."""

        with self._lock:
            return self._messages_received

    def clear(self) -> None:
        """Remove all registered endpoints and reset transport counters."""

        with self._lock:
            self._handlers.clear()
            self._messages_sent = 0
            self._messages_received = 0

    def count(self) -> int:
        """Return the number of registered endpoints."""

        return self.endpoint_count()
