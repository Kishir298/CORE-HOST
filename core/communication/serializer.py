import json
from datetime import datetime

from .models import Message


class MessageSerializer:
    """Serialize and deserialize C.O.R.E. messages."""

    @staticmethod
    def serialize(message: Message) -> str:
        """Convert a message to JSON."""

        data = {
            "message_id": message.message_id,
            "source": message.source,
            "destination": message.destination,
            "message_type": message.message_type,
            "timestamp": message.timestamp.isoformat(),
            "request_id": message.request_id,
            "payload": message.payload,
            "identity_id": message.identity_id,
        }

        return json.dumps(data)

    @staticmethod
    def deserialize(data: str) -> Message:
        """Convert JSON into a Message."""

        parsed = json.loads(data)

        return Message(
            message_id=parsed["message_id"],
            source=parsed["source"],
            destination=parsed["destination"],
            message_type=parsed["message_type"],
            timestamp=datetime.fromisoformat(parsed["timestamp"]),
            request_id=parsed.get("request_id"),
            payload=parsed.get("payload", {}),
            identity_id=parsed.get("identity_id"),
        )
