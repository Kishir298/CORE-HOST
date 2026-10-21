import pytest

from core.application import CoreApplication
from core.runtime import RuntimeState


def test_application_initializes():
    app = CoreApplication()

    assert app is not None
    assert app._initialized is True


def test_application_registers_runtime_components():
    app = CoreApplication()

    expected_components = {
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
    }

    assert app.runtime.component_count() == len(expected_components)

    start_order = app.runtime.get_start_order()

    assert set(start_order) == expected_components
    assert start_order.index("configuration") < start_order.index("logging")
    assert start_order.index("configuration") < start_order.index("security")
    assert start_order.index("resources") < start_order.index("organization")
    assert start_order.index("events") < start_order.index("communication")
    assert start_order.index("communication") < start_order.index("routing")
    assert start_order.index("dependencies") < start_order.index("services")


def test_application_runtime_dependencies():
    app = CoreApplication()

    assert app.runtime.get_dependencies("logging") == [
        "configuration",
    ]

    assert app.runtime.get_dependencies("security") == [
        "configuration",
    ]

    assert app.runtime.get_dependencies("resources") == [
        "configuration",
    ]

    assert app.runtime.get_dependencies("organization") == [
        "resources",
    ]

    assert app.runtime.get_dependencies("communication") == [
        "events",
        "security",
    ]

    assert app.runtime.get_dependencies("routing") == [
        "communication",
    ]

    assert app.runtime.get_dependencies("services") == [
        "dependencies",
        "health",
    ]


def test_application_core_depends_on_all_subsystems():
    app = CoreApplication()

    dependencies = set(
        app.runtime.get_dependencies("core")
    )

    expected = {
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
    }

    assert dependencies == expected


def test_application_starts():
    app = CoreApplication()

    app.start()

    assert app.state == RuntimeState.RUNNING
    assert app.is_running is True


def test_application_stops():
    app = CoreApplication()

    app.start()
    app.stop()

    assert app.state == RuntimeState.STOPPED
    assert app.is_running is False


def test_application_start_and_stop_are_idempotent():
    app = CoreApplication()

    app.start()
    app.start()

    assert app.state == RuntimeState.RUNNING

    app.stop()
    app.stop()

    assert app.state == RuntimeState.STOPPED


def test_application_restart():
    app = CoreApplication()

    app.start()
    app.restart()

    assert app.state == RuntimeState.RUNNING
    assert app.is_running is True

    app.stop()


def test_application_health_check():
    app = CoreApplication()

    results = app.health_check()

    assert results is not None


def test_application_exposes_core_subsystems():
    app = CoreApplication()

    assert app.configuration is not None
    assert app.logger is not None
    assert app.communication is not None
    assert app.resources is not None
    assert app.organization is not None
    assert app.events is not None
    assert app.health is not None
    assert app.dependencies is not None
    assert app.security is not None
    assert app.services is not None
    assert app.routing is not None


def test_application_dependency_manager_is_configured():
    app = CoreApplication()

    assert app.dependencies is not None

    registered_dependencies = app.dependencies.get_dependencies("core")

    assert set(registered_dependencies) == {
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
    }


def test_application_services_are_registered():
    app = CoreApplication()

    expected_service_ids = {
        "communication",
        "events",
        "resources",
        "organization",
        "routing",
        "health",
        "runtime",
        "rescs",
        "agent",
    }

    for service_id in expected_service_ids:
        assert app.services.get(service_id) is not None


def test_application_runtime_is_stopped_initially():
    app = CoreApplication()

    assert app.state == RuntimeState.STOPPED
    assert app.is_running is False


def test_application_start_order_places_core_last():
    app = CoreApplication()

    start_order = app.runtime.get_start_order()

    assert start_order[-1] == "core"


def test_application_shutdown_uses_reverse_dependency_order():
    app = CoreApplication()

    start_order = app.runtime.get_start_order()

    assert start_order[-1] == "core"

    app.start()
    app.stop()

    assert app.state == RuntimeState.STOPPED


def test_application_cannot_start_uninitialized_runtime():
    app = CoreApplication()

    app._initialized = False

    with pytest.raises(RuntimeError):
        app.start()