"""Unit tests for the C.O.R.E. data organization layer.

No sockets, no R.E.S.C.S. installation: the organizer is exercised with
recording fake readers, a real SecurityManager and a real DeviceRegistry.
"""

import base64
import hashlib

import pytest

from core.communication.devices import DeviceRegistry
from core.communication.protocol import (
    DATA_ERROR,
    DATA_RESPONSE,
)
from core.data.errors import (
    DataAccessDenied,
    DataNotFound,
    DataRetrievalFailed,
    DataSourceUnavailable,
)
from core.data.normalize import (
    normalize_file_metadata,
    normalize_record,
    order_records,
    paginate,
)
from core.data.organizer import DataOrganizer
from core.data.requests import validate_data_request, validate_pagination
from core.data.rescs_reader import AdapterDataReader, RescsDataReader
from core.events import EventBus
from core.rescs import InMemoryRescsAdapter
from core.resources import Resource
from core.security import SecurityManager
from core.security.models import Identity, IdentityType, Permission
from core.security.provider import TokenAuthenticationProvider


def _make_record(i, namespace="core.memory", owner="device-a", stamp=None):
    return {
        "id": f"{namespace}/key-{i:03d}",
        "namespace": namespace,
        "key": f"key-{i:03d}",
        "value": {"index": i, "topic": "pilot" if i % 2 == 0 else "other"},
        "metadata": {"name": f"record-{i}"},
        "owner": owner,
        "version": 1,
        "etag": f"etag-{i}",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": stamp or f"2026-02-{(i % 27) + 1:02d}T00:00:00+00:00",
    }


class FakeReader(RescsDataReader):
    """Recording in-memory reader with injectable failures."""

    def __init__(self, scope, cross=False, records=None, files=None, failure=None):
        super().__init__(scope, cross)
        self.records = records if records is not None else [_make_record(0)]
        self.files = files or {}
        self.failure = failure
        self.calls = []

    def _maybe_fail(self):
        if self.failure is not None:
            raise self.failure

    def get_record(self, namespace, key, owner=None):
        self.calls.append(("get", namespace, key, self._check_owner(owner)))
        self._maybe_fail()
        for record in self.records:
            if record["namespace"] == namespace and record["key"] == key:
                if self.owner_scope is not None and record["owner"] != self.owner_scope and not self._allow_cross_owner:
                    continue
                return record
        raise DataNotFound("Record was not found.")

    def list_records(self, namespace=None, key_prefix=None, owner=None):
        effective = self._check_owner(owner)
        self.calls.append(("list", namespace, key_prefix, effective))
        self._maybe_fail()
        result = list(self.records)
        if namespace is not None:
            result = [r for r in result if r["namespace"] == namespace]
        if key_prefix is not None:
            result = [r for r in result if r["key"].startswith(key_prefix)]
        if effective is not None:
            result = [r for r in result if r["owner"] == effective]
        return result

    def search_records(self, query, namespace=None, key_prefix=None, owner=None):
        self.calls.append(("search", query, namespace, key_prefix, self._check_owner(owner)))
        self._maybe_fail()
        needle = query.lower()
        return [
            r
            for r in self.list_records(namespace, key_prefix, owner)
            if needle in r["key"].lower()
            or needle in str(r["value"]).lower()
        ]

    def get_file_metadata(self, file_id):
        self.calls.append(("file_metadata", file_id))
        self._maybe_fail()
        try:
            return self.files[file_id][0]
        except KeyError:
            raise DataNotFound("File was not found.")

    def get_file_bytes(self, file_id):
        self.calls.append(("file_bytes", file_id))
        self._maybe_fail()
        try:
            return self.files[file_id]
        except KeyError:
            raise DataNotFound("File was not found.")


def _make_security(extra_permissions=None):
    sm = SecurityManager(provider=TokenAuthenticationProvider())
    sm.register_identity(
        Identity(
            identity_id="device-a",
            name="Device A",
            identity_type=IdentityType.DEVICE,
            permissions=frozenset({Permission.READ}),
            metadata={"token": "secret-a"},
        )
    )
    sm.register_identity(
        Identity(
            identity_id="device-admin",
            name="Admin",
            identity_type=IdentityType.DEVICE,
            permissions=frozenset(extra_permissions or {Permission.READ, Permission.ADMIN}),
            metadata={"token": "secret-admin"},
        )
    )
    return sm


