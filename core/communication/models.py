from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class Message:
    """Standard C.O.R.E. communication message."""

    source: str
    destination: str
    message_type: str
    payload: dict[str, Any] = field(default_factory=dict)

    message_id: str = field(default_factory=lambda: str(uuid4()))

    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    request_id: str | None = None

    identity_id: str | None = None

    def create_response(
        self,
        source: str,
        payload: dict[str, Any] | None = None,
        message_type: str = "RESPONSE",
    ) -> "Message":
        """Create a response associated with this message."""

        return Message(
            source=source,
            destination=self.source,
            message_type=message_type,
            payload=payload or {},
            request_id=self.message_id,
            identity_id=self.identity_id,
        )
