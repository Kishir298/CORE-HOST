"""C.O.R.E. data organizer.

Coordinates one DATA_REQUEST end to end: validation, authorization,
owner-scoped R.E.S.C.S. retrieval, normalization, deterministic ordering,
pagination, response packaging and size validation. Thread-safe; all
mutable counters hold one ``RLock``.

C.O.R.E. never stores records or files here — every byte comes from the
injected :class:`RescsDataReader` factory bound to the requesting owner.
"""

from __future__ import annotations

import base64
import hashlib
from threading import RLock
from typing import Any, Callable

from core.communication.models import Message
from core.communication.protocol import (
    COMMUNICATION_ERROR,
    DATA_ACCESS_DENIED,
    DATA_ERROR,
    DATA_NOT_FOUND,
    DATA_RESPONSE,
    DATA_RESPONSE_TOO_LARGE,
    DATA_RETRIEVAL_FAILED,
    DATA_SOURCE_UNAVAILABLE,
    DESTINATION_UNAVAILABLE,
    FILE_TRANSFER_REQUIRED,
    INVALID_DATA_REQUEST,
    INVALID_DESTINATION,
    MAX_FILE_METADATA_RESPONSE_BYTES,
    MAX_RECORD_RESPONSE_BYTES,
    REQUEST_FILE_DOWNLOAD,
    REQUEST_FILE_METADATA,
    REQUEST_RECORD_GET,
    REQUEST_RECORD_LIST,
    REQUEST_RECORD_SEARCH,
    build_device_error,
)
from core.communication.serializer import MessageSerializer

from .errors import DataError
from .normalize import (
    normalize_file_metadata,
    normalize_record,
    order_records,
    paginate,
)
from .requests import ParsedRequest, validate_data_request
from .rescs_reader import RescsDataReader

ReaderFactory = Callable[[str | None, bool], RescsDataReader]


