"""
Agent assignment spine — preserves legacy assign API (device_id only)
while verifying capability-driven assignment and auto-release.
"""


from core.application import CoreApplication
from core.communication import Message


def test_agent_spine_explicit_assign_and_release():
    app = CoreApplication()
    app.start()
    try:
        # Legacy payload: register device with capabilities
        dev_msg = Message(
            source="test",
            destination="service:resources",
            message_type="ANY",
            payload={
                "operation": "register",
                "resource_id": "dev-agent-spine-1",
                "name": "iPhone Spine",
                "resource_type": "device",
                "capabilities": ["camera", "gps"],
                "metadata": {"device_type": "phone", "platform": "ios"},
            },
        )
        r = app.communication.send(dev_msg)
        assert r.payload["success"] is True

        # Legacy assign: only device_id, no profile_id — must still work
        assign_msg = Message(
            source="test",
            destination="service:agent",
            message_type="ANY",
            payload={"operation": "assign", "device_id": "dev-agent-spine-1"},
        )
        resp = app.communication.send(assign_msg)
        assert resp.payload["success"] is True
        agent_id = resp.payload["result"]["agent"]["id"]
        assert agent_id.startswith("agent-dev-agent-spine-1")
        assert resp.payload["result"]["assignment"]["profile_id"] == "asis-offload"

        # Verify agent is discoverable via resources
        get_msg = Message(
            source="test",
            destination="service:resources",
            message_type="ANY",
            payload={"operation": "get", "resource_id": agent_id},
        )
        get_resp = app.communication.send(get_msg)
        assert get_resp.payload["success"] is True
        assert get_resp.payload["result"]["resource"]["type"] == "agent"

        # List assignments via agent service
        list_msg = Message(
            source="test",
            destination="service:agent",
            message_type="ANY",
            payload={"operation": "assignments"},
        )
        list_resp = app.communication.send(list_msg)
        assert list_resp.payload["success"] is True
        assert len(list_resp.payload["result"]["assignments"]) == 1

        # Idempotent assign — legacy should not create duplicate
        resp2 = app.communication.send(assign_msg)
        assert resp2.payload["success"] is True
        assert resp2.payload["result"]["agent"]["id"] == agent_id

        # Release via legacy release (device_id only)
        rel_msg = Message(
            source="test",
            destination="service:agent",
            message_type="ANY",
            payload={"operation": "release", "device_id": "dev-agent-spine-1"},
        )
        rel_resp = app.communication.send(rel_msg)
        assert rel_resp.payload["success"] is True
        assert app.scheduler.assignment_count() == 0

        # Agent resource should be removed
        assert app.resources.count() == 1  # only device remains
    finally:
        app.stop()


def test_agent_spine_auto_release_on_device_remove():
    app = CoreApplication()
    app.start()
    try:
        # Register watch (low-capability)
        dev_msg = Message(
            source="test",
            destination="service:resources",
            message_type="ANY",
            payload={
                "operation": "register",
                "resource_id": "dev-auto-1",
                "name": "Watch Auto",
                "resource_type": "device",
                "capabilities": ["heart_rate"],
                "metadata": {"device_type": "watch", "platform": "watchos"},
            },
        )
        app.communication.send(dev_msg)

        assign_msg = Message(
            source="test",
            destination="service:agent",
            message_type="ANY",
            payload={"operation": "assign", "device_id": "dev-auto-1"},
        )
        resp = app.communication.send(assign_msg)
        assert resp.payload["success"] is True
        assert app.scheduler.assignment_count() == 1

        # Remove device — should auto-release agent (preserve legacy remove flow)
        rm_msg = Message(
            source="test",
            destination="service:resources",
            message_type="ANY",
            payload={"operation": "remove", "resource_id": "dev-auto-1"},
        )
        rm_resp = app.communication.send(rm_msg)
        assert rm_resp.payload["success"] is True
        assert app.scheduler.assignment_count() == 0
        # Agent should also be gone
        assert all(r.resource_id != resp.payload["result"]["agent"]["id"] for r in app.resources.list())
    finally:
        app.stop()