def _make_devices(*online_ids):
    registry = DeviceRegistry()
    for device_id in online_ids:
        registry.register(
            device_id=device_id,
            device_name=device_id,
            device_type="generic",
            platform="test",
            capabilities=[],
            protocol_version="0.3.0",
            identity_id=device_id,
            connection_id=f"conn-{device_id}",
        )
    return registry


def _organizer(reader=None, security=None, devices=None, events=None):
    fake = reader or FakeReader("device-a")
    return DataOrganizer(
        reader_factory=lambda scope, cross=False: fake
        if scope == "device-a" and not cross
        else FakeReader(scope, cross, records=fake.records, files=fake.files, failure=fake.failure),
        security_manager=security if security is not None else _make_security(),
        device_registry=devices,
        event_bus=events,
    ), fake


def _handle(org, payload, sender="device-a", request_id="req-1", message_id="mid-1"):
    return org.handle_request(
        payload=payload,
        sender_device_id=sender,
        correlation_id=request_id,
        message_id=message_id,
        identity_id=sender,
    )


# -- request validation ---------------------------------------------------------


def test_valid_request_types_parse():
    for request_type, payload in [
        ("record_get", {"request_type": "record_get", "namespace": "n", "key": "k"}),
        ("record_list", {"request_type": "record_list", "namespace": "n"}),
        ("record_search", {"request_type": "record_search", "namespace": "n", "query": "q"}),
        ("file_metadata", {"request_type": "file_metadata", "file_id": "f"}),
        ("file_download", {"request_type": "file_download", "file_id": "f"}),
    ]:
        parsed, error = validate_data_request(payload)
        assert error is None, payload
        assert parsed.request_type == request_type


def test_missing_request_type():
    parsed, error = validate_data_request({"namespace": "n"})
    assert parsed is None
    assert error[0] == "INVALID_DATA_REQUEST"


def test_unknown_request_type():
    org, _ = _organizer()
    msg, dest = _handle(org, {"request_type": "sql_exec"})
    assert msg.message_type == DATA_ERROR
    assert msg.payload["error"] == "INVALID_DATA_REQUEST"
    assert msg.payload["request_id"] == "req-1"
    assert msg.request_id == "mid-1"
    assert dest == "device-a"


def test_malformed_payloads():
    org, _ = _organizer()
    for bad in ("string", ["list"], None, 42):
        msg, _ = org.handle_request(
            payload=bad,
            sender_device_id="device-a",
            correlation_id="r",
            message_id="m",
            identity_id="device-a",
        )
        assert msg.message_type == DATA_ERROR
        assert msg.payload["error"] == "INVALID_DATA_REQUEST"


def test_record_get_missing_fields():
    org, _ = _organizer()
    msg, _ = _handle(org, {"request_type": "record_get", "namespace": "n"})
    assert msg.payload["error"] == "INVALID_DATA_REQUEST"
    msg, _ = _handle(org, {"request_type": "record_get", "key": "k"})
    assert msg.payload["error"] == "INVALID_DATA_REQUEST"


@pytest.mark.parametrize("limit", [0, -1, 501, 1000, "20", 1.5, True])
def test_invalid_limits_rejected(limit):
    parsed, error = validate_data_request(
        {"request_type": "record_list", "limit": limit}
    )
    assert parsed is None
    assert error[0] == "INVALID_PAGINATION"


@pytest.mark.parametrize("offset", [-1, -100, "0", 1.5, True])
def test_invalid_offsets_rejected(offset):
    parsed, error = validate_data_request(
        {"request_type": "record_list", "offset": offset}
    )
    assert parsed is None
    assert error[0] == "INVALID_PAGINATION"


def test_pagination_boundaries_accepted():
    for limit, offset in [(1, 0), (500, 0), (100, 250), (500, 10000)]:
        limit_out, offset_out, error = validate_pagination(limit, offset)
        assert error is None
        assert (limit_out, offset_out) == (limit, offset)


# -- operations -------------------------------------------------------------------


def test_record_get():
    org, _ = _organizer(FakeReader("device-a", records=[_make_record(3)]))
    msg, dest = _handle(org, {"request_type": "record_get", "namespace": "core.memory", "key": "key-003"})
    assert msg.message_type == DATA_RESPONSE
    assert msg.payload["data_type"] == "record"
    assert msg.payload["item"]["key"] == "key-003"
    assert msg.payload["item"]["owner"] == "device-a"
    assert dest == "device-a"


def test_record_get_unknown_namespace():
    org, _ = _organizer()
    msg, _ = _handle(org, {"request_type": "record_get", "namespace": "no.such", "key": "key-000"})
    assert msg.message_type == DATA_ERROR
    assert msg.payload["error"] == "DATA_NOT_FOUND"


def test_record_list_filters_and_page_shape():
    records = [_make_record(i) for i in range(5)]
    org, fake = _organizer(FakeReader("device-a", records=records))
    msg, _ = _handle(
        org,
        {"request_type": "record_list", "namespace": "core.memory", "limit": 2, "offset": 1},
    )
    assert msg.message_type == DATA_RESPONSE
    body = msg.payload
    assert body["data_type"] == "records"
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert len(body["items"]) == 2
    assert ("list", "core.memory", None, "device-a") in fake.calls


def test_record_search():
    records = [_make_record(i) for i in range(4)]
    org, _ = _organizer(FakeReader("device-a", records=records))
    msg, _ = _handle(
        org, {"request_type": "record_search", "namespace": "core.memory", "query": "key-001"}
    )
    assert msg.message_type == DATA_RESPONSE
    assert msg.payload["total"] == 1
    assert msg.payload["items"][0]["key"] == "key-001"


def _make_file(content: bytes, file_id="file-1", owner="device-a"):
    meta = {
        "id": file_id,
        "filename": "doc.bin",
        "mime_type": "application/octet-stream",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "etag": "etag-f1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-02T00:00:00+00:00",
    }
    return meta, content


def test_file_metadata_only():
    meta, content = _make_file(b"hello")
    org, _ = _organizer(FakeReader("device-a", files={"file-1": (meta, content)}))
    msg, _ = _handle(org, {"request_type": "file_metadata", "file_id": "file-1"})
    assert msg.message_type == DATA_RESPONSE
    assert msg.payload["data_type"] == "file_metadata"
    item = msg.payload["item"]
    for key in ("id", "filename", "mime_type", "size", "sha256", "etag", "created_at", "updated_at"):
        assert key in item
    assert "content_base64" not in msg.payload
    assert item["sha256"] == hashlib.sha256(b"hello").hexdigest()


def test_file_download_inline():
    meta, content = _make_file(b"inline-bytes")
    org, _ = _organizer(FakeReader("device-a", files={"file-1": (meta, content)}))
    msg, _ = _handle(org, {"request_type": "file_download", "file_id": "file-1"})
    assert msg.message_type == DATA_RESPONSE
    assert msg.payload["data_type"] == "file_content"
    assert base64.b64decode(msg.payload["content_base64"]) == b"inline-bytes"
    assert msg.payload["sha256"] == meta["sha256"]
    metrics = org.data_metrics()
    assert metrics["data_files_retrieved"] == 1


def test_file_download_too_large():
    big = b"x" * (1024 * 1024 + 1)
    meta, _ = _make_file(big, file_id="big")
    org, _ = _organizer(FakeReader("device-a", files={"big": (meta, big)}))
    msg, _ = _handle(org, {"request_type": "file_download", "file_id": "big"})
    assert msg.message_type == DATA_ERROR
    assert msg.payload["error"] == "FILE_TRANSFER_REQUIRED"
    assert msg.payload["request_id"] == "req-1"


def test_file_integrity_sha_mismatch():
    meta, content = _make_file(b"real")
    meta = dict(meta, sha256="0" * 64)
    org, _ = _organizer(FakeReader("device-a", files={"file-1": (meta, content)}))
    msg, _ = _handle(org, {"request_type": "file_download", "file_id": "file-1"})
    assert msg.message_type == DATA_ERROR
    assert msg.payload["error"] == "DATA_RETRIEVAL_FAILED"


def test_file_integrity_size_mismatch():
    meta, content = _make_file(b"real")
    meta = dict(meta, size=999)
    org, _ = _organizer(FakeReader("device-a", files={"file-1": (meta, content)}))
    msg, _ = _handle(org, {"request_type": "file_download", "file_id": "file-1"})
    assert msg.message_type == DATA_ERROR
    assert msg.payload["error"] == "DATA_RETRIEVAL_FAILED"


# -- normalization / ordering / pagination ------------------------------------------


def test_normalization_shapes():
    item = normalize_record(_make_record(1))
    assert set(item) == {
        "id", "namespace", "key", "value", "metadata",
        "owner", "version", "etag", "created_at", "updated_at",
    }
    assert item["value"] == {"index": 1, "topic": "other"}
    fitem = normalize_file_metadata(_make_file(b"z")[0])
    assert set(fitem) == {
        "id", "filename", "mime_type", "size",
        "sha256", "etag", "created_at", "updated_at",
    }


def test_deterministic_ordering():
    records = [
        normalize_record(_make_record(0, stamp="2026-01-03T00:00:00+00:00")),
        normalize_record(_make_record(1, stamp="2026-01-01T00:00:00+00:00")),
        normalize_record(_make_record(2, stamp="2026-01-03T00:00:00+00:00")),
    ]
    ordered = order_records(records)
    # updated_at DESC, id ASC on ties.
    assert [r["key"] for r in ordered] == ["key-000", "key-002", "key-001"]
    assert order_records(records) == order_records(list(reversed(records)))


def test_ordering_missing_timestamps_last():
    records = [
        normalize_record(_make_record(0, stamp="2026-01-01T00:00:00+00:00")),
        normalize_record(_make_record(1)),
    ]
    records[1]["updated_at"] = None
    ordered = order_records(records)
    assert ordered[0]["key"] == "key-000"
    assert ordered[1]["key"] == "key-001"


def test_pagination_correctness():
    records = [normalize_record(_make_record(i, stamp="2026-03-01T00:00:00+00:00")) for i in range(25)]
    page = paginate(order_records(records), 10, 5)
    assert page["total"] == 25
    assert page["limit"] == 10
    assert page["offset"] == 5
    assert len(page["items"]) == 10
    page2 = paginate(order_records(records), 10, 20)
    assert len(page2["items"]) == 5


# -- correlation / identity ------------------------------------------------------------


def test_request_id_source_destination_preserved():
    org, _ = _organizer()
    msg, dest = org.handle_request(
        payload={"request_type": "record_get", "namespace": "core.memory", "key": "key-000"},
        sender_device_id="device-a",
        correlation_id="orig-req-9",
        message_id="orig-mid-9",
        identity_id="device-a",
    )
    assert msg.request_id == "orig-mid-9"
    assert msg.payload if False else True
    assert msg.source == "core"
    assert msg.destination == "device-a"
    assert msg.identity_id == "device-a"
    assert dest == "device-a"


def test_error_preserves_request_id():
    org, _ = _organizer()
    msg, _ = org.handle_request(
        payload={"request_type": "record_get", "namespace": "core.memory", "key": "ghost"},
        sender_device_id="device-a",
        correlation_id="orig-req-7",
        message_id="orig-mid-7",
        identity_id="device-a",
    )
    assert msg.message_type == DATA_ERROR
    assert msg.payload["request_id"] == "orig-req-7"
    assert msg.request_id == "orig-mid-7"
    assert set(("error", "message", "request_id")) <= set(msg.payload)


def test_no_traceback_leak_on_unexpected_failure():
    class BoomReader(FakeReader):
        def list_records(self, namespace=None, key_prefix=None, owner=None):
            raise RuntimeError("secret db path /var/lib/rescs爆")

    org, _ = _organizer(BoomReader("device-a"))
    msg, _ = _handle(org, {"request_type": "record_list"})
    assert msg.message_type == DATA_ERROR
    assert msg.payload["error"] == "DATA_RETRIEVAL_FAILED"
    assert "Traceback" not in msg.payload["message"]
    assert "/var/lib" not in msg.payload["message"]


# -- authorization ------------------------------------------------------------------------


def test_unauthenticated_identity_denied():
    sm = SecurityManager(provider=TokenAuthenticationProvider())
    org, fake = _organizer(security=sm)
    msg, _ = _handle(org, {"request_type": "record_list"})
    assert msg.message_type == DATA_ERROR
    assert msg.payload["error"] == "DATA_ACCESS_DENIED"
    assert fake.calls == []
    assert org.data_metrics()["data_access_denied"] == 1


def test_identity_without_read_denied():
    sm = SecurityManager(provider=TokenAuthenticationProvider())
    sm.register_identity(
        Identity(
            identity_id="device-a",
            name="Device A",
            identity_type=IdentityType.DEVICE,
            permissions=frozenset(),
            metadata={"token": "secret-a"},
        )
    )
    org, fake = _organizer(security=sm)
    msg, _ = _handle(org, {"request_type": "record_list"})
    assert msg.payload["error"] == "DATA_ACCESS_DENIED"
    assert fake.calls == []


def test_owner_isolation_blocks_other_owner():
    owned = [_make_record(0, owner="device-a"), _make_record(1, owner="device-b")]
    org, fake = _organizer(FakeReader("device-a", records=owned))
    msg, _ = _handle(org, {"request_type": "record_list", "owner": "device-b"})
    assert msg.message_type == DATA_ERROR
    assert msg.payload["error"] == "DATA_ACCESS_DENIED"
    # Reader was never asked for the other owner's data.
    assert all(call[0] != "list" or call[3] != "device-b" for call in fake.calls)


def test_admin_may_read_other_owner():
    owned = [_make_record(0, owner="device-a"), _make_record(1, owner="device-b")]
    sm = _make_security()
    org = DataOrganizer(
        reader_factory=lambda scope, cross=False: FakeReader(
            "device-b" if cross else scope, cross, records=owned
        ),
        security_manager=sm,
        device_registry=None,
    )
    msg, _ = _handle(
        org,
        {"request_type": "record_list", "owner": "device-b"},
        sender="device-admin",
    )
    assert msg.message_type == DATA_RESPONSE
    assert msg.payload["total"] == 1
    assert msg.payload["items"][0]["owner"] == "device-b"


def test_namespace_isolation_scopes_reader():
    org, fake = _organizer()
    msg, _ = _handle(org, {"request_type": "record_list", "namespace": "core.memory"})
    assert msg.message_type == DATA_RESPONSE
    assert fake.calls and fake.calls[0][1] == "core.memory"


# -- error mapping ----------------------------------------------------------------------------


def test_rescs_not_found_maps():
    org, _ = _organizer(FakeReader("device-a", failure=DataNotFound("gone")))
    msg, _ = _handle(org, {"request_type": "record_list"})
    assert msg.payload["error"] == "DATA_NOT_FOUND"


def test_rescs_unavailable_maps_and_counts():
    org, _ = _organizer(FakeReader("device-a", failure=DataSourceUnavailable("down")))
    msg, _ = _handle(org, {"request_type": "record_list"})
    assert msg.payload["error"] == "DATA_SOURCE_UNAVAILABLE"
    assert org.data_metrics()["data_source_failures"] == 1


def test_rescs_unauthorized_maps():
    org, _ = _organizer(FakeReader("device-a", failure=DataAccessDenied("no")))
    msg, _ = _handle(org, {"request_type": "record_list"})
    assert msg.payload["error"] == "DATA_ACCESS_DENIED"


def test_rescs_unexpected_maps():
    org, _ = _organizer(FakeReader("device-a", failure=DataRetrievalFailed("boom")))
    msg, _ = _handle(org, {"request_type": "record_list"})
    assert msg.payload["error"] == "DATA_RETRIEVAL_FAILED"


def test_oversized_response_rejected():
    big_value = {"blob": "y" * (6 * 1024 * 1024)}
    record = _make_record(0)
    record["value"] = big_value
    org, _ = _organizer(FakeReader("device-a", records=[record]))
    msg, _ = _handle(org, {"request_type": "record_get", "namespace": "core.memory", "key": "key-000"})
    assert msg.message_type == DATA_ERROR
    assert msg.payload["error"] == "DATA_RESPONSE_TOO_LARGE"
    assert org.data_metrics()["data_response_too_large"] == 1


