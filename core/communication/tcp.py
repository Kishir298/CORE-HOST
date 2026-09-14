from __future__ import annotations

import hmac
import socket
import struct
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from core.errors import MessageError

from .connection import ConnectionSession, ConnectionState
from .devices import DeviceRegistry, DeviceRegistryError
from .models import Message
from .protocol import (
    COMMUNICATION_ERROR,
    DATA_ERROR,
    DATA_REQUEST,
    DATA_RESPONSE,
    DEVICE_ALREADY_REGISTERED,
    DEVICE_DISCOVER,
    DEVICE_DISCOVER_RESPONSE,
    DEVICE_ERROR,
    DEVICE_INFO,
    DEVICE_INFO_RESPONSE,
    DEVICE_NOT_FOUND,
    DEVICE_NOT_REGISTERED,
    DEVICE_REGISTER,
    DEVICE_REGISTER_RESPONSE,
    DEVICE_REGISTRATION_FAILED,
    DEVICE_STATUS_OFFLINE,
    DEVICE_STATUS_ONLINE,
    DEVICE_UNAVAILABLE,
    INVALID_DESTINATION,
    MINIMUM_TLS_VERSION,
    SUPPORTED_PROTOCOL_VERSION,
    build_device_error,
    validate_registration_payload,
)
from .serializer import MessageSerializer
from .transport import MessageHandler, Transport

# Fixed protocol limits (NOT configurable per hardening spec).
MAX_FRAME_SIZE = 10 * 1024 * 1024
MAX_CONNECTIONS = 64
HEADER_SIZE = 4
TLS_HANDSHAKE_TIMEOUT = 5.0
IDLE_CONNECTION_TIMEOUT = 300.0

# Authoritative connection lease for external-device sessions (24 hours).
# Configurable via ``communication.connection_lease_seconds``; this constant
# is the single default path so the value is never hardcoded twice.
CONNECTION_LEASE_SECONDS = 24 * 60 * 60

HANDSHAKE_TYPE = "CORE_HANDSHAKE"
HANDSHAKE_RESPONSE_TYPE = "CORE_HANDSHAKE_RESPONSE"