def test_agent_spine_offload_low_capability_vs_generic():
    app = CoreApplication()
    app.start()
    try:
        # Generic device (no special caps) should still get an agent (fallback)
        dev_generic = Message(
            source="test",
            destination="service:resources",
            message_type="ANY",
            payload={
                "operation": "register",
                "resource_id": "dev-generic-1",
                "name": "Generic Box",
                "resource_type": "device",
                "capabilities": [],
                "metadata": {"device_type": "generic", "platform": "unknown"},
            },
        )
        app.communication.send(dev_generic)
        resp = app.communication.send(
            Message(source="test", destination="service:agent", message_type="ANY", payload={"operation": "assign", "device_id": "dev-generic-1"})
        )
        assert resp.payload["success"] is True
        assert resp.payload["result"]["assignment"]["profile_id"] in ("asis-local", "tiviss-compat", "asis-offload")

        # Explicit profile_id preserves legacy ability to force profile
        dev_phone = Message(
            source="test",
            destination="service:resources",
            message_type="ANY",
            payload={
                "operation": "register",
                "resource_id": "dev-phone-force",
                "name": "Phone Force",
                "resource_type": "device",
                "capabilities": ["voice"],
                "metadata": {"device_type": "phone", "platform": "ios"},
            },
        )
        app.communication.send(dev_phone)
        forced = app.communication.send(
            Message(source="test", destination="service:agent", message_type="ANY", payload={"operation": "assign", "device_id": "dev-phone-force", "profile_id": "tiviss-compat"})
        )
        assert forced.payload["success"] is True
        assert forced.payload["result"]["assignment"]["profile_id"] == "tiviss-compat"
    finally:
        app.stop()


def test_agent_spine_auto_assign_opt_in(tmp_path):
    """Auto-assign is opt-in via agent.auto_assign=true — legacy explicit remains default."""
    import yaml

    cfg = tmp_path / "core.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "core": {"name": "C.O.R.E.", "version": "0.2.1"},
                "environment": "development",
                "agent": {"auto_assign": True},
            }
        )
    )
    app = CoreApplication(config_path=cfg)
    app.start()
    try:
        # Register device — auto-assign should create agent without explicit assign
        dev_msg = Message(
            source="test",
            destination="service:resources",
            message_type="ANY",
            payload={
                "operation": "register",
                "resource_id": "dev-auto-optin",
                "name": "Auto Optin Phone",
                "resource_type": "device",
                "capabilities": ["camera"],
                "metadata": {"device_type": "phone", "platform": "ios"},
            },
        )
        resp = app.communication.send(dev_msg)
        assert resp.payload["success"] is True
        # Auto-assigned agent should exist
        assert app.scheduler.assignment_count() == 1
        assert app.scheduler.get_assignment("dev-auto-optin").profile_id in ("asis-offload", "asis-local", "tiviss-compat")
        # Explicit assign should be idempotent (preserve legacy)
        explicit = app.communication.send(
            Message(source="test", destination="service:agent", message_type="ANY", payload={"operation": "assign", "device_id": "dev-auto-optin"})
        )
        assert explicit.payload["success"] is True
        assert explicit.payload["result"]["assignment"]["profile_id"] == app.scheduler.get_assignment("dev-auto-optin").profile_id

        # With auto_assign=false, no auto-assign
        cfg2 = tmp_path / "core2.yaml"
        cfg2.write_text(
            yaml.safe_dump(
                {
                    "core": {"name": "C.O.R.E.", "version": "0.2.1"},
                    "environment": "development",
                    "agent": {"auto_assign": False},
                }
            )
        )
        app2 = CoreApplication(config_path=cfg2)
        app2.start()
        try:
            resp2 = app2.communication.send(
                Message(source="test", destination="service:resources", message_type="ANY", payload={"operation": "register", "resource_id": "dev-no-auto", "name": "No Auto", "resource_type": "device", "capabilities": ["camera"], "metadata": {"device_type": "phone", "platform": "ios"}})
            )
            assert resp2.payload["success"] is True
            assert app2.scheduler.assignment_count() == 0
        finally:
            app2.stop()
    finally:
        app.stop()
