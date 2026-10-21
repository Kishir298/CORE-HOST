import pytest

from core.application import CoreApplication
from core.health.models import HealthStatus
from core.runtime import RuntimeState
from core.services.models import ServiceStatus


@pytest.fixture
def app() -> CoreApplication:
    application = CoreApplication()
    application.start()
    yield application
    application.stop()


def test_application_loads_configuration_on_start(app):
    assert app.configuration.load_count() == 1
    assert app.configuration.current() is not None


def test_application_configuration_applies_logging_level(app, tmp_path):
    import logging

    import yaml

    config_file = tmp_path / "core.yaml"

    config_file.write_text(
        yaml.safe_dump(
            {
                "core": {"name": "C.O.R.E.", "version": "0.2.0"},
                "environment": "development",
                "logging": {"level": "DEBUG"},
            }
        )
    )

    application = CoreApplication(config_path=config_file)
    application.start()

    try:
        assert application.configuration.load_count() == 1
        assert application.logger._logger.level == logging.DEBUG
    finally:
        application.stop()


def test_application_initializes_all_components_in_order(app):
    expected = [
        "configuration",
        "logging",
        "security",
        "resources",
        "organization",
        "events",
        "communication",
        "routing",
        "health",
        "rescs",
        "dependencies",
        "services",
        "core",
    ]

    assert app.runtime.initialized_components() == expected

    for name in expected:
        assert app.runtime.component_state(name).value == "running"


def test_application_starts_all_internal_services(app):
    assert app.services.count() == 9

    for service in app.services.list():
        assert service.status == ServiceStatus.RUNNING


def test_application_shutdown_stops_subsystems():
    application = CoreApplication()
    application.start()

    assert application.communication.is_running is True
    assert application.events.is_running is True
    assert application.security.is_running is True

    application.stop()

    assert application.communication.is_running is False
    assert application.events.is_running is False
    assert application.security.is_running is False
    assert application.state == RuntimeState.STOPPED


def test_application_shutdown_occurs_in_reverse_dependency_order(app):
    start_order = app.runtime.initialized_components()

    assert start_order == [
        "configuration",
        "logging",
        "security",
        "resources",
        "organization",
        "events",
        "communication",
        "routing",
        "health",
        "rescs",
        "dependencies",
        "services",
        "core",
    ]


def test_application_emits_lifecycle_events(app):
    assert app.events.event_count() >= 1


def test_application_health_reflects_real_component_state(app):
    results = app.health_check()

    statuses = {result.component_id: result.status for result in results}

    assert statuses["runtime"] == HealthStatus.HEALTHY
    assert statuses["communication"] == HealthStatus.HEALTHY
    assert statuses["events"] == HealthStatus.HEALTHY
    assert statuses["security"] == HealthStatus.HEALTHY
    assert statuses["routing"] == HealthStatus.HEALTHY


def test_application_health_degrades_when_service_fails(app):
    service = app.services.get("events")
    service.status = ServiceStatus.FAILED

    results = app.health_check()

    services_result = next(
        result
        for result in results
        if result.component_id == "services"
    )

    assert services_result.status == HealthStatus.DEGRADED


def test_application_health_degrades_when_communication_stops(app):
    app.communication.stop()

    results = app.health_check()

    communication_result = next(
        result
        for result in results
        if result.component_id == "communication"
    )

    assert communication_result.status == HealthStatus.UNHEALTHY


def test_application_restart_reinitializes_without_error():
    application = CoreApplication()
    application.start()
    application.restart()

    assert application.state == RuntimeState.RUNNING
    assert application.configuration.load_count() == 2

    application.stop()