class TcpTransport(Transport):
    """
    TCP message transport for Windows co-hosted deployment.

    Binds to ``127.0.0.1`` by default. External binding to ``0.0.0.0``
    requires TLS (fail closed — no plaintext downgrade) and per-connection
    handshake + authentication before application messages.

    Localhost operation without an injected ``security_manager`` preserves
    the legacy framing path so existing local tests keep passing.
    When a ``security_manager`` is injected (as ``CoreApplication`` does),
    every socket connection must complete::

        CORE_HANDSHAKE -> authenticate -> SESSION_ESTABLISHED

    before any application message is routed. Connections are persistent
    and support multiple messages until disconnect / idle timeout /
    protocol violation / shutdown.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        on_delivery: Callable[[Message], None] | None = None,
        use_tls: bool = False,
        certfile: str | Path | None = None,
        keyfile: str | Path | None = None,
        cafile: str | Path | None = None,
        require_client_cert: bool = False,
        security_manager: Any | None = None,
        version_negotiator: Callable[[str | None], str] | None = None,
        version_supported: Callable[[str], bool] | None = None,
        time_func: Callable[[], float] | None = None,
        device_registry: DeviceRegistry | None = None,
        event_bus: Any | None = None,
        data_organizer: Any | None = None,
        connection_lease_seconds: int | float | None = None,
        log_external_tokens: bool = False,
        logger: Any | None = None,
    ) -> None:
        from threading import RLock

        raw_host = (host or "127.0.0.1").strip()
        if raw_host == "":
            raw_host = "127.0.0.1"
        self._host = raw_host
        self._port = int(port) if isinstance(port, int) else 0
        if self._port < 0 or self._port > 65535:
            raise ValueError(f"TCP port out of range: {self._port}")
        self._on_delivery = on_delivery

        self._use_tls = bool(use_tls)
        self._certfile = Path(certfile) if certfile else None
        self._keyfile = Path(keyfile) if keyfile else None
        self._cafile = Path(cafile) if cafile else None
        self._require_client_cert = bool(require_client_cert)
        self._tls_active = False
        self._ssl_context = None  # type: ignore

        # External binding mandates TLS — fail closed at construction.
        if self.is_external and not self._use_tls:
            raise ValueError(
                "External TCP (0.0.0.0) requires TLS: "
                "set use_tls=True with a valid certfile."
            )
        if self._use_tls:
            try:
                self._ssl_context = self._build_ssl_context()
                self._tls_active = True
            except Exception as exc:
                if self.is_external:
                    # FAIL CLOSED for external devices: never downgrade.
                    raise
                # Localhost legacy fallback: plaintext with warning state.
                self._ssl_context = None
                self._tls_active = False
                _ = exc

        self._security_manager = security_manager
        # External binding mandates credential-based auth — fail closed.
        # Localhost keeps legacy behavior (handshake disabled without a
        # security manager; existence provider tolerated).
        if self.is_external:
            provider = (
                getattr(security_manager, "provider", None)
                if security_manager is not None
                else None
            )
            provider_name = (
                type(provider).__name__ if provider is not None else "None"
            )
            if security_manager is None or provider_name in (
                "ExistenceAuthenticationProvider",
                "None",
            ):
                raise ValueError(
                    "External TCP (0.0.0.0) requires a credential-based "
                    "authentication provider (TokenAuthenticationProvider); "
                    f"got {provider_name}."
                )
        self._version_negotiator = version_negotiator
        self._version_supported = version_supported
        if self._version_negotiator is None or self._version_supported is None:
            try:
                from core.version import is_supported as _is_supported
                from core.version import negotiate as _negotiate

                if self._version_negotiator is None:
                    self._version_negotiator = _negotiate
                if self._version_supported is None:
                    self._version_supported = _is_supported
            except Exception:
                pass
        self._time = time_func or time.monotonic

        self._handlers: dict[str, MessageHandler] = {}
        self._lock = RLock()
        self._active = True
        self._messages_sent = 0
        self._messages_received = 0

        # Hardened session registry + metrics.
        # _reserved counts connections admitted but not yet registered,
        # so reserved + active can never exceed MAX_CONNECTIONS.
        self._connections: dict[str, ConnectionSession] = {}
        self._reserved = 0
        self._total_connections = 0
        self._rejected_connections = 0
        self._authentication_failures = 0
        self._protocol_failures = 0

        # Authoritative device layer (one record per device_id).
        self._devices = device_registry or DeviceRegistry()
        self._event_bus = event_bus
        # Data organization layer (R.E.S.C.S. retrieval + packaging).
        # None means no data backend is configured for this transport.
        self._data_organizer = data_organizer
        # device_id -> [conn socket, write RLock, connection_id]
        self._device_sockets: dict[str, list] = {}
        self._device_registration_failures = 0
        self._device_routing_failures = 0
        self._device_discovery_requests = 0
        self._device_messages_routed = 0
        self._lease_expirations = 0

        # Authoritative connection lease (host-enforced, per connection).
        # Falls back to CONNECTION_LEASE_SECONDS when unset/invalid.
        try:
            lease_value = (
                float(connection_lease_seconds)
                if connection_lease_seconds is not None
                else float(CONNECTION_LEASE_SECONDS)
            )
        except (TypeError, ValueError):
            lease_value = float(CONNECTION_LEASE_SECONDS)
        if lease_value <= 0:
            lease_value = float(CONNECTION_LEASE_SECONDS)
        self._lease_seconds = lease_value

        # Plaintext token logging is strictly opt-in (development only).
        self._log_external_tokens = bool(log_external_tokens)
        if logger is not None:
            self._logger = logger
        else:  # pragma: no cover - production path injects CoreLogger
            import logging as _logging

            self._logger = _logging.getLogger("core.communication.tcp")

        self._server_socket: socket.socket | None = None
        self._server_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # -- properties ------------------------------------------------------

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def is_external(self) -> bool:
        """Return whether the transport is bound for external LAN access."""
        return self._host == "0.0.0.0"

    @property
    def is_tls(self) -> bool:
        """Return whether TLS is configured and active."""
        return self._tls_active

    @property
    def uses_tls(self) -> bool:
        """Return whether TLS was requested (may be inactive if fallback)."""
        return self._use_tls

    @property
    def hardened(self) -> bool:
        """Return whether per-connection handshake/auth is enforced."""
        return self._security_manager is not None

    def _build_ssl_context(self):  # type: ignore
        """Build an SSLContext for the TLS listener (TLS 1.2+ only)."""
        import ssl

        if self._certfile is not None and not self._certfile.exists():
            raise FileNotFoundError(f"TLS certfile not found: {self._certfile}")
        if self._keyfile is not None and not self._keyfile.exists():
            raise FileNotFoundError(f"TLS keyfile not found: {self._keyfile}")
        if self._cafile is not None and not self._cafile.exists():
            raise FileNotFoundError(f"TLS cafile not found: {self._cafile}")

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        except Exception:
            ctx.options |= getattr(ssl, "OP_NO_TLSv1", 0) | getattr(
                ssl, "OP_NO_TLSv1_1", 0
            )
        if self._certfile:
            ctx.load_cert_chain(
                certfile=str(self._certfile),
                keyfile=str(self._keyfile) if self._keyfile else None,
            )
        else:
            raise FileNotFoundError("TLS certfile not configured for TLS mode")

        if self._require_client_cert and self._cafile:
            ctx.verify_mode = ssl.CERT_REQUIRED
            ctx.load_verify_locations(cafile=str(self._cafile))
        elif self._cafile:
            ctx.verify_mode = ssl.CERT_OPTIONAL
            ctx.load_verify_locations(cafile=str(self._cafile))

        return ctx

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Start the transport and optional TCP listener."""
        needs_server = False
        with self._lock:
            if not self._active:
                self._active = True
                self._stop_event.clear()
                needs_server = self._port != 0 and self._server_socket is None
            else:
                needs_server = self._port != 0 and self._server_socket is None

        if needs_server:
            # Fail-closed errors propagate for external; localhost keeps
            # legacy best-effort fallback.
            try:
                self._start_server()
            except Exception:
                if self.is_external:
                    raise
                pass

    def stop(self) -> None:
        """Stop the transport, close sessions, retain endpoints."""
        with self._lock:
            self._active = False
            self._stop_event.set()
        self._close_all_connections()
        self._stop_server()
        try:
            self._devices.mark_all_offline()
        except Exception:
            pass
        with self._lock:
            self._device_sockets.clear()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._active

    # -- endpoints (unchanged public API) --------------------------------

    def register(self, endpoint: str, handler: MessageHandler) -> None:
        if not endpoint:
            raise MessageError("Communication endpoint cannot be empty.")
        if not callable(handler):
            raise MessageError(f"Handler for endpoint '{endpoint}' is not callable.")
        with self._lock:
            if endpoint in self._handlers:
                raise MessageError(f"Endpoint already registered: {endpoint}")
            self._handlers[endpoint] = handler

    def unregister(self, endpoint: str) -> None:
        with self._lock:
            self._handlers.pop(endpoint, None)

    def has_endpoint(self, endpoint: str) -> bool:
        with self._lock:
            return endpoint in self._handlers

    def endpoint_count(self) -> int:
        with self._lock:
            return len(self._handlers)

    def send(self, message: Message) -> Message | None:
        if not isinstance(message, Message):
            raise MessageError("Communication can only send Message instances.")

        with self._lock:
            if not self._active:
                raise MessageError("Communication layer is not running.")
            handler = self._handlers.get(message.destination)
            if handler is None:
                raise MessageError(f"Destination not registered: {message.destination}")
            self._messages_sent += 1

        try:
            serialized = MessageSerializer.serialize(message)
            deserialized = MessageSerializer.deserialize(serialized)
            response = handler(deserialized)
        except MessageError:
            raise
        except Exception as exc:
            raise MessageError(
                f"Message handling failed for destination: {message.destination}"
            ) from exc

        with self._lock:
            self._messages_received += 1

        if response is not None and not isinstance(response, Message):
            raise MessageError("Communication handlers must return a Message or None.")

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
        message = Message(
            source=source,
            destination=destination,
            message_type=message_type,
            payload=payload or {},
        )
        return self.send(message)

    def message_count(self) -> int:
        with self._lock:
            return self._messages_sent

    def response_count(self) -> int:
        with self._lock:
            return self._messages_received

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()
            self._messages_sent = 0
            self._messages_received = 0
            try:
                for _sess in list(self._connections.values()):
                    try:
                        _sess.clear_session_token()
                    except Exception:
                        pass
            except Exception:
                pass
            self._connections.clear()
            self._reserved = 0
            self._total_connections = 0
            self._rejected_connections = 0
            self._authentication_failures = 0
            self._protocol_failures = 0
            self._device_sockets.clear()
            self._device_registration_failures = 0
            self._device_routing_failures = 0
            self._device_discovery_requests = 0
            self._device_messages_routed = 0
            self._lease_expirations = 0
        try:
            self._devices.clear()
        except Exception:
            pass

    def count(self) -> int:
        return self.endpoint_count()

    # -- session registry + metrics (additive API) ------------------------

    def get_connection(self, connection_id: str) -> ConnectionSession | None:
        with self._lock:
            return self._connections.get(connection_id)

    def list_connections(self) -> list[ConnectionSession]:
        with self._lock:
            return list(self._connections.values())

    def connection_count(self) -> int:
        with self._lock:
            return len(self._connections)

    def active_connections(self) -> int:
        return self.connection_count()

    def total_connections(self) -> int:
        with self._lock:
            return self._total_connections

    def rejected_connections(self) -> int:
        with self._lock:
            return self._rejected_connections

    def authentication_failures(self) -> int:
        with self._lock:
            return self._authentication_failures

    def protocol_failures(self) -> int:
        with self._lock:
            return self._protocol_failures

    # -- device registry + presence + metrics (additive API) ---------------

    @property
    def device_registry(self) -> DeviceRegistry:
        """Return the authoritative device registry."""
        return self._devices

    def registered_devices(self) -> int:
        """Return the number of registered devices (online + offline)."""
        try:
            return self._devices.registered_count()
        except Exception:
            return 0

    def online_devices(self) -> int:
        """Return the number of online devices."""
        try:
            return self._devices.online_count()
        except Exception:
            return 0

    def offline_devices(self) -> int:
        """Return the number of offline devices."""
        try:
            return self._devices.offline_count()
        except Exception:
            return 0

    def device_registration_failures(self) -> int:
        with self._lock:
            return self._device_registration_failures

    def device_routing_failures(self) -> int:
        with self._lock:
            return self._device_routing_failures

    def device_discovery_requests(self) -> int:
        with self._lock:
            return self._device_discovery_requests

    def device_messages_routed(self) -> int:
        with self._lock:
            return self._device_messages_routed

    def device_metrics(self) -> dict:
        """Return the device-layer observability snapshot."""
        with self._lock:
            routing_failures = self._device_routing_failures
            registration_failures = self._device_registration_failures
            discovery_requests = self._device_discovery_requests
            messages_routed = self._device_messages_routed
            lease_expirations = self._lease_expirations
        snapshot = {
            "registered_devices": self.registered_devices(),
            "online_devices": self.online_devices(),
            "offline_devices": self.offline_devices(),
            "device_registration_failures": registration_failures,
            "device_routing_failures": routing_failures,
            "device_discovery_requests": discovery_requests,
            "device_messages_routed": messages_routed,
            "lease_expirations": lease_expirations,
        }
        organizer = self._data_organizer
        metrics_fn = getattr(organizer, "data_metrics", None)
        if callable(metrics_fn):
            try:
                for key, value in dict(metrics_fn()).items():
                    snapshot.setdefault(key, value)
            except Exception:
                pass
        return snapshot

    @property
    def data_organizer(self) -> Any | None:
        """Return the attached data organizer, if any."""
        return self._data_organizer

    def set_data_organizer(self, organizer: Any | None) -> None:
        """Attach or replace the data organization layer."""
        with self._lock:
            self._data_organizer = organizer

    def get_device(self, device_id: str) -> dict | None:
        """Return the discovery entry for a device, or None."""
        try:
            return self._devices.get(device_id).to_discovery_entry()
        except Exception:
            return None

    def deliver_to_device(self, message: Message) -> None:
        """Deliver a device-addressed message to its live socket.

        Used by :meth:`Router.route_to_device` for in-process device
        delivery. Validates destination existence / online presence /
        active binding and preserves message identity. Raises
        :class:`MessageError` deterministically when undeliverable.
        """
        from core.errors import MessageError as _MessageError

        destination = message.destination
        if not isinstance(destination, str) or not destination.strip():
            raise _MessageError("INVALID_DESTINATION: destination must not be empty.")
        try:
            record = self._devices.get(destination)
        except Exception as exc:
            raise _MessageError(
                f"DEVICE_NOT_FOUND: unknown destination {destination!r}."
            ) from exc
        with self._lock:
            binding = self._device_sockets.get(destination)
            binding_cid = binding[2] if binding else None
        if (
            record.status != DEVICE_STATUS_ONLINE
            or record.connection_id is None
            or binding is None
            or binding_cid != record.connection_id
        ):
            raise _MessageError(
                f"DEVICE_UNAVAILABLE: destination {destination!r} is offline."
            )
        dest_conn, dest_lock, _dest_cid = binding
        try:
            with dest_lock:
                text = MessageSerializer.serialize(message)
                data = text.encode("utf-8")
                if len(data) == 0 or len(data) > MAX_FRAME_SIZE:
                    raise _MessageError("Forwarded frame size invalid.")
                dest_conn.sendall(struct.pack("!I", len(data)) + data)
        except _MessageError:
            raise
        except Exception as exc:
            raise _MessageError("Failed to deliver device message.") from exc
        with self._lock:
            self._device_messages_routed += 1
        return None

    def list_devices(self, *, include_offline: bool = True) -> list[dict]:
        """Return discovery entries for registered devices."""
        try:
            return [
                record.to_discovery_entry()
                for record in self._devices.list_devices(
                    include_offline=include_offline
                )
            ]
        except Exception:
            return []

    def _register_session(
        self, session: ConnectionSession, has_reservation: bool = False
    ) -> bool:
        """Register a session if capacity allows. Returns True if admitted.

        When has_reservation is True the caller already owns one reserved
        slot (via try_reserve_slot); registration consumes it. Direct
        callers without a reservation are capped against active + reserved.
        """
        with self._lock:
            if session.connection_id in self._connections:
                return True
            if has_reservation:
                if self._reserved > 0:
                    self._reserved -= 1
                if len(self._connections) >= MAX_CONNECTIONS:
                    self._rejected_connections += 1
                    return False
            else:
                if len(self._connections) + self._reserved >= MAX_CONNECTIONS:
                    self._rejected_connections += 1
                    return False
            self._connections[session.connection_id] = session
            self._total_connections += 1
            return True

    def try_reserve_slot(self) -> bool:
        """Atomically reserve one connection slot before expensive work.

        Returns True when the caller owns a reservation; False when the
        64-slot budget (active + reserved) is exhausted. Rejections
        increment rejected_connections exactly once here.
        """
        with self._lock:
            if len(self._connections) + self._reserved >= MAX_CONNECTIONS:
                self._rejected_connections += 1
                return False
            self._reserved += 1
            return True

    def release_reservation(self, count_rejection: bool = False) -> None:
        """Release a previously reserved slot (e.g. TLS/handshake failure)."""
        with self._lock:
            if self._reserved > 0:
                self._reserved -= 1
            if count_rejection:
                self._rejected_connections += 1

    def reserved_slots(self) -> int:
        """Return admitted-but-not-yet-registered slot count."""
        with self._lock:
            return self._reserved

    def _remove_session(self, connection_id: str) -> None:
        with self._lock:
            session = self._connections.pop(connection_id, None)
            if session is not None:
                try:
                    session.clear_session_token()
                except Exception:
                    pass
                session.transition(ConnectionState.CLOSED)

    def _close_all_connections(self) -> None:
        with self._lock:
            ids = list(self._connections.keys())
            self._reserved = 0
        for cid in ids:
            self._remove_session(cid)

    # -- TCP server --------------------------------------------------------

    def _start_server(self) -> None:
        if self._server_socket is not None:
            return

        # External binding mandates active TLS — fail closed.
        if self.is_external:
            if not self._use_tls:
                raise ValueError("External TCP (0.0.0.0) requires TLS.")
            if self._ssl_context is None:
                try:
                    self._ssl_context = self._build_ssl_context()
                except Exception:
                    self._tls_active = False
                    raise
                else:
                    self._tls_active = True
            if not self._tls_active or self._ssl_context is None:
                raise ValueError("External TCP requires active TLS context.")
            is_tls = True
        else:
            is_tls = False
            if self._use_tls:
                if self._ssl_context is None:
                    try:
                        self._ssl_context = self._build_ssl_context()
                    except Exception:
                        self._tls_active = False
                        self._ssl_context = None
                    else:
                        self._tls_active = True
                is_tls = self._tls_active and self._ssl_context is not None
            else:
                self._tls_active = False

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except Exception:
            pass

        bind_host = self._host if self._host else "127.0.0.1"
        sock.bind((bind_host, self._port))
        actual_port = sock.getsockname()[1]
        self._port = actual_port
        sock.listen(5)
        sock.settimeout(1.0)
        self._server_socket = sock

        def _accept_loop() -> None:
            while not self._stop_event.is_set():
                try:
                    conn, addr = sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break

                # Atomic admission: reserve before expensive TLS work so a
                # concurrent burst can never admit more than 64 total.
                if not self.try_reserve_slot():
                    try:
                        conn.close()
                    except Exception:
                        pass
                    continue

                remote = f"{addr[0]}:{addr[1]}" if addr else ""
                if is_tls and self._ssl_context is not None:
                    try:
                        conn.settimeout(TLS_HANDSHAKE_TIMEOUT)
                        conn = self._ssl_context.wrap_socket(
                            conn, server_side=True, do_handshake_on_connect=True
                        )
                    except Exception:
                        # TLS failure releases the reservation (no session);
                        # count the rejection exactly once.
                        self.release_reservation(count_rejection=True)
                        try:
                            conn.close()
                        except Exception:
                            pass
                        continue

                threading.Thread(
                    target=self._handle_connection,
                    args=(conn, remote, is_tls, True),
                    daemon=True,
                ).start()

        self._server_thread = threading.Thread(target=_accept_loop, daemon=True)
        self._server_thread.start()

    def _stop_server(self) -> None:
        sock = self._server_socket
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
            self._server_socket = None
        thread = self._server_thread
        if thread is not None:
            try:
                thread.join(timeout=1.0)
            except Exception:
                pass
            self._server_thread = None

    # -- connection handling ----------------------------------------------

    def _handle_connection(
        self,
        conn: socket.socket,
        remote_address: str = "",
        is_tls: bool = False,
        has_reservation: bool = False,
    ) -> None:
        now = self._time()
        session = ConnectionSession(
            remote_address=remote_address,
            connected_at=now,
            last_activity=now,
        )
        session.transition(
            ConnectionState.TLS_ESTABLISHED if is_tls else ConnectionState.CONNECTED
        )
        if not self._register_session(session, has_reservation=has_reservation):
            try:
                conn.close()
            except Exception:
                pass
            return
        try:
            if self._security_manager is not None:
                self._serve_hardened(conn, session)
            else:
                self._serve_legacy(conn, session)
        finally:
            try:
                self._cleanup_device_binding(session)
            except Exception:
                pass
            try:
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                conn.close()
            except Exception:
                pass
            session.transition(ConnectionState.CLOSING)
            self._remove_session(session.connection_id)

    def _serve_legacy(self, conn: socket.socket, session: ConnectionSession) -> None:
        """Persistent legacy framing loop (no handshake) for localhost compat."""
        try:
            while not self._stop_event.is_set():
                if not self._wait_readable(conn, session):
                    return
                frame = self._recv_frame(conn, session)
                if frame is None:
                    return
                try:
                    message = MessageSerializer.deserialize(frame)
                except Exception:
                    with self._lock:
                        self._protocol_failures += 1
                    return
                try:
                    self._validate_message_shape(message)
                except MessageError:
                    with self._lock:
                        self._protocol_failures += 1
                    return
                session.messages_received += 1
                session.touch(self._time())
                try:
                    response = self.send(message)
                except MessageError:
                    with self._lock:
                        self._protocol_failures += 1
                    return
                if response is not None:
                    session.messages_sent += 1
                    self._send_frame(conn, response)
                    session.touch(self._time())
        except Exception:
            with self._lock:
                self._protocol_failures += 1
            return

    def _serve_hardened(self, conn: socket.socket, session: ConnectionSession) -> None:
        """Handshake -> authenticate -> persistent validated message loop."""
        try:
            session.transition(ConnectionState.AUTHENTICATING)
            if not self._wait_readable(conn, session):
                with self._lock:
                    self._protocol_failures += 1
                return
            frame = self._recv_frame(conn, session)
            if frame is None:
                with self._lock:
                    self._protocol_failures += 1
                return
            try:
                hello = MessageSerializer.deserialize(frame)
            except Exception:
                with self._lock:
                    self._protocol_failures += 1
                return
            ok = self._process_handshake(conn, session, hello)
            if not ok:
                return
            # Authenticated message loop.
            while not self._stop_event.is_set():
                if not self._wait_readable(conn, session):
                    return
                frame = self._recv_frame(conn, session)
                if frame is None:
                    return
                try:
                    message = MessageSerializer.deserialize(frame)
                except Exception:
                    with self._lock:
                        self._protocol_failures += 1
                    return
                if message.message_type == HANDSHAKE_TYPE:
                    # Duplicate handshake is a protocol violation.
                    with self._lock:
                        self._protocol_failures += 1
                    return
                try:
                    self._validate_message_shape(message)
                    self._enforce_identity(message, session)
                except MessageError:
                    with self._lock:
                        self._protocol_failures += 1
                    return
                # Transport credential: strip the session token before any
                # application, routing, echo, or peer delivery so it never
                # leaks into app payloads, forwarded messages, or logs.
                try:
                    if isinstance(message.payload, dict):
                        message.payload.pop("_session_token", None)
                except Exception:
                    pass
                session.messages_received += 1
                session.touch(self._time())
                # Device-layer protocol interception. Returns True when the
                # message was fully handled (response already framed).
                # Returns "close" when the connection must be terminated
                # after the error response (protocol/security violation).
                try:
                    disposition = self._dispatch_device_message(conn, session, message)
                except Exception:
                    with self._lock:
                        self._protocol_failures += 1
                    return
                if disposition == "close":
                    return
                if disposition == "handled":
                    continue
                try:
                    response = self.send(message)
                except MessageError:
                    # Registered devices get a graceful device error for
                    # unroutable destinations; unregistered senders keep the
                    # legacy strict close behavior.
                    if self._is_session_device_registered(session):
                        self._record_routing_failure()
                        self._send_device_error(
                            conn,
                            session,
                            message,
                            DEVICE_NOT_FOUND,
                            "Destination device was not found.",
                        )
                        continue
                    with self._lock:
                        self._protocol_failures += 1
                    return
                if response is not None:
                    session.messages_sent += 1
                    self._send_frame(conn, response)
                    session.touch(self._time())
        except Exception:
            with self._lock:
                self._protocol_failures += 1
            return

    def _process_handshake(
        self, conn: socket.socket, session: ConnectionSession, hello: Message
    ) -> bool:
        """Validate handshake, authenticate, reply. Returns True on success."""
        if hello.message_type != HANDSHAKE_TYPE:
            # Application message before handshake.
            with self._lock:
                self._protocol_failures += 1
            return False
        payload = hello.payload if isinstance(hello.payload, dict) else None
        if payload is None:
            with self._lock:
                self._protocol_failures += 1
            return False
        identity_id = payload.get("identity_id")
        credential = payload.get("credential")
        protocol_version = payload.get("protocol_version")
        if not identity_id or credential is None or not protocol_version:
            with self._lock:
                self._protocol_failures += 1
            return False
        if not isinstance(identity_id, str) or not isinstance(protocol_version, str):
            with self._lock:
                self._protocol_failures += 1
            return False
        # Version validation via existing infrastructure.
        try:
            supported = (
                self._version_supported(str(protocol_version))
                if self._version_supported is not None
                else True
            )
            negotiated = (
                self._version_negotiator(str(protocol_version))
                if self._version_negotiator is not None
                else str(protocol_version)
            )
        except Exception:
            with self._lock:
                self._protocol_failures += 1
            return False
        if not supported:
            with self._lock:
                self._protocol_failures += 1
            return False
        # External auth must not use existence-only provider, and the
        # identity must have a credential/token configured: existence
        # alone never authenticates an external connection.
        provider = getattr(self._security_manager, "provider", None)
        provider_name = type(provider).__name__ if provider is not None else ""
        join_label = self._join_label(payload, str(identity_id))
        self._log_device_auth(
            "attempt",
            device_id=str(identity_id),
            join_name=join_label,
            credential=credential,
        )
        if provider_name == "ExistenceAuthenticationProvider":
            with self._lock:
                self._authentication_failures += 1
            self._log_device_auth(
                "failed",
                device_id=str(identity_id),
                join_name=join_label,
                credential=credential,
            )
            return False
        try:
            identity = self._security_manager.get_identity(str(identity_id))
        except Exception:
            with self._lock:
                self._authentication_failures += 1
            self._log_device_auth(
                "failed",
                device_id=str(identity_id),
                join_name=join_label,
                credential=credential,
            )
            return False
        metadata = getattr(identity, "metadata", {}) or {}
        try:
            has_token = any(
                key in metadata
                for key in ("token", "credential", "api_token", "password")
            )
        except Exception:
            has_token = False
        if not has_token:
            with self._lock:
                self._authentication_failures += 1
            self._log_device_auth(
                "failed",
                device_id=str(identity_id),
                join_name=join_label,
                credential=credential,
            )
            return False
        try:
            self._security_manager.authenticate(str(identity_id), credential)
        except Exception:
            with self._lock:
                self._authentication_failures += 1
            self._log_device_auth(
                "failed",
                device_id=str(identity_id),
                join_name=join_label,
                credential=credential,
            )
            return False
        session.mark_authenticated(str(identity_id), self._time())
        # Temporary session credential: cryptographically random, per
        # connection, memory-only. The provisioning credential is used ONLY
        # to reach this point and is never returned or reused as the session.
        session_token = session.rotate_session_token()
        session.messages_received += 1
        lease = self._stamp_lease(session)
        self._log_device_auth(
            "success",
            device_id=str(identity_id),
            join_name=join_label,
            connection_id=session.connection_id,
        )
        response = Message(
            source="core",
            destination=str(identity_id),
            message_type=HANDSHAKE_RESPONSE_TYPE,
            payload={
                "authenticated": True,
                "identity_id": str(identity_id),
                "protocol_version": negotiated,
                "connection_id": session.connection_id,
                "session_token": session_token,
                "connected_at": lease["connected_at"],
                "lease_expires_at": lease["lease_expires_at"],
                "lease_duration_seconds": lease["lease_duration_seconds"],
            },
            identity_id=str(identity_id),
        )
        try:
            self._send_frame(conn, response)
        except Exception:
            with self._lock:
                self._protocol_failures += 1
            return False
        session.messages_sent += 1
        return True

    # -- validation --------------------------------------------------------

    @staticmethod
    def _validate_message_shape(message: Message) -> None:
        if (
            not message.message_id
            or not message.source
            or not message.destination
            or not message.message_type
            or message.timestamp is None
            or not isinstance(message.payload, dict)
        ):
            raise MessageError("Malformed message.")

    @staticmethod
    def _enforce_identity(message: Message, session: ConnectionSession) -> None:
        if session.identity_id is None or not session.authenticated:
            raise MessageError("Unauthenticated session.")
        if message.identity_id != session.identity_id:
            raise MessageError("identity_id mismatch.")
        if message.source != session.identity_id:
            raise MessageError("source mismatch.")
        # Temporary session binding: the presented session token must match
        # the active session exactly (constant-time compare). Provisioning
        # credentials are never accepted here — only the host-issued token.
        if session.session_token is None:
            raise MessageError("Session token missing.")
        presented = None
        try:
            if isinstance(message.payload, dict):
                presented = message.payload.get("_session_token")
        except Exception:
            presented = None
        if not isinstance(presented, str) or not presented:
            raise MessageError("Session token missing.")
        try:
            if not hmac.compare_digest(presented, session.session_token):
                raise MessageError("Session token mismatch.")
        except MessageError:
            raise
        except Exception:
            raise MessageError("Session token mismatch.")

    # -- device protocol -----------------------------------------------------

    def _is_session_device_registered(self, session: ConnectionSession) -> bool:
        """Return whether this connection completed DEVICE_REGISTER."""
        try:
            record = self._devices.find_by_connection(session.connection_id)
        except Exception:
            return False
        return record is not None and record.status == DEVICE_STATUS_ONLINE

    def _record_registration_failure(self) -> None:
        with self._lock:
            self._device_registration_failures += 1
            self._protocol_failures += 1

    def _record_routing_failure(self) -> None:
        with self._lock:
            self._device_routing_failures += 1

    # -- external-device auth logging + connection lease -------------------

    def _token_display(self, credential: Any) -> str:
        """Render a credential for logs: plaintext only when explicitly enabled."""
        if (
            self._log_external_tokens
            and isinstance(credential, str)
            and credential
        ):
            return credential
        return "<REDACTED>"

    @staticmethod
    def _join_label(payload: dict | None, identity_id: str) -> str:
        """Best-effort display label for auth logs (never a trust decision)."""
        if isinstance(payload, dict):
            candidate = payload.get("join_name")
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()[:64]
        return str(identity_id)

    def _log_device_auth(
        self,
        outcome: str,
        *,
        device_id: str,
        join_name: str,
        credential: Any = None,
        connection_id: str | None = None,
    ) -> None:
        """Log an external-device authentication attempt/result.

        The supplied token appears in plaintext ONLY when
        ``log_external_tokens`` is enabled (development testing); otherwise
        it is rendered as ``<REDACTED>``. Never raises.
        """
        try:
            token = self._token_display(credential)
            if outcome == "attempt":
                self._logger.info(
                    "External device login attempt "
                    f"device_id={device_id} join_name={join_name} token={token}"
                )
            elif outcome == "failed":
                self._logger.warning(
                    "External device authentication failed "
                    f"device_id={device_id} join_name={join_name} token={token}"
                )
            elif outcome == "success":
                self._logger.info(
                    "External device authenticated "
                    f"device_id={device_id} join_name={join_name} "
                    f"connection_id={connection_id}"
                )
        except Exception:
            pass

    def _stamp_lease(self, session: ConnectionSession) -> dict[str, Any]:
        """Start the authoritative lease at authentication and describe it.

        Enforcement uses the monotonic ``session.lease_expires_at``; the
        returned ISO wall-clock triple is what clients track locally.
        """
        now = self._time()
        session.start_lease(self._lease_seconds, now)
        wall = datetime.now(timezone.utc)
        connected_iso = wall.isoformat()
        expires_iso = (wall + timedelta(seconds=self._lease_seconds)).isoformat()
        session.connected_iso = connected_iso
        session.lease_expires_iso = expires_iso
        return {
            "connected_at": connected_iso,
            "lease_expires_at": expires_iso,
            "lease_duration_seconds": int(self._lease_seconds),
        }

    def _session_lease_payload(self, session: ConnectionSession) -> dict[str, Any]:
        """Return the lease triple + session token for the session.

        Restamps the lease only if missing (registration must NOT restart
        the authentication-anchored window). The session token is the
        temporary host-issued credential, never the provisioning secret.
        """
        if session.connected_iso is None or session.lease_expires_iso is None:
            leased = self._stamp_lease(session)
        else:
            leased = {
                "connected_at": session.connected_iso,
                "lease_expires_at": session.lease_expires_iso,
                "lease_duration_seconds": int(self._lease_seconds),
            }
        if session.session_token is not None:
            leased["session_token"] = session.session_token
        return leased

    def _record_lease_expiration(self) -> None:
        with self._lock:
            self._lease_expirations += 1

    def _emit_device_event(
        self, event_type: str, device_id: str, extra: dict | None = None
    ) -> None:
        bus = self._event_bus
        if bus is None:
            return
        try:
            payload = {"device_id": device_id, "resource_id": device_id}
            if extra:
                payload.update(extra)
            emit = getattr(bus, "emit", None)
            if callable(emit):
                emit(event_type, "communication", payload)
        except Exception:
            pass

    def _send_message_frame(
        self, conn: socket.socket, session: ConnectionSession, message: Message
    ) -> bool:
        """Frame one message to a socket. Returns False on transport failure."""
        try:
            self._send_frame(conn, message)
        except Exception:
            return False
        try:
            session.messages_sent += 1
            session.touch(self._time())
        except Exception:
            pass
        return True

    def _send_device_error(
        self,
        conn: socket.socket,
        session: ConnectionSession,
        request: Message,
        error_code: str,
        human_message: str,
    ) -> None:
        """Send a DEVICE_ERROR envelope preserving the request ID."""
        request_id = request.request_id or request.message_id
        try:
            destination = session.identity_id or request.source
        except Exception:
            destination = request.source
        error_message = Message(
            source="core",
            destination=destination,
            message_type=DEVICE_ERROR,
            payload=build_device_error(error_code, human_message, request_id),
            request_id=request.message_id,
            identity_id=request.identity_id,
        )
        self._send_message_frame(conn, session, error_message)

    def _dispatch_device_message(
        self, conn: socket.socket, session: ConnectionSession, message: Message
    ) -> str | None:
        """Handle device-protocol and device-routed messages.

        Returns ``"handled"`` when a response was framed, ``"close"`` when
        the connection must be terminated after the response, or ``None``
        when the caller should fall back to endpoint dispatch.
        """
        mtype = message.message_type
        if mtype == DEVICE_REGISTER:
            return self._handle_device_register(conn, session, message)
        if mtype in (DEVICE_DISCOVER, DEVICE_INFO):
            return self._handle_device_query(conn, session, message)
        if mtype == DATA_REQUEST:
            return self._handle_data_request(conn, session, message)
        if mtype in (DEVICE_ERROR, DATA_ERROR, DATA_RESPONSE):
            # Never route envelopes or server responses as app traffic.
            return "handled"
        # Device-to-device routing: destination names a known device.
        try:
            known = self._devices.has(message.destination)
        except Exception:
            known = False
        if known:
            return self._handle_device_routed(conn, session, message)
        # Registered sender naming an unknown destination gets a graceful
        # device error instead of a legacy connection teardown.
        if message.destination and self._is_session_device_registered(session):
            with self._lock:
                has_endpoint = message.destination in self._handlers
            if not has_endpoint:
                self._record_routing_failure()
                if not message.destination.strip():
                    self._send_device_error(
                        conn, session, message,
                        INVALID_DESTINATION, "Destination must not be empty.",
                    )
                else:
                    self._send_device_error(
                        conn, session, message,
                        DEVICE_NOT_FOUND, "Destination device was not found.",
                    )
                return "handled"
        return None

    def _identity_token(self, identity_id: str) -> str | None:
        """Return the stored auth token for an identity, if any."""
        manager = self._security_manager
        if manager is None:
            return None
        try:
            identity = manager.get_identity(identity_id)
        except Exception:
            return None
        try:
            metadata = dict(getattr(identity, "metadata", {}) or {})
        except Exception:
            return None
        for key in ("token", "credential", "api_token", "password"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _identity_permissions(self, identity_id: str) -> list[str]:
        """Return the permission names for an identity, if known."""
        manager = self._security_manager
        if manager is None:
            return []
        try:
            identity = manager.get_identity(identity_id)
            permissions = getattr(identity, "permissions", None) or []
            names = []
            for permission in permissions:
                value = getattr(permission, "value", permission)
                if isinstance(value, str) and value:
                    names.append(value)
            return names
        except Exception:
            return []

    def _handle_device_register(
        self, conn: socket.socket, session: ConnectionSession, message: Message
    ) -> str:
        """Process DEVICE_REGISTER. Returns 'handled' or 'close'."""
        payload = message.payload if isinstance(message.payload, dict) else None
        if payload is None:
            self._record_registration_failure()
            self._send_device_error(
                conn, session, message,
                DEVICE_REGISTRATION_FAILED, "Invalid payload structure.",
            )
            return "close"
        code, detail = validate_registration_payload(payload)
        if code is not None:
            self._record_registration_failure()
            self._send_device_error(
                conn, session, message, code, detail or "Invalid registration."
            )
            return "close"
        device_id = payload["device_id"]
        # Authenticated identity MUST equal the registering device identity.
        if session.identity_id != device_id:
            self._record_registration_failure()
            with self._lock:
                self._authentication_failures += 1
            self._send_device_error(
                conn, session, message,
                DEVICE_REGISTRATION_FAILED,
                "Authenticated identity does not match device_id.",
            )
            return "close"
        try:
            record = self._devices.register_payload(
                payload,
                identity_id=session.identity_id,
                connection_id=session.connection_id,
            )
        except DeviceRegistryError as exc:
            self._record_registration_failure()
            err_code = exc.code or DEVICE_REGISTRATION_FAILED
            self._send_device_error(conn, session, message, err_code, str(exc))
            # Duplicate active registration and validation failures both
            # terminate this connection without touching the live binding.
            return "close"
        except Exception:
            self._record_registration_failure()
            self._send_device_error(
                conn, session, message,
                DEVICE_REGISTRATION_FAILED, "Device registration failed.",
            )
            return "close"
        # Bind the live socket for device-to-device delivery.
        from threading import RLock as _RLock

        with self._lock:
            self._device_sockets[device_id] = [conn, _RLock(), session.connection_id]
        try:
            self._devices.update_last_seen(device_id)
        except Exception:
            pass
        # Persist the identity (with credentials) so it survives restarts.
        # Best-effort: persist_identity never raises.
        try:
            self._devices.persist_identity(
                device_id,
                token=self._identity_token(device_id),
                permissions=self._identity_permissions(device_id),
            )
        except Exception:
            pass
        self._emit_device_event(
            "DEVICE_CONNECTED",
            device_id,
            {
                "device_name": record.device_name,
                "connection_id": session.connection_id,
                "identity_id": session.identity_id,
            },
        )
        response = Message(
            source="core",
            destination=device_id,
            message_type=DEVICE_REGISTER_RESPONSE,
            payload={
                "registered": True,
                "device_id": device_id,
                "status": DEVICE_STATUS_ONLINE,
                "join_name": record.join_name,
                **self._session_lease_payload(session),
            },
            request_id=message.message_id,
            identity_id=message.identity_id,
        )
        self._send_message_frame(conn, session, response)
        return "handled"

    def _handle_device_query(
        self, conn: socket.socket, session: ConnectionSession, message: Message
    ) -> str:
        """Process DEVICE_DISCOVER / DEVICE_INFO. Returns 'handled'."""
        if not self._is_session_device_registered(session):
            self._record_routing_failure()
            self._send_device_error(
                conn, session, message,
                DEVICE_NOT_REGISTERED,
                "Device must register before discovery.",
            )
            return "handled"
        if message.message_type == DEVICE_DISCOVER:
            with self._lock:
                self._device_discovery_requests += 1
            try:
                self._devices.update_last_seen(session.identity_id or "")
            except Exception:
                pass
            try:
                devices = [
                    record.to_discovery_entry()
                    for record in self._devices.list_devices(include_offline=True)
                ]
            except Exception:
                devices = []
            response = Message(
                source="core",
                destination=session.identity_id or message.source,
                message_type=DEVICE_DISCOVER_RESPONSE,
                payload={"devices": devices},
                request_id=message.message_id,
                identity_id=message.identity_id,
            )
            self._send_message_frame(conn, session, response)
            return "handled"
        # DEVICE_INFO
        payload = message.payload if isinstance(message.payload, dict) else {}
        target = payload.get("device_id") if isinstance(payload, dict) else None
        with self._lock:
            self._device_discovery_requests += 1
        if not isinstance(target, str) or not target.strip():
            self._record_routing_failure()
            self._send_device_error(
                conn, session, message,
                INVALID_DESTINATION, "device_id must not be empty.",
            )
            return "handled"
        try:
            record = self._devices.get(target)
        except Exception:
            self._record_routing_failure()
            self._send_device_error(
                conn, session, message,
                DEVICE_NOT_FOUND, "Destination device was not found.",
            )
            return "handled"
        try:
            self._devices.update_last_seen(session.identity_id or "")
        except Exception:
            pass
        response = Message(
            source="core",
            destination=session.identity_id or message.source,
            message_type=DEVICE_INFO_RESPONSE,
            payload={"device": record.to_info_entry()},
            request_id=message.message_id,
            identity_id=message.identity_id,
        )
        self._send_message_frame(conn, session, response)
        return "handled"

    def _handle_data_request(
        self, conn: socket.socket, session: ConnectionSession, message: Message
    ) -> str:
        """Process DATA_REQUEST via the data organizer. Returns 'handled'.

        Data errors never tear down the connection. Distribution to another
        device reuses the live socket binding with the same connection-id
        match guarantee as device routing.
        """
        from .protocol import DATA_ERROR as _DATA_ERROR
        from .protocol import DEVICE_NOT_REGISTERED as _NOT_REGISTERED

        sender = session.identity_id or ""
        if not self._is_session_device_registered(session):
            self._send_data_envelope(
                conn, session, message,
                _DATA_ERROR, _NOT_REGISTERED,
                "Device must register before requesting data.",
            )
            return "handled"
        organizer = self._data_organizer
        if organizer is None:
            from .protocol import DATA_SOURCE_UNAVAILABLE as _UNAVAILABLE

            self._send_data_envelope(
                conn, session, message,
                _DATA_ERROR, _UNAVAILABLE, "Data service is not configured.",
            )
            return "handled"
        try:
            response, target = organizer.handle_request(
                payload=message.payload,
                sender_device_id=sender,
                correlation_id=message.request_id or message.message_id,
                message_id=message.message_id,
                identity_id=message.identity_id,
            )
        except Exception:
            from .protocol import COMMUNICATION_ERROR as _COMM_ERROR

            self._send_data_envelope(
                conn, session, message,
                _DATA_ERROR, _COMM_ERROR, "Data request failed.",
            )
            return "handled"
        if target == sender:
            self._send_message_frame(conn, session, response)
            session.touch(self._time())
            try:
                self._devices.update_last_seen(sender)
            except Exception:
                pass
            return "handled"
        # Distribution: forward the packaged response to the target device.
        with self._lock:
            binding = self._device_sockets.get(target)
            binding_cid = binding[2] if binding else None
        try:
            record = self._devices.get(target)
            live = (
                record.status == DEVICE_STATUS_ONLINE
                and record.connection_id is not None
                and binding is not None
                and binding_cid == record.connection_id
            )
        except Exception:
            live = False
        if not live:
            from .protocol import DESTINATION_UNAVAILABLE as _DEST_DOWN

            self._send_data_envelope(
                conn, session, message,
                _DATA_ERROR, _DEST_DOWN,
                "Destination device is unavailable.",
            )
            with self._lock:
                self._device_routing_failures += 1
            return "handled"
        dest_conn, dest_lock, _dest_cid = binding
        try:
            with dest_lock:
                text = MessageSerializer.serialize(response)
                data = text.encode("utf-8")
                if len(data) == 0 or len(data) > MAX_FRAME_SIZE:
                    raise MessageError("Forwarded frame size invalid.")
                dest_conn.sendall(struct.pack("!I", len(data)) + data)
        except Exception:
            from .protocol import COMMUNICATION_ERROR as _COMM_ERROR

            self._send_data_envelope(
                conn, session, message,
                _DATA_ERROR, _COMM_ERROR, "Failed to deliver data response.",
            )
            with self._lock:
                self._device_routing_failures += 1
            return "handled"
        with self._lock:
            self._device_messages_routed += 1
        try:
            now = datetime.now(timezone.utc)
            self._devices.update_last_seen(sender, now)
            self._devices.update_last_seen(target, now)
        except Exception:
            pass
        session.touch(self._time())
        return "handled"

    def _send_data_envelope(
        self,
        conn: socket.socket,
        session: ConnectionSession,
        request: Message,
        envelope_type: str,
        error_code: str,
        human_message: str,
    ) -> None:
        """Frame a DATA_ERROR envelope preserving the request ID."""
        request_id = request.request_id or request.message_id
        try:
            destination = session.identity_id or request.source
        except Exception:
            destination = request.source
        error_message = Message(
            source="core",
            destination=destination,
            message_type=envelope_type,
            payload=build_device_error(error_code, human_message, request_id),
            request_id=request.message_id,
            identity_id=request.identity_id,
        )
        self._send_message_frame(conn, session, error_message)

    def _handle_device_routed(
        self, conn: socket.socket, session: ConnectionSession, message: Message
    ) -> str:
        """Forward a device-to-device message. Returns 'handled'."""
        # Check 6: source must be the authenticated source identity
        # (already enforced upstream; re-checked defensively).
        if message.source != session.identity_id or message.identity_id != session.identity_id:
            self._record_routing_failure()
            with self._lock:
                self._protocol_failures += 1
            self._send_device_error(
                conn, session, message,
                COMMUNICATION_ERROR, "Source identity mismatch.",
            )
            return "handled"
        # Sender must be registered.
        if not self._is_session_device_registered(session):
            self._record_routing_failure()
            self._send_device_error(
                conn, session, message,
                DEVICE_NOT_REGISTERED,
                "Device must register before sending messages.",
            )
            return "handled"
        destination = message.destination
        # Check 1: destination must not be empty.
        if not isinstance(destination, str) or not destination.strip():
            self._record_routing_failure()
            self._send_device_error(
                conn, session, message,
                INVALID_DESTINATION, "Destination must not be empty.",
            )
            return "handled"
        # Check 2: destination must exist.
        try:
            record = self._devices.get(destination)
        except Exception:
            self._record_routing_failure()
            self._send_device_error(
                conn, session, message,
                DEVICE_NOT_FOUND, "Destination device was not found.",
            )
            return "handled"
        # Check 3/4: online with an active connection.
        with self._lock:
            binding = self._device_sockets.get(destination)
            binding_cid = binding[2] if binding else None
        if (
            record.status != DEVICE_STATUS_ONLINE
            or record.connection_id is None
            or binding is None
            or binding_cid != record.connection_id
        ):
            self._record_routing_failure()
            self._send_device_error(
                conn, session, message,
                DEVICE_UNAVAILABLE, "Destination device is unavailable.",
            )
            return "handled"
        # Check 5: binding corresponds to the registered device (verified
        # above via connection_id equality).
        forwarded = Message(
            source=message.source,
            destination=message.destination,
            message_type=message.message_type,
            payload=dict(message.payload) if isinstance(message.payload, dict) else {},
            message_id=message.message_id,
            timestamp=message.timestamp,
            request_id=message.request_id,
            identity_id=message.identity_id,
        )
        dest_conn, dest_lock, _dest_cid = binding
        try:
            with dest_lock:
                text = MessageSerializer.serialize(forwarded)
                data = text.encode("utf-8")
                if len(data) == 0 or len(data) > MAX_FRAME_SIZE:
                    raise MessageError("Forwarded frame size invalid.")
                dest_conn.sendall(struct.pack("!I", len(data)) + data)
        except Exception:
            self._record_routing_failure()
            self._send_device_error(
                conn, session, message,
                COMMUNICATION_ERROR, "Failed to deliver message.",
            )
            return "handled"
        with self._lock:
            self._device_messages_routed += 1
        try:
            now = datetime.now(timezone.utc)
            self._devices.update_last_seen(message.source, now)
            self._devices.update_last_seen(destination, now)
        except Exception:
            pass
        session.touch(self._time())
        return "handled"

    def _cleanup_device_binding(self, session: ConnectionSession) -> None:
        """Mark the session's device offline (race-safe) and free its slot."""
        record = None
        try:
            record = self._devices.find_by_connection(session.connection_id)
        except Exception:
            record = None
        if record is None:
            return
        device_id = record.device_id
        connection_id = session.connection_id
        with self._lock:
            binding = self._device_sockets.get(device_id)
            if binding is not None and binding[2] != connection_id:
                # A newer connection replaced this one; leave it alone.
                return
            if binding is not None:
                self._device_sockets.pop(device_id, None)
        try:
            self._devices.mark_offline(device_id, connection_id)
        except Exception:
            pass
        self._emit_device_event(
            "DEVICE_DISCONNECTED",
            device_id,
            {"connection_id": connection_id},
        )

    # -- framing I/O -------------------------------------------------------

    def _wait_readable(self, conn: socket.socket, session: ConnectionSession) -> bool:
        """Wait until data is available, idle timeout, or lease expiry.

        Lease expiry is authoritative: an expired connection never becomes
        readable again and the serve loop tears it down (offline + event +
        socket close) via the standard cleanup path.
        """
        import select

        while not self._stop_event.is_set():
            now = self._time()
            if session.is_lease_expired(now):
                self._record_lease_expiration()
                return False
            remaining = IDLE_CONNECTION_TIMEOUT - (now - session.last_activity)
            lease_remaining = session.lease_remaining(now)
            if lease_remaining is not None and lease_remaining < remaining:
                remaining = lease_remaining
            if remaining <= 0:
                return False
            try:
                r, _, _ = select.select([conn], [], [], min(1.0, remaining))
            except Exception:
                return False
            if r:
                return True
        return False

    def _recv_frame(self, conn: socket.socket, session: ConnectionSession) -> str | None:
        header = self._recv_exact(conn, HEADER_SIZE, session)
        if header is None:
            return None
        (length,) = struct.unpack("!I", header)
        if length <= 0 or length > MAX_FRAME_SIZE:
            return None
        data = self._recv_exact(conn, length, session)
        if data is None:
            return None
        try:
            return data.decode("utf-8")
        except Exception:
            return None

    def _recv_exact(
        self, conn: socket.socket, n: int, session: ConnectionSession | None = None
    ) -> bytes | None:
        buf = b""
        while len(buf) < n:
            if session is not None:
                if session.is_lease_expired(self._time()):
                    self._record_lease_expiration()
                    return None
                if self._time() - session.last_activity > IDLE_CONNECTION_TIMEOUT:
                    return None
            try:
                chunk = conn.recv(n - len(buf))
            except socket.timeout:
                if session is not None and (
                    session.is_lease_expired(self._time())
                    or self._time() - session.last_activity > IDLE_CONNECTION_TIMEOUT
                ):
                    if session.is_lease_expired(self._time()):
                        self._record_lease_expiration()
                    return None
                continue
            except OSError:
                return None
            if not chunk:
                return None
            buf += chunk
            if session is not None:
                session.touch(self._time())
        return buf

    def _send_frame(self, conn: socket.socket, message: Message) -> None:
        text = MessageSerializer.serialize(message)
        payload = text.encode("utf-8")
        if len(payload) == 0 or len(payload) > MAX_FRAME_SIZE:
            raise MessageError("Response frame size invalid.")
        conn.sendall(struct.pack("!I", len(payload)) + payload)


__all__ = [
    "TcpTransport",
    "MAX_FRAME_SIZE",
    "MAX_CONNECTIONS",
    "HEADER_SIZE",
    "TLS_HANDSHAKE_TIMEOUT",
    "IDLE_CONNECTION_TIMEOUT",
    "MINIMUM_TLS_VERSION",
    "SUPPORTED_PROTOCOL_VERSION",
    "HANDSHAKE_TYPE",
    "HANDSHAKE_RESPONSE_TYPE",
    "DEVICE_REGISTER",
    "DEVICE_REGISTER_RESPONSE",
    "DEVICE_DISCOVER",
    "DEVICE_DISCOVER_RESPONSE",
    "DEVICE_INFO",
    "DEVICE_INFO_RESPONSE",
    "DEVICE_ERROR",
]
