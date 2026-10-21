import pytest

from core.runtime import (
    ComponentState,
    Runtime,
    RuntimeError,
    RuntimeState,
)


def test_runtime_starts_and_stops():
    runtime = Runtime()

    runtime.start()

    assert runtime.state == RuntimeState.RUNNING

    runtime.stop()

    assert runtime.state == RuntimeState.STOPPED


def test_runtime_initializes_components():
    runtime = Runtime()
    initialized = []

    def initialize():
        initialized.append("component")

    runtime.register_component(
        "test",
        initialize,
    )

    runtime.start()

    assert initialized == ["component"]
    assert runtime.state == RuntimeState.RUNNING


def test_runtime_component_shutdown():
    runtime = Runtime()
    events = []

    def initialize():
        events.append("start")

    def shutdown():
        events.append("stop")

    runtime.register_component(
        "test",
        initialize,
        shutdown,
    )

    runtime.start()
    runtime.stop()

    assert events == ["start", "stop"]


def test_shutdown_runs_in_reverse_order():
    runtime = Runtime()
    events = []

    runtime.register_component(
        "first",
        lambda: events.append("first-start"),
        lambda: events.append("first-stop"),
    )

    runtime.register_component(
        "second",
        lambda: events.append("second-start"),
        lambda: events.append("second-stop"),
    )

    runtime.start()
    runtime.stop()

    assert events == [
        "first-start",
        "second-start",
        "second-stop",
        "first-stop",
    ]


def test_dependency_aware_startup_order():
    runtime = Runtime()
    events = []

    runtime.register_component(
        "database",
        lambda: events.append("database"),
    )

    runtime.register_component(
        "service",
        lambda: events.append("service"),
        dependencies=["database"],
    )

    runtime.register_component(
        "application",
        lambda: events.append("application"),
        dependencies=["service"],
    )

    runtime.start()

    assert events == [
        "database",
        "service",
        "application",
    ]


def test_dependency_aware_startup_ignores_registration_order():
    runtime = Runtime()
    events = []

    runtime.register_component(
        "application",
        lambda: events.append("application"),
        dependencies=["service"],
    )

    runtime.register_component(
        "service",
        lambda: events.append("service"),
        dependencies=["database"],
    )

    runtime.register_component(
        "database",
        lambda: events.append("database"),
    )

    runtime.start()

    assert events == [
        "database",
        "service",
        "application",
    ]


def test_dependency_aware_shutdown_order():
    runtime = Runtime()
    events = []

    runtime.register_component(
        "database",
        lambda: events.append("database-start"),
        lambda: events.append("database-stop"),
    )

    runtime.register_component(
        "service",
        lambda: events.append("service-start"),
        lambda: events.append("service-stop"),
        dependencies=["database"],
    )

    runtime.register_component(
        "application",
        lambda: events.append("application-start"),
        lambda: events.append("application-stop"),
        dependencies=["service"],
    )

    runtime.start()
    runtime.stop()

    assert events == [
        "database-start",
        "service-start",
        "application-start",
        "application-stop",
        "service-stop",
        "database-stop",
    ]


def test_get_start_order():
    runtime = Runtime()

    runtime.register_component(
        "database",
        lambda: None,
    )

    runtime.register_component(
        "service",
        lambda: None,
        dependencies=["database"],
    )

    runtime.register_component(
        "application",
        lambda: None,
        dependencies=["service"],
    )

    assert runtime.get_start_order() == [
        "database",
        "service",
        "application",
    ]


def test_missing_dependency_fails_startup():
    runtime = Runtime()

    runtime.register_component(
        "service",
        lambda: None,
        dependencies=["missing"],
    )

    with pytest.raises(RuntimeError, match="Missing dependency"):
        runtime.start()

    assert runtime.state == RuntimeState.FAILED


def test_circular_dependency_fails_startup():
    runtime = Runtime()

    runtime.register_component(
        "first",
        lambda: None,
        dependencies=["second"],
    )

    runtime.register_component(
        "second",
        lambda: None,
        dependencies=["first"],
    )

    with pytest.raises(RuntimeError, match="Circular dependency"):
        runtime.start()

    assert runtime.state == RuntimeState.FAILED


def test_set_dependencies():
    runtime = Runtime()

    runtime.register_component(
        "database",
        lambda: None,
    )

    runtime.register_component(
        "service",
        lambda: None,
    )

    runtime.set_dependencies(
        "service",
        ["database"],
    )

    assert runtime.get_dependencies("service") == ["database"]