class DataOrganizer:
    """Stateless-per-request coordinator with aggregated metrics."""

    def __init__(
        self,
        reader_factory: ReaderFactory,
        security_manager: Any | None = None,
        device_registry: Any | None = None,
        event_bus: Any | None = None,
    ) -> None:
        self._reader_factory = reader_factory
        self._security = security_manager
        self._devices = device_registry
        self._events = event_bus
        self._lock = RLock()
        self._data_requests = 0
        self._data_requests_successful = 0
        self._data_requests_failed = 0
        self._data_records_retrieved = 0
        self._data_files_retrieved = 0
        self._data_access_denied = 0
        self._data_source_failures = 0
        self._data_response_too_large = 0
        self._data_messages_sent = 0

    # -- metrics -----------------------------------------------------------
    def data_metrics(self) -> dict[str, int]:
        """Return the data-layer observability snapshot."""
        with self._lock:
            return {
                "data_requests": self._data_requests,
                "data_requests_successful": self._data_requests_successful,
                "data_requests_failed": self._data_requests_failed,
                "data_records_retrieved": self._data_records_retrieved,
                "data_files_retrieved": self._data_files_retrieved,
                "data_access_denied": self._data_access_denied,
                "data_source_failures": self._data_source_failures,
                "data_response_too_large": self._data_response_too_large,
                "data_messages_sent": self._data_messages_sent,
            }

    def _count(self, name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + amount)

    def _emit(self, event_type: str, payload: dict) -> None:
        bus = self._events
        if bus is None:
            return
        try:
            emit = getattr(bus, "emit", None)
            if callable(emit):
                emit(event_type, "data", payload)
        except Exception:
            pass

    # -- entry point ---------------------------------------------------------
    def handle_request(
        self,
        *,
        payload: object,
        sender_device_id: str,
        correlation_id: str | None,
        message_id: str | None,
        identity_id: str | None,
    ) -> tuple[Message, str]:
        """Process one DATA_REQUEST.

        Returns ``(response_message, destination_device_id)`` where the
        destination is the authenticated sender unless a valid
        ``destination_device_id`` was supplied. The caller (transport)
        performs the actual socket delivery.
        """
        self._count("_data_requests")
        self._emit("DATA_REQUEST_RECEIVED", {"device_id": sender_device_id})
        request_ref = correlation_id or message_id

        parsed, error = (
            validate_data_request(payload) if isinstance(payload, dict)
            else (None, (INVALID_DATA_REQUEST, "Invalid DATA_REQUEST: payload must be a JSON object."))
        )
        if error is not None or parsed is None:
            code, text = error or (INVALID_DATA_REQUEST, "Invalid DATA_REQUEST.")
            return self._fail(
                sender_device_id, request_ref, message_id, identity_id, code, text
            )

        assert parsed is not None
        # -- authorization (before any retrieval) ---------------------------
        allowed, auth_error = self._authorize(sender_device_id, parsed)
        if not allowed:
            self._count("_data_access_denied")
            self._emit(
                "DATA_ACCESS_DENIED",
                {"device_id": sender_device_id, "request_type": parsed.request_type},
            )
            code, text = auth_error or (DATA_ACCESS_DENIED, "Access denied.")
            return self._fail(
                sender_device_id, request_ref, message_id, identity_id, code, text
            )

        # -- destination validation ------------------------------------------
        target = parsed.destination_device_id or sender_device_id
        dest_error = self._validate_destination(target, sender_device_id)
        if dest_error is not None:
            code, text = dest_error
            return self._fail(
                sender_device_id, request_ref, message_id, identity_id, code, text
            )

        # -- retrieval ---------------------------------------------------------
        try:
            data_payload, records, files = self._retrieve(sender_device_id, parsed)
        except DataError as exc:
            code = exc.code or DATA_RETRIEVAL_FAILED
            if code == DATA_SOURCE_UNAVAILABLE:
                self._count("_data_source_failures")
            self._emit(
                "DATA_RETRIEVAL_FAILURE",
                {"device_id": sender_device_id, "error": code},
            )
            return self._fail(
                sender_device_id, request_ref, message_id, identity_id,
                code, self._public_message(code, str(exc)),
            )
        except Exception:
            self._emit(
                "DATA_RETRIEVAL_FAILURE",
                {"device_id": sender_device_id, "error": DATA_RETRIEVAL_FAILED},
            )
            return self._fail(
                sender_device_id, request_ref, message_id, identity_id,
                DATA_RETRIEVAL_FAILED, "Data retrieval failed.",
            )

        # -- packaging + size validation ---------------------------------------
        try:
            response_payload, budget = self._package(parsed, data_payload)
        except DataError as exc:
            return self._fail(
                sender_device_id, request_ref, message_id, identity_id,
                exc.code, str(exc) or "Data response packaging failed.",
            )
        message = Message(
            source="core",
            destination=target,
            message_type=DATA_RESPONSE,
            payload=response_payload,
            request_id=message_id,
            identity_id=identity_id,
        )
        size_error = self._check_size(message, budget)
        if size_error is not None:
            self._count("_data_response_too_large")
            return self._fail(
                sender_device_id, request_ref, message_id, identity_id,
                DATA_RESPONSE_TOO_LARGE, size_error,
            )

        self._count("_data_requests_successful")
        self._count("_data_records_retrieved", records)
        self._count("_data_files_retrieved", files)
        self._count("_data_messages_sent")
        self._emit(
            "DATA_RETRIEVAL_SUCCESS",
            {"device_id": sender_device_id, "destination": target},
        )
        self._emit(
            "DATA_RESPONSE_SENT",
            {"device_id": target, "request_id": request_ref},
        )
        return message, target

    # -- authorization ---------------------------------------------------------
    def _authorize(
        self, sender: str, parsed: ParsedRequest
    ) -> tuple[bool, tuple[str, str] | None]:
        """Check identity, READ permission and owner scope."""
        cross_owner = False
        if self._security is not None:
            try:
                identity = self._security.get_identity(sender)
            except Exception:
                return False, (DATA_ACCESS_DENIED, "Access denied.")
            try:
                from core.security.models import Permission

                permissions = getattr(identity, "permissions", frozenset()) or frozenset()
                if Permission.READ not in set(permissions):
                    return False, (DATA_ACCESS_DENIED, "Access denied.")
                cross_owner = Permission.ADMIN in set(permissions)
            except Exception:
                return False, (DATA_ACCESS_DENIED, "Access denied.")
        if (
            parsed.owner is not None
            and parsed.owner != sender
            and not cross_owner
        ):
            return False, (DATA_ACCESS_DENIED, "Access denied for requested owner.")
        return True, None

    def _validate_destination(
        self, target: str, sender: str
    ) -> tuple[str, str] | None:
        """Validate an explicit distribution target. None when valid."""
        if target == sender:
            return None
        registry = self._devices
        if registry is None:
            return (INVALID_DESTINATION, "Unknown destination device.")
        try:
            has = registry.has(target)
        except Exception:
            has = False
        if not has:
            return (INVALID_DESTINATION, "Unknown destination device.")
        try:
            record = registry.get(target)
            online = (
                getattr(record, "status", "") == "online"
                and getattr(record, "connection_id", None) is not None
            )
        except Exception:
            online = False
        if not online:
            return (DESTINATION_UNAVAILABLE, "Destination device is unavailable.")
        return None

    # -- retrieval ---------------------------------------------------------------
    def _retrieve(
        self, sender: str, parsed: ParsedRequest
    ) -> tuple[Any, int, int]:
        """Run the R.E.S.C.S. read. Returns (data, record_count, file_count)."""
        allow_cross = parsed.owner is not None and parsed.owner != sender
        reader = self._reader_factory(sender, allow_cross)
        rtype = parsed.request_type
        if rtype == REQUEST_RECORD_GET:
            assert parsed.namespace is not None and parsed.key is not None
            record = reader.get_record(parsed.namespace, parsed.key, parsed.owner)
            return normalize_record(record), 1, 0
        if rtype == REQUEST_RECORD_LIST:
            records = reader.list_records(parsed.namespace, parsed.key_prefix, parsed.owner)
            return self._collection(records, parsed), len(records), 0
        if rtype == REQUEST_RECORD_SEARCH:
            assert parsed.query is not None
            records = reader.search_records(
                parsed.query, parsed.namespace, parsed.key_prefix, parsed.owner
            )
            return self._collection(records, parsed), len(records), 0
        if rtype == REQUEST_FILE_METADATA:
            assert parsed.file_id is not None
            return normalize_file_metadata(reader.get_file_metadata(parsed.file_id)), 0, 1
        if rtype == REQUEST_FILE_DOWNLOAD:
            assert parsed.file_id is not None
            return self._download(reader, parsed.file_id)
        raise DataError(f"Unknown request_type: {rtype!r}.", code=INVALID_DATA_REQUEST)

    def _collection(
        self, records: list[dict], parsed: ParsedRequest
    ) -> dict[str, Any]:
        """Normalize + order + paginate a record collection."""
        from core.communication.protocol import MAX_DATA_ITEMS

        normalized = [normalize_record(r) for r in records]
        ordered = order_records(normalized)
        page = paginate(ordered, parsed.limit, parsed.offset)
        if len(page["items"]) > MAX_DATA_ITEMS:
            raise DataError(
                "Response exceeds the maximum data size.",
                code=DATA_RESPONSE_TOO_LARGE,
            )
        return page

    def _download(self, reader: RescsDataReader, file_id: str) -> tuple[Any, int, int]:
        """Fetch, integrity-check and bound one file download."""
        from core.communication.protocol import MAX_INLINE_FILE_BYTES

        meta, content = reader.get_file_bytes(file_id)
        normalized = normalize_file_metadata(meta)
        expected_size = normalized.get("size")
        if isinstance(expected_size, int) and expected_size != len(content):
            raise DataError("File integrity validation failed.", code=DATA_RETRIEVAL_FAILED)
        expected_sha = normalized.get("sha256")
        if isinstance(expected_sha, str) and expected_sha:
            actual_sha = hashlib.sha256(content).hexdigest()
            if actual_sha.lower() != expected_sha.lower():
                raise DataError(
                    "File integrity validation failed.", code=DATA_RETRIEVAL_FAILED
                )
        if len(content) > MAX_INLINE_FILE_BYTES:
            raise DataError(
                f"File {file_id!r} exceeds the inline transfer limit; "
                "out-of-band transfer is required.",
                code=FILE_TRANSFER_REQUIRED,
            )
        return (
            {
                "data_type": "file_content",
                "item": normalized,
                "content_base64": base64.b64encode(content).decode("ascii"),
                "sha256": normalized.get("sha256"),
                "size": len(content),
            },
            0,
            1,
        )

    # -- packaging -----------------------------------------------------------------
    def _package(
        self, parsed: ParsedRequest, data: Any
    ) -> tuple[dict[str, Any], int]:
        """Wrap organized data in the exact DATA_RESPONSE shape + byte budget."""
        rtype = parsed.request_type
        if rtype in (REQUEST_RECORD_LIST, REQUEST_RECORD_SEARCH):
            return (
                {
                    "data_type": "records",
                    "items": data["items"],
                    "total": data["total"],
                    "limit": data["limit"],
                    "offset": data["offset"],
                },
                MAX_RECORD_RESPONSE_BYTES,
            )
        if rtype == REQUEST_RECORD_GET:
            return ({"data_type": "record", "item": data}, MAX_RECORD_RESPONSE_BYTES)
        if rtype == REQUEST_FILE_METADATA:
            return (
                {"data_type": "file_metadata", "item": data},
                MAX_FILE_METADATA_RESPONSE_BYTES,
            )
        if rtype == REQUEST_FILE_DOWNLOAD:
            if isinstance(data, dict) and data.get("data_type") == "file_content":
                return data, MAX_RECORD_RESPONSE_BYTES
            return (
                {"data_type": "file_metadata", "item": data},
                MAX_FILE_METADATA_RESPONSE_BYTES,
            )
        raise DataError("Cannot package response.", code=DATA_RETRIEVAL_FAILED)

    def _check_size(self, message: Message, budget: int) -> str | None:
        """Serialize and enforce budgets. Returns an error string or None."""
        try:
            encoded = MessageSerializer.serialize(message).encode("utf-8")
        except Exception:
            return "Data response serialization failed."
        if len(encoded) > budget:
            return "Data response exceeds the maximum allowed size."
        try:
            from core.communication.tcp import MAX_FRAME_SIZE

            if len(encoded) > MAX_FRAME_SIZE:
                return "Data response exceeds the maximum allowed size."
        except Exception:
            pass
        return None

    # -- failures ---------------------------------------------------------------------
    @staticmethod
    def _public_message(code: str, detail: str) -> str:
        """Map internal detail to a safe human-readable message."""
        mapping = {
            DATA_NOT_FOUND: "Requested data was not found.",
            DATA_ACCESS_DENIED: "Access denied.",
            DATA_SOURCE_UNAVAILABLE: "Data source is unavailable.",
            DATA_RETRIEVAL_FAILED: "Data retrieval failed.",
            COMMUNICATION_ERROR: "Communication failure.",
        }
        if code in mapping:
            return mapping[code]
        text = (detail or "").strip()
        if not text or "Traceback" in text or 'File "' in text:
            return "Data request failed."
        return text if len(text) <= 300 else text[:297] + "..."

    def _fail(
        self,
        sender: str,
        request_ref: str | None,
        message_id: str | None,
        identity_id: str | None,
        code: str,
        text: str,
    ) -> tuple[Message, str]:
        """Build a DATA_ERROR addressed to the sender."""
        self._count("_data_requests_failed")
        message = Message(
            source="core",
            destination=sender,
            message_type=DATA_ERROR,
            payload=build_device_error(code, text, request_ref),
            request_id=message_id,
            identity_id=identity_id,
        )
        return message, sender


__all__ = ["DataOrganizer", "ReaderFactory"]