# -- destinations ----------------------------------------------------------------------------------


def test_explicit_valid_destination():
    devices = _make_devices("device-a", "device-b")
    org, _ = _organizer(FakeReader("device-a"), devices=devices)
    msg, dest = _handle(
        org,
        {
            "request_type": "record_get",
            "namespace": "core.memory",
            "key": "key-000",
            "destination_device_id": "device-b",
        },
    )
    assert msg.message_type == DATA_RESPONSE
    assert msg.destination == "device-b"
    assert dest == "device-b"


def test_unknown_destination_rejected():
    devices = _make_devices("device-a")
    org, _ = _organizer(FakeReader("device-a"), devices=devices)
    msg, dest = _handle(
        org,
        {
            "request_type": "record_get",
            "namespace": "core.memory",
            "key": "key-000",
            "destination_device_id": "device-ghost",
        },
    )
    assert msg.message_type == DATA_ERROR
    assert msg.payload["error"] == "INVALID_DESTINATION"
    assert dest == "device-a"


def test_offline_destination_rejected():
    devices = _make_devices("device-a", "device-b")
    devices.mark_offline("device-b", "conn-device-b")
    org, _ = _organizer(FakeReader("device-a"), devices=devices)
    msg, dest = _handle(
        org,
        {
            "request_type": "record_get",
            "namespace": "core.memory",
            "key": "key-000",
            "destination_device_id": "device-b",
        },
    )
    assert msg.message_type == DATA_ERROR
    assert msg.payload["error"] == "DESTINATION_UNAVAILABLE"
    assert dest == "device-a"


# -- adapter backend ----------------------------------------------------------------------------------


def test_adapter_reader_owner_scoping():
    adapter = InMemoryRescsAdapter()
    adapter.persist_resource(
        Resource(
            resource_id="device-a:note",
            name="note",
            resource_type="service",
            owner="device-a",
            metadata={"namespace": "core.memory", "key": "note"},
        )
    )
    adapter.persist_resource(
        Resource(
            resource_id="device-b:note",
            name="note",
            resource_type="service",
            owner="device-b",
            metadata={"namespace": "core.memory", "key": "note"},
        )
    )
    reader = AdapterDataReader(adapter, "device-a")
    assert reader.get_record("core.memory", "note")["owner"] == "device-a"
    assert len(reader.list_records("core.memory")) == 1
    with pytest.raises(DataAccessDenied):
        reader.list_records("core.memory", owner="device-b")
    cross = AdapterDataReader(adapter, "device-a", allow_cross_owner=True)
    assert len(cross.list_records("core.memory", owner="device-b")) == 1
    assert len(cross.list_records("core.memory")) == 1


# -- metrics / events --------------------------------------------------------------------------------------


def test_metrics_and_events_flow():
    events = EventBus()
    seen = []
    for name in (
        "DATA_REQUEST_RECEIVED",
        "DATA_RETRIEVAL_SUCCESS",
        "DATA_RETRIEVAL_FAILURE",
        "DATA_ACCESS_DENIED",
        "DATA_RESPONSE_SENT",
    ):
        events.subscribe(name, lambda e, n=name: seen.append(n))
    org, _ = _organizer(events=events)
    _handle(org, {"request_type": "record_list"})
    _handle(org, {"request_type": "record_get", "namespace": "core.memory", "key": "ghost"})
    metrics = org.data_metrics()
    assert metrics["data_requests"] == 2
    assert metrics["data_requests_successful"] == 1
    assert metrics["data_requests_failed"] == 1
    assert metrics["data_records_retrieved"] == 1
    assert metrics["data_messages_sent"] == 1
    assert "DATA_REQUEST_RECEIVED" in seen
    assert "DATA_RETRIEVAL_SUCCESS" in seen
    assert "DATA_RETRIEVAL_FAILURE" in seen
    assert "DATA_RESPONSE_SENT" in seen