def test_unknown_component_dependency_update_fails():
    runtime = Runtime()

    with pytest.raises(RuntimeError, match="Component not registered"):
        runtime.set_dependencies(
            "missing",
            ["database"],
        )


def test_unknown_component_dependency_lookup_fails():
    runtime = Runtime()

    with pytest.raises(RuntimeError, match="Component not registered"):
        runtime.get_dependencies("missing")


def test_start_is_idempotent():
    runtime = Runtime()
    count = 0

    def initialize():
        nonlocal count
        count += 1

    runtime.register_component(
        "test",
        initialize,
    )

    runtime.start()
    runtime.start()

    assert count == 1
    assert runtime.state == RuntimeState.RUNNING


def test_stop_is_idempotent():
    runtime = Runtime()
    count = 0

    def shutdown():
        nonlocal count
        count += 1

    runtime.register_component(
        "test",
        lambda: None,
        shutdown,
    )

    runtime.start()
    runtime.stop()
    runtime.stop()

    assert count == 1
    assert runtime.state == RuntimeState.STOPPED


def test_restart():
    runtime = Runtime()
    events = []

    runtime.register_component(
        "test",
        lambda: events.append("start"),
        lambda: events.append("stop"),
    )

    runtime.start()
    runtime.restart()

    assert runtime.state == RuntimeState.RUNNING
    assert events == [
        "start",
        "stop",
        "start",
    ]


def test_failed_initialization():
    runtime = Runtime()

    def broken_initializer():
        raise ValueError("broken component")

    runtime.register_component(
        "broken",
        broken_initializer,
    )

    with pytest.raises(RuntimeError):
        runtime.start()

    assert runtime.state == RuntimeState.FAILED
    assert runtime.error is not None


def test_failed_component_shutdown_does_not_stop_other_shutdowns():
    runtime = Runtime()
    events = []

    runtime.register_component(
        "first",
        lambda: None,
        lambda: events.append("first"),
    )

    def broken_shutdown():
        raise RuntimeError("shutdown failed")

    runtime.register_component(
        "broken",
        lambda: None,
        broken_shutdown,
    )

    runtime.register_component(
        "last",
        lambda: None,
        lambda: events.append("last"),
    )

    runtime.start()
    runtime.stop()

    assert runtime.state == RuntimeState.STOPPED
    assert "first" in events
    assert "last" in events


def test_component_count():
    runtime = Runtime()

    runtime.register_component(
        "one",
        lambda: None,
    )

    runtime.register_component(
        "two",
        lambda: None,
    )

    assert runtime.component_count() == 2


def test_clear_components():
    runtime = Runtime()

    runtime.register_component(
        "one",
        lambda: None,
    )

    runtime.clear_components()

    assert runtime.component_count() == 0


def test_cannot_clear_active_runtime():
    runtime = Runtime()

    runtime.register_component(
        "one",
        lambda: None,
    )

    runtime.start()

    with pytest.raises(RuntimeError):
        runtime.clear_components()


def test_invalid_start_state():
    runtime = Runtime()

    runtime._state = RuntimeState.INITIALIZING

    with pytest.raises(RuntimeError):
        runtime.start()


def test_error_is_cleared_on_successful_restart():
    runtime = Runtime()

    attempts = 0

    def initializer():
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            raise ValueError("temporary failure")

    runtime.register_component(
        "test",
        initializer,
    )

    with pytest.raises(RuntimeError):
        runtime.start()

    assert runtime.state == RuntimeState.FAILED

    runtime.start()

    assert runtime.state == RuntimeState.RUNNING
    assert runtime.error is None


def test_partial_startup_is_cleaned_up_on_failure():
    runtime = Runtime()
    events = []

    runtime.register_component(
        "first",
        lambda: events.append("first-start"),
        lambda: events.append("first-stop"),
    )

    def broken_initializer():
        raise ValueError("broken component")

    runtime.register_component(
        "second",
        broken_initializer,
        lambda: events.append("second-stop"),
    )

    runtime.register_component(
        "third",
        lambda: events.append("third-start"),
        lambda: events.append("third-stop"),
        dependencies=["second"],
    )

    with pytest.raises(RuntimeError):
        runtime.start()

    assert runtime.state == RuntimeState.FAILED

    assert events == [
        "first-start",
        "first-stop",
    ]

    assert runtime.initialized_component_count() == 0


def test_stop_after_failed_start_does_not_shutdown_twice():
    runtime = Runtime()
    shutdown_count = 0

    def shutdown():
        nonlocal shutdown_count
        shutdown_count += 1

    runtime.register_component(
        "first",
        lambda: None,
        shutdown,
    )

    def broken_initializer():
        raise ValueError("broken component")

    runtime.register_component(
        "second",
        broken_initializer,
    )

    with pytest.raises(RuntimeError):
        runtime.start()

    runtime.stop()

    assert shutdown_count == 1
    assert runtime.state == RuntimeState.STOPPED


