import pytest

from core.application import CoreApplication
from core.communication import Message
from core.health import HealthMonitor, HealthResult, HealthStatus


@pytest.fixture
def app() -> CoreApplication:
    application = CoreApplication()
    application.start()
    yield application
    application.stop()


def _dispatch(app, operation, **kwargs):
    return app.routing.route(
        Message(
            source="asis",
            destination="_",
            message_type="RESOURCES.LIST",
            payload={"operation": operation, **kwargs},
        )
    ).payload


def test_health_check_reports_registered_component_count(app):
    assert app.health.count() >= 11


def test_runtime_health_healthy_when_running(app):
    result = app.health.check("runtime")

    assert result.status == HealthStatus.HEALTHY


def test_services_health_reports_running_total(app):
    result = app.health.check("services")

    assert result.status == HealthStatus.HEALTHY
    assert "running" in result.message


def test_communication_stop_marks_unhealthy(app):
    app.communication.stop()

    result = app.health.check("communication")

    assert result.status == HealthStatus.UNHEALTHY


def test_check_result_stored_and_retrieved(app):
    app.health.check("routing")

    assert app.health.get("routing").component_id == "routing"
    assert app.health.last_checked("routing") is not None
    assert app.health.check_count("routing") >= 1


def test_health_changed_event_published_on_status_transition(app):
    seen = []
    app.events.subscribe("HEALTH_CHANGED", lambda event: seen.append(event.payload))

    state = {"healthy": False}

    def flapping():
        state["healthy"] = not state["healthy"]
        return HealthResult(
            component_id="probe",
            status=(
                HealthStatus.HEALTHY
                if state["healthy"]
                else HealthStatus.UNHEALTHY
            ),
            message="probe",
        )

    app.health.register("probe", flapping)

    app.health.check("probe")
    app.health.check("probe")

    probe_events = [
        payload
        for payload in seen
        if payload["component_id"] == "probe"
    ]

    assert probe_events, "no HEALTH_CHANGED event published for probe"
    assert probe_events[0]["status"] == "healthy"


def test_health_changed_event_emitted_when_status_degrades(app):
    seen = []
    app.events.subscribe("HEALTH_CHANGED", lambda event: seen.append(event.payload))

    app.communication.stop()
    app.health.check("communication")

    assert any(
        payload["component_id"] == "communication"
        and payload["status"] == "unhealthy"
        for payload in seen
    )


def test_health_status_via_service_dispatcher(app):
    response = app.routing.route(
        Message(
            source="asis",
            destination="_",
            message_type="HEALTH.STATUS",
            payload={"operation": "status"},
        )
    )

    assert response.payload["success"] is True

    result = response.payload["result"]

    assert "checks" in result
    assert "overall" in result
    assert any(
        check["component_id"] == "communication"
        for check in result["checks"]
    )


def test_health_monitor_on_change_called_only_on_transition():
    transitions = []

    monitor = HealthMonitor(on_change=transitions.append)
    monitor.register("x", lambda: HealthResult(
        component_id="x", status=HealthStatus.HEALTHY, message="ok",
    ))

    monitor.check("x")
    monitor.check("x")

    assert len(transitions) == 1


def test_health_monitor_invalid_result_becomes_unhealthy():
    monitor = HealthMonitor()
    monitor.register("x", lambda: "not-a-result")

    result = monitor.check("x")

    assert result.status == HealthStatus.UNHEALTHY
    assert monitor.failure_count("x") == 1


def test_health_overall_status_healthy_after_full_initialization(app):
    status = app.health.overall_status()

    assert status == HealthStatus.HEALTHY
