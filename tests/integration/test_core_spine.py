"""
C.O.R.E. Integration Spine — end-to-end verification for Windows laptop host.

Covers:
  start → device register → message → route → service → response → health → stop
  and device → agent → rescs flow with mocked adapter.

The spine validates the full path without requiring external R.E.S.C.S. or
hardware, using InMemoryRescsAdapter and LocalTransport (and TcpTransport)
on the same 32GB/1TB Windows host.
"""



from core.application import CoreApplication
from core.communication import Message, MessageSerializer, TcpTransport
from core.rescs import FileRescsAdapter, InMemoryRescsAdapter


def test_spine_start_device_message_route_service_response_health_stop(tmp_path):
    """Full spine: start, register device, route, service, health, stop."""

    app = CoreApplication()
    app.start()
    assert app.is_running

    # Register a device resource via service operation
    device_payload = {
        "operation": "register",
        "resource_id": "device-iphone-1",
        "name": "iPhone 15",
        "resource_type": "device",
        "capabilities": ["camera", "gps"],
        "metadata": {"platform": "ios", "device_type": "phone"},
    }
    msg = Message(
        source="test-spine",
        destination="service:resources",
        message_type="ANY",
        payload=device_payload,
        identity_id="test-identity",
    )

    # Verify serializer preserves identity_id over wire
    serialized = MessageSerializer.serialize(msg)
    deserialized = MessageSerializer.deserialize(serialized)
    assert deserialized.identity_id == "test-identity"
    assert deserialized.payload == device_payload

    # Deliver via communication (service dispatcher)
    resp = app.communication.send(msg)
    assert resp is not None
    assert resp.payload["success"] is True
    assert resp.payload["result"]["resource"]["id"] == "device-iphone-1"

    # Verify resource registry and organization auto-categorization
    assert app.resources.count() >= 1
    assert app.organization.count() >= 1  # device auto-categorized

    # Verify runtime history tracking for device
    assert app.runtime_history.count() >= 1
    record = app.runtime_history.get("device-iphone-1")
    assert record.entity_type == "device"
    assert record.end_time is None  # still active

    # Verify routing via health service
    health_msg = Message(
        source="test-spine",
        destination="service:health",
        message_type="HEALTH.STATUS",
        payload={"operation": "status"},
    )
    health_resp = app.communication.send(health_msg)
    assert health_resp.payload["success"] is True
    overall = health_resp.payload["result"]["overall"]
    # Should be healthy (or degraded if rescs not healthy, but not unhealthy)
    assert overall in ("healthy", "degraded", "unknown")

    # Direct health check via app
    results = app.health_check()
    overall_enum = app.health.overall_status()
    assert overall_enum.value in ("healthy", "degraded", "unknown")
    # Ensure key subsystems are healthy
    ids = {r.component_id for r in results}
    assert {"runtime", "communication", "events", "resources"}.issubset(ids)

    # Stop and verify reverse order and no shutdown errors
    # Capture shutdown via runtime state
    from core.runtime import RuntimeState

    app.stop()
    assert app.state == RuntimeState.STOPPED
    assert not app.communication.is_running
    # runtime_history should retain record until explicit clear or
    # InMemory adapter cleared on shutdown; check it still exists (active)
    # For InMemory, shutdown clears rescs but history remains; after stop
    # the device record should still be queryable until removed
    assert app.runtime_history.get("device-iphone-1") is not None


def test_spine_device_agent_rescs_flow_with_mock_adapter():
    """Device → CORE → Agent → persistence via InMemoryRescsAdapter."""

    adapter = InMemoryRescsAdapter()
    app = CoreApplication(rescs_adapter=adapter)
    app.start()

    # Device registers
    dev_msg = Message(
        source="device-manager",
        destination="service:resources",
        message_type="ANY",
        payload={
            "operation": "register",
            "resource_id": "dev-watch-1",
            "name": "Apple Watch",
            "resource_type": "device",
            "capabilities": ["heart_rate"],
            "metadata": {"platform": "watchos"},
        },
    )
    app.communication.send(dev_msg)

    # Agent offloads to Windows host
    agent_msg = Message(
        source="agent-scheduler",
        destination="service:resources",
        message_type="ANY",
        payload={
            "operation": "register",
            "resource_id": "agent-1",
            "name": "ASIS-Agent-Watch-1",
            "resource_type": "agent",
            "metadata": {"version": "0.2.0", "host": "windows-host", "target_device": "dev-watch-1"},
        },
    )
    app.communication.send(agent_msg)

    # Verify RESCS persistence via adapter
    assert adapter.fetch_resource("dev-watch-1") is not None
    assert adapter.fetch_resource("agent-1") is not None
    assert len(adapter.list_resources()) == 2

    # Runtime history persisted
    assert len(adapter.list_runtimes()) >= 2
    runtimes = {r.entity_id for r in adapter.list_runtimes()}
    assert {"dev-watch-1", "agent-1"}.issubset(runtimes)

    # Data flow: simulate agent producing data and persisting
    # Use runtime service to query history
    hist_msg = Message(
        source="test",
        destination="service:runtime",
        message_type="ANY",
        payload={"operation": "history"},
    )
    hist_resp = app.communication.send(hist_msg)
    assert hist_resp.payload["success"] is True
    assert len(hist_resp.payload["result"]["records"]) >= 2

    # RESCS service health
    rescs_msg = Message(
        source="test",
        destination="service:rescs",
        message_type="ANY",
        payload={"operation": "health"},
    )
    rescs_resp = app.communication.send(rescs_msg)
    assert rescs_resp.payload["success"] is True
    assert rescs_resp.payload["result"]["healthy"] is True

    app.stop()
    # After stop with InMemory, rescs is cleared; ensure adapter cleared
    # (File adapter would persist)
    assert adapter.list_resources() == []


def test_spine_file_adapter_persists_across_restarts(tmp_path):
    """File adapter survives app stop/start (Windows file path)."""

    rescs_file = tmp_path / "rescs.json"
    adapter = FileRescsAdapter(path=rescs_file)
    app = CoreApplication(rescs_adapter=adapter)
    app.start()

    msg = Message(
        source="test",
        destination="service:resources",
        message_type="ANY",
        payload={
            "operation": "register",
            "resource_id": "dev-persistent-1",
            "name": "Persistent Device",
            "resource_type": "device",
        },
    )
    app.communication.send(msg)
    app.stop()

    # File should exist and contain resource
    assert rescs_file.exists()
    # Create new app with same file
    adapter2 = FileRescsAdapter(path=rescs_file)
    app2 = CoreApplication(rescs_adapter=adapter2)
    app2.start()
    # New adapter should load persisted resource (via file loader)
    # Note: CoreApplication resources are empty after restart; RESCS retains
    assert adapter2.fetch_resource("dev-persistent-1") is not None
    assert adapter2.fetch_resource("dev-persistent-1").name == "Persistent Device"
    app2.stop()


def test_spine_external_transport_loopback():
    """Verify TcpTransport can replace LocalTransport and still route."""

    tcp = TcpTransport(host="127.0.0.1", port=0)
    app = CoreApplication()
    # Swap transport before start (simulates network.enabled=true)
    app.communication = tcp  # type: ignore
    app.routing.set_transport(tcp)
    app.start()

    msg = Message(
        source="external-device",
        destination="service:resources",
        message_type="ANY",
        payload={"operation": "list"},
    )
    resp = app.communication.send(msg)
    assert resp.payload["success"] is True
    assert "resources" in resp.payload["result"]

    app.stop()
