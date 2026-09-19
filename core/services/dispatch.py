from collections.abc import Callable
from typing import Any

from core.communication import Message
from core.security import SecurityManager, SecurityPolicy
from core.services.manager import ServiceManager
from core.services.models import ServiceRequest, ServiceResponse

EventEmitter = Callable[[str, str, dict], None]

SERVICE_ENDPOINT_PREFIX = "service:"

OPERATION_KEY = "operation"

SECURITY_ACCESS_DENIED_EVENT = "SECURITY_ACCESS_DENIED"


class ServiceDispatcher:
    """
    Bridges routed messages to service operation execution.

    A service endpoint is registered on the communication transport for
    each C.O.R.E. service. When a message is routed to that endpoint,
    ServiceDispatcher converts it into a ServiceRequest, executes the
    requested operation through ServiceManager, and converts the result
    back into a response message.

    When a SecurityManager and SecurityPolicy are supplied and the policy is
    marked as enforced, every request is authenticated and authorized before
    its operation executes. Denied requests never reach the service
    implementation. Enforcement is opt-in: until a policy is explicitly marked
    enforced, the internal trusted flow is unchanged.

    This completes the canonical flow:
        Message -> Router -> Destination -> Service -> Operation -> Response
    """

    def __init__(
        self,
        services: ServiceManager,
        security: SecurityManager | None = None,
        policy: SecurityPolicy | None = None,
        emitter: EventEmitter | None = None,
    ) -> None:
        self._services = services
        self._security = security
        self._policy = policy
        self._emitter = emitter

    def _enforcement_active(self) -> bool:
        """
        Return whether the security boundary is actively enforcing.

        A security manager and policy must be supplied AND the policy must be
        marked as enforced. This keeps enforcement opt-in so internal trusted
        flows are unchanged until a boundary is configured.
        """

        if self._security is None or self._policy is None:
            return False

        return self._policy.enforced

    def endpoint_for(self, service_id: str) -> str:
        """Return the transport endpoint that dispatches to a service."""

        return f"{SERVICE_ENDPOINT_PREFIX}{service_id}"

    def is_service_endpoint(self, destination: str) -> bool:
        """Return whether a destination is a service endpoint."""

        return destination.startswith(SERVICE_ENDPOINT_PREFIX)

    def service_id_for(self, destination: str) -> str:
        """Extract the service id from a service endpoint."""

        return destination[len(SERVICE_ENDPOINT_PREFIX):]

    def to_request(self, message: Message) -> ServiceRequest:
        """Convert a routed message into a service request."""

        service_id = self.service_id_for(message.destination)

        operation = message.payload.get(OPERATION_KEY)

        if not operation or not isinstance(operation, str):
            raise ValueError(
                "Service message payload must declare a string "
                f"'{OPERATION_KEY}'."
            )

        # Credential fields are consumed for authentication and must not be
        # forwarded as service operation arguments.
        credential_keys = {
            "_credential",
            "credential",
            "token",
            "_token",
            "api_token",
        }
        arguments = {
            key: value
            for key, value in message.payload.items()
            if key != OPERATION_KEY and key not in credential_keys
        }

        return ServiceRequest(
            service_id=service_id,
            operation=operation,
            payload=arguments,
            request_id=message.request_id or message.message_id,
        )

    def dispatch(self, request: ServiceRequest) -> ServiceResponse:
        """
        Execute a service operation and return a response.

        Operation handlers are responsible for interpreting request payload
        values. Successful execution returns success=True; failures are
        captured as error responses rather than exceptions so a response
        can always be delivered back through the routing spine.
        """

        try:
            result = self._services.execute(
                request.service_id,
                request.operation,
                **request.payload,
            )
        except Exception as exc:
            if self._emitter is not None:
                self._emitter(
                    "SERVICE_FAILED",
                    f"service:{request.service_id}",
                    {
                        "service_id": request.service_id,
                        "operation": request.operation,
                        "error": str(exc),
                    },
                )

            return ServiceResponse(
                service_id=request.service_id,
                operation=request.operation,
                success=False,
                request_id=request.request_id,
                error=str(exc),
            )

        if self._emitter is not None:
            self._emitter(
                "SERVICE_EXECUTED",
                f"service:{request.service_id}",
                {
                    "service_id": request.service_id,
                    "operation": request.operation,
                },
            )

        return ServiceResponse(
            service_id=request.service_id,
            operation=request.operation,
            payload=_coerce_payload(result),
            success=True,
            request_id=request.request_id,
        )

    def to_message(
        self,
        response: ServiceResponse,
        source: str,
        destination: str = "router",
    ) -> Message:
        """Build a response message linking the original request.

        ``destination`` should be the original requester; it defaults to
        ``"router"`` for backward compatibility. Transports deliver the
        reply over the request's own connection regardless.
        """

        return Message(
            source=source,
            destination=destination,
            message_type="SERVICE_RESPONSE",
            payload={
                "service_id": response.service_id,
                "operation": response.operation,
                "result": response.payload,
                "success": response.success,
                "error": response.error,
            },
            request_id=response.request_id,
        )

    def handle(self, message: Message) -> Message:
        """
        Transport endpoint handler: process a routed service message.

        Requests are authenticated and authorized when the security boundary is
        actively enforcing. Invalid requests are converted into error responses
        so the routing spine always delivers a structured result back to the
        caller.
        """

        if not isinstance(message.payload, dict):
            response = ServiceResponse(
                service_id=self.service_id_for(message.destination),
                operation="",
                success=False,
                request_id=message.request_id or message.message_id,
                error="Service message payload must be a dictionary.",
            )
            return self.to_message(response, source=message.destination, destination=message.source)

        try:
            request = self.to_request(message)
        except ValueError as exc:
            response = ServiceResponse(
                service_id=self.service_id_for(message.destination),
                operation="",
                success=False,
                request_id=message.request_id or message.message_id,
                error=str(exc),
            )
            return self.to_message(response, source=message.destination, destination=message.source)

        if self._enforcement_active():
            denied = self._enforce(request, message)

            if denied is not None:
                return denied

        response = self.dispatch(request)
        return self.to_message(response, source=message.destination, destination=message.source)

    def _enforce(
        self,
        request: ServiceRequest,
        message: Message,
    ) -> Message | None:
        """
        Authenticate and authorize a request against the security boundary.

        Operations without a registered permission requirement are open.
        Denied requests return an error response and never execute.
        """

        required = self._policy.required(
            request.service_id,
            request.operation,
        )

        if required is None:
            return None

        identity_id = message.identity_id

        if not identity_id:
            return self._deny(
                request,
                message,
                "Authentication is required for operation "
                f"'{request.operation}'.",
            )

        # Extract opaque credential from payload if supplied (supports Windows
        # co-hosted token flows without hardcoding secrets).
        credential = None
        if isinstance(message.payload, dict):
            for key in ("_credential", "credential", "token", "_token", "api_token"):
                if key in message.payload:
                    credential = message.payload[key]
                    break

        try:
            # Support both new (identity_id, credential) and legacy (identity_id) signatures
            try:
                self._security.authenticate(identity_id, credential)
            except TypeError:
                self._security.authenticate(identity_id)  # type: ignore
            self._security.authorize(identity_id, required)
        except Exception as exc:
            return self._deny(request, message, str(exc))

        return None

    def _deny(
        self,
        request: ServiceRequest,
        message: Message,
        reason: str,
    ) -> Message:
        """Build a denied response and emit a security event."""

        if self._emitter is not None:
            self._emitter(
                SECURITY_ACCESS_DENIED_EVENT,
                f"service:{request.service_id}",
                {
                    "service_id": request.service_id,
                    "operation": request.operation,
                    "identity_id": message.identity_id,
                    "reason": reason,
                },
            )

        response = ServiceResponse(
            service_id=request.service_id,
            operation=request.operation,
            success=False,
            request_id=request.request_id,
            error=reason,
        )

        return self.to_message(response, source=message.destination, destination=message.source)


def _coerce_payload(result: Any) -> dict[str, Any]:
    """Wrap an operation result into a serializable payload."""

    if isinstance(result, dict):
        return result

    return {"value": result}
