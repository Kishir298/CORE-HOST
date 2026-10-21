import pytest

from core.runtime import (
    ComponentState,
    Runtime,
    RuntimeError,
    RuntimeState,
)


def test_components_enabled_by_default():
    runtime = Runtime()

    runtime.register_component("component", lambda: None)

    assert runtime.is_enabled("component") is True


def test_set_enabled_flips_is_enabled():
    runtime = Runtime()

    runtime.register_component("component", lambda: None)

    runtime.set_enabled("component", False)

    assert runtime.is_enabled("component") is False

    runtime.set_enabled("component", True)

    assert runtime.is_enabled("component") is True


def test_set_enabled_requires_registered_component():
    runtime = Runtime()

    with pytest.raises(RuntimeError):
        runtime.set_enabled("missing", False)

    with pytest.raises(RuntimeError):
        runtime.is_enabled("missing")


def test_disabled_component_is_skipped_during_startup():
    runtime = Runtime()
    initialized = []

    def initialize():
        initialized.append("component")

    runtime.register_component("enabled", lambda: initialized.append("enabled"))
    runtime.register_component("disabled", initialize)

    runtime.set_enabled("disabled", False)

    runtime.start()

    assert initialized == ["enabled"]
    assert runtime.state == RuntimeState.RUNNING
    assert runtime.initialized_components() == ["enabled"]
    assert runtime.component_state("disabled") == ComponentState.DISABLED


def test_disabled_component_shutdown_handler_does_not_run():
    runtime = Runtime()
    events = []

    def initialize():
        events.append("start")

    def shutdown():
        events.append("stop")

    runtime.register_component("enabled", initialize, shutdown)
    runtime.register_component("disabled", initialize, shutdown)

    runtime.set_enabled("disabled", False)

    runtime.start()
    runtime.stop()

    assert events == ["start", "stop"]
    assert runtime.component_state("disabled") == ComponentState.DISABLED


def test_enabled_component_cannot_depend_on_disabled_component():
    runtime = Runtime()

    runtime.register_component("database", lambda: None)
    runtime.register_component(
        "service",
        lambda: None,
        dependencies=["database"],
    )

    runtime.set_enabled("database", False)

    with pytest.raises(RuntimeError) as excinfo:
        runtime.start()

    assert "disabled component 'database'" in str(excinfo.value)
    assert runtime.state == RuntimeState.FAILED


def test_disabled_components_can_be_re_enabled():
    runtime = Runtime()
    initialized = []

    def initialize():
        initialized.append("component")

    runtime.register_component("component", initialize)

    runtime.set_enabled("component", False)
    runtime.start()

    assert initialized == []
    runtime.stop()

    runtime.set_enabled("component", True)
    runtime.start()

    assert initialized == ["component"]
    assert runtime.component_state("component") == ComponentState.RUNNING


def test_set_enabled_rejected_while_runtime_active():
    runtime = Runtime()

    runtime.register_component("component", lambda: None)
    runtime.start()

    with pytest.raises(RuntimeError):
        runtime.set_enabled("component", False)

    runtime.stop()