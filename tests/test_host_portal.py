"""Host portal tests: startup, localhost binding, APIs, redaction."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from core.application import CoreApplication
from core.portal import models
from core.portal.server import HostPortal

SECRET_MARKERS = (
    "s3cr3t-token",
    "supersecret",
    "PRIVATE-KEY-MATERIAL",
    "postgres://user:hunter2@db/app",
)


@pytest.fixture()
def app():
    application = CoreApplication()
    application.start()
    try:
        yield application
    finally:
        application.stop()


@pytest.fixture()
def portal(app):
    host_portal = HostPortal(app, host="127.0.0.1", port=0)
    url = host_portal.start()
    try:
        yield host_portal
    finally:
        host_portal.stop()
    assert url.startswith("http://127.0.0.1:")


def _get(portal, path, *, method="GET", body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        portal.url + path, data=data, headers=headers, method=method
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _assert_no_secrets(text: str) -> None:
    for marker in SECRET_MARKERS:
        assert marker not in text


def test_portal_binds_localhost(portal):
    assert portal.url.startswith("http://127.0.0.1:")
    assert portal.is_running


def test_portal_index_serves_html(portal):
    request = urllib.request.Request(portal.url + "/")
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
        assert "text/html" in response.headers.get("Content-Type", "")
        body = response.read().decode("utf-8")
    assert "C.O.R.E. Host Portal" in body
    _assert_no_secrets(body)


def test_status_shape(portal):
    status, body = _get(portal, "/api/status")
    assert status == 200 and body["ok"] is True
    data = body["data"]
    assert data["service"] == "core-host-portal"
    assert "core_version" in data and "devices" in data and "portal" in data
    _assert_no_secrets(json.dumps(data))


def test_devices_list_and_redaction(app, portal):
    app.device_registry.register(
        device_id="mac-01",
        device_name="MacBook",
        device_type="phone",
        platform="mac",
        capabilities=["chat"],
    )
    status, body = _get(portal, "/api/devices")
    assert status == 200
    assert any(d["device_id"] == "mac-01" for d in body["data"])
    _assert_no_secrets(json.dumps(body["data"]))


def test_resources_services_agents_health(app, portal):
    for path in ("/api/resources", "/api/services", "/api/agents", "/api/health"):
        status, body = _get(portal, path)
        assert status == 200, path
        assert body["ok"] is True, path
        _assert_no_secrets(json.dumps(body["data"]))
    _, services = _get(portal, "/api/services")
    ids = {s["service_id"] for s in services["data"]}
    assert {"resources", "health", "agent", "rescs"} <= ids
    _, agents = _get(portal, "/api/agents")
    assert "profiles" in agents["data"] and "assignments" in agents["data"]


def test_events_network_sessions_rescs_config_diagnostics(portal):
    for path in (
        "/api/events",
        "/api/network",
        "/api/sessions",
        "/api/rescs",
        "/api/config",
        "/api/diagnostics",
    ):
        status, body = _get(portal, path)
        assert status == 200, path
        assert body["ok"] is True, path
        _assert_no_secrets(json.dumps(body["data"]))


def test_agent_assign_release_flow(app, portal):
    app.device_registry.register(device_id="mac-01", device_name="MacBook")
    status, body = _get(
        portal, "/api/agents/assign", method="POST", body={"device_id": "mac-01"}
    )
    assert status == 200
    assert body["data"]["agent_id"]
    status, body = _get(
        portal, "/api/agents/release", method="POST", body={"device_id": "mac-01"}
    )
    assert status == 200
    assert body["data"]["released"] is True


def test_location_and_distance(app, portal):
    app.device_registry.register(device_id="mac-01", device_name="MacBook")
    status, body = _get(
        portal,
        "/api/devices/location",
        method="POST",
        body={
            "device_id": "mac-01",
            "location": {"latitude": 25.2048, "longitude": 55.2708,
                         "source": "manual"},
        },
    )
    assert status == 200
    status, network = _get(portal, "/api/network")
    device = next(d for d in network["data"]["devices"] if d["device_id"] == "mac-01")
    assert device["distance"] == "Distance unavailable"  # host location unknown
    status, body = _get(
        portal,
        "/api/location",
        method="POST",
        body={"location": {"latitude": 25.1972, "longitude": 55.2744,
                           "source": "manual"}},
    )
    assert status == 200
    status, network = _get(portal, "/api/network")
    device = next(d for d in network["data"]["devices"] if d["device_id"] == "mac-01")
    assert "away" in device["distance"]


def test_models_redaction_unit():
    cleaned = models.redact({"session_token": "x", "ok": 1})
    assert "session_token" not in cleaned and cleaned == {"ok": 1}
    assert models.redact({"nested": {"api_key": "x", "v": 2}}) == {"nested": {"v": 2}}
    assert models.redact({"database_url": "postgres://user:hunter2@db/app"}) == {
        "database_url": "postgres://***@db"
    }
    entry = models.session_entry(
        type("S", (), {"connection_id": "c", "identity_id": "i",
                       "session_token": "s3cr3t-token", "state": "x",
                       "authenticated": True})()
    )
    assert entry["session_token_state"] == "ACTIVE"
    assert "s3cr3t-token" not in json.dumps(entry)


def test_unknown_endpoints_404(portal):
    request = urllib.request.Request(portal.url + "/api/nope")
    try:
        urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    else:
        raise AssertionError("expected 404")
    request = urllib.request.Request(
        portal.url + "/api/agents/assign",
        data=json.dumps({}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
    else:
        raise AssertionError("expected 400")


def test_portal_stop_is_idempotent(app):
    host_portal = HostPortal(app, port=0)
    host_portal.start()
    assert host_portal.is_running
    host_portal.stop()
    host_portal.stop()
    assert not host_portal.is_running


def test_agent_infer_places_request(app):
    from core.resources.models import Resource

    app.resources.register(
        Resource(resource_id="mac-01", name="MacBook", resource_type="device")
    )
    result = app._infer_agent(device_id="mac-01", prompt="summarize this")
    assert result["status"] == "assigned"
    assert result["device_id"] == "mac-01"
    assert result["agent_id"]
    assert result["execution_location"]
    assert result["prompt_chars"] == len("summarize this")
    assert "placement" in result["note"] or "routing" in result["note"]
    # No fabricated generation.
    assert "content" not in result and "text" not in result


def test_agent_infer_rejects_empty_prompt(app):
    with pytest.raises(ValueError):
        app._infer_agent(device_id="mac-01", prompt="  ")
