"""HTTP reader + fixture contract tests (localhost only)."""

import pytest

from core.data.errors import (
    DataAccessDenied,
    DataNotFound,
    DataRetrievalFailed,
    DataSourceUnavailable,
)
from core.data.rescs_reader import HttpDataReader

from .rescs_http_fixture import RescsFixture


@pytest.fixture()
def fixture():
    with RescsFixture() as running:
        yield running


def _reader(fixture, scope="device-a", cross=False, timeout=2.0):
    return HttpDataReader(
        endpoint=fixture.url,
        owner_scope=scope,
        allow_cross_owner=cross,
        timeout=timeout,
    )


def test_http_get_record(fixture):
    reader = _reader(fixture)
    record = reader.get_record("core.memory", "flight-plan")
    assert record["key"] == "flight-plan"
    assert record["owner"] == "device-a"
    assert record["etag"] == "etag-core.memory-flight-plan"


def test_http_get_missing_record(fixture):
    with pytest.raises(DataNotFound):
        _reader(fixture).get_record("core.memory", "ghost")


def test_http_list_and_search(fixture):
    reader = _reader(fixture)
    records = reader.list_records("core.memory")
    assert {r["key"] for r in records} == {"flight-plan", "pilot-notes", "shopping"}
    found = reader.search_records("pilot", "core.memory")
    assert {r["key"] for r in found} == {"flight-plan", "pilot-notes"}


def test_http_owner_scoping(fixture):
    reader = _reader(fixture, scope="device-a")
    assert all(r["owner"] == "device-a" for r in reader.list_records("core.memory"))
    with pytest.raises(DataAccessDenied):
        reader.list_records("core.memory", owner="device-b")


def test_http_file_metadata_and_bytes(fixture):
    reader = _reader(fixture)
    meta = reader.get_file_metadata("manual")
    assert meta["filename"] == "manual.bin"
    assert meta["size"] == len(b"pilot-manual-bytes")
    returned_meta, content = reader.get_file_bytes("manual")
    assert content == b"pilot-manual-bytes"
    assert returned_meta["sha256"] == meta["sha256"]


def test_http_missing_file(fixture):
    with pytest.raises(DataNotFound):
        _reader(fixture).get_file_metadata("ghost")


def test_http_unauthorized_maps(fixture):
    fixture.fail_mode = "unauthorized"
    with pytest.raises(DataAccessDenied):
        _reader(fixture).list_records("core.memory")


def test_http_server_error_maps(fixture):
    fixture.fail_mode = "error"
    with pytest.raises(DataRetrievalFailed):
        _reader(fixture).list_records("core.memory")


def test_http_timeout_maps_to_unavailable(fixture):
    fixture.timeout_delay = 3.0
    fixture.fail_mode = "timeout"
    with pytest.raises(DataSourceUnavailable):
        _reader(fixture, timeout=0.5).list_records("core.memory")


def test_http_garbage_maps(fixture):
    fixture.fail_mode = "garbage"
    with pytest.raises(DataRetrievalFailed):
        _reader(fixture).list_records("core.memory")


def test_http_unreachable_maps_to_unavailable():
    reader = HttpDataReader(
        endpoint="http://127.0.0.1:1", owner_scope="device-a", timeout=0.5
    )
    with pytest.raises(DataSourceUnavailable):
        reader.list_records("core.memory")