def test_component_state_tracks_lifecycle():
    runtime = Runtime()

    runtime.register_component(
        "test",
        lambda: None,
        lambda: None,
    )

    assert runtime.component_state("test") == ComponentState.REGISTERED

    runtime.start()

    assert runtime.component_state("test") == ComponentState.RUNNING

    runtime.stop()

    assert runtime.component_state("test") == ComponentState.STOPPED


def test_component_states_after_partial_failure():
    runtime = Runtime()

    runtime.register_component(
        "first",
        lambda: None,
    )

    def broken_initializer():
        raise ValueError("broken component")

    runtime.register_component(
        "second",
        broken_initializer,
        dependencies=["first"],
    )

    with pytest.raises(RuntimeError):
        runtime.start()

    assert runtime.component_state("first") == ComponentState.STOPPED
    assert runtime.component_state("second") == ComponentState.FAILED


def test_initializer_failure_is_attributed_to_component():
    runtime = Runtime()

    def broken_initializer():
        raise ValueError("broken component")

    runtime.register_component(
        "broken",
        broken_initializer,
    )

    with pytest.raises(
        RuntimeError,
        match="component 'broken'",
    ) as excinfo:
        runtime.start()

    assert isinstance(excinfo.value.__cause__, ValueError)

    assert runtime.error is not None
    assert runtime.state == RuntimeState.FAILED


def test_initialized_components_listing():
    runtime = Runtime()

    runtime.register_component(
        "database",
        lambda: None,
    )

    runtime.register_component(
        "service",
        lambda: None,
        dependencies=["database"],
    )

    assert runtime.initialized_components() == []

    runtime.start()

    assert runtime.initialized_components() == [
        "database",
        "service",
    ]

    assert runtime.initialized_component_count() == 2

    runtime.stop()

    assert runtime.initialized_components() == []


def test_shutdown_errors_are_recorded():
    runtime = Runtime()

    def broken_shutdown():
        raise RuntimeError("shutdown failed")

    runtime.register_component(
        "broken",
        lambda: None,
        broken_shutdown,
    )

    runtime.register_component(
        "healthy",
        lambda: None,
        lambda: None,
    )

    runtime.start()
    runtime.stop()

    assert runtime.state == RuntimeState.STOPPED

    assert len(runtime.shutdown_errors) == 1

    name, error = runtime.shutdown_errors[0]

    assert name == "broken"
    assert isinstance(error, RuntimeError)

    assert runtime.component_state("broken") == ComponentState.FAILED
    assert runtime.component_state("healthy") == ComponentState.STOPPED


def test_shutdown_errors_are_cleared_on_restart():
    runtime = Runtime()

    def broken_shutdown():
        raise RuntimeError("shutdown failed")

    runtime.register_component(
        "broken",
        lambda: None,
        broken_shutdown,
    )

    runtime.start()
    runtime.stop()

    assert len(runtime.shutdown_errors) == 1

    runtime.start()

    assert runtime.shutdown_errors == []
    assert runtime.state == RuntimeState.RUNNING

    runtime.stop()


def test_shutdown_error_does_not_block_restart_cleanup():
    runtime = Runtime()

    def broken_shutdown():
        raise RuntimeError("shutdown failed")

    runtime.register_component(
        "broken",
        lambda: None,
        broken_shutdown,
    )

    runtime.start()
    runtime.stop()

    events = []

    runtime.register_component(
        "later",
        lambda: events.append("later-start"),
        lambda: events.append("later-stop"),
    )

    runtime.start()
    runtime.stop()

    assert events == [
        "later-start",
        "later-stop",
    ]


def test_component_state_unknown_component_fails():
    runtime = Runtime()

    with pytest.raises(RuntimeError, match="Component not registered"):
        runtime.component_state("missing")


def test_components_reach_running_state_together():
    runtime = Runtime()

    states = {}

    def record(name):
        def initialize():
            states[name] = runtime.component_state(name)

        return initialize

    runtime.register_component("alpha", record("alpha"))
    runtime.register_component("beta", record("beta"))

    runtime.start()

    assert states == {
        "alpha": ComponentState.STARTING,
        "beta": ComponentState.STARTING,
    }

    assert runtime.component_state("alpha") == ComponentState.RUNNING
    assert runtime.component_state("beta") == ComponentState.RUNNING