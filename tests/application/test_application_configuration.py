import pytest

from core.application import CoreApplication
from core.runtime import ComponentState, RuntimeState
from core.services.models import ServiceStatus

MINIMAL_CONFIG = """\
core:
  name: "C.O.R.E."
  version: "0.2.0"
environment: "development"
"""


def _write_config(tmp_path, content):
    path = tmp_path / "core.yaml"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_defaults_enable_all_components(tmp_path):
    app = CoreApplication(
        config_path=_write_config(tmp_path, MINIMAL_CONFIG)
    )

    app.start()

    assert app.is_running is True
    assert app.communication.is_running is True
    assert app.runtime.component_state("communication") == (
        ComponentState.RUNNING
    )

    app.stop()


def test_components_section_disable_changes_runtime_behavior(tmp_path):
    config = MINIMAL_CONFIG + """\
components:
  communication:
    enabled: false
"""

    app = CoreApplication(
        config_path=_write_config(tmp_path, config)
    )

    app.start()

    assert app.runtime.component_state("communication") == (
        ComponentState.DISABLED
    )
    assert app.communication.is_running is False
    assert "communication" not in app.runtime.initialized_components()

    assert app.runtime.component_state("events") == ComponentState.RUNNING
    assert app.events.is_running is True

    assert app.is_running is True

    app.stop()


def test_legacy_section_disable_controls_runtime(tmp_path):
    config = MINIMAL_CONFIG + """\
communication:
  enabled: false
"""

    app = CoreApplication(
        config_path=_write_config(tmp_path, config)
    )

    app.start()

    assert app.communication.is_running is False
    assert app.runtime.component_state("communication") == (
        ComponentState.DISABLED
    )

    app.stop()


def test_disable_cascades_to_dependents(tmp_path):
    config = MINIMAL_CONFIG + """\
components:
  communication:
    enabled: false
"""

    app = CoreApplication(
        config_path=_write_config(tmp_path, config)
    )

    app.start()

    assert app.runtime.component_state("routing") == (
        ComponentState.DISABLED
    )
    assert app.runtime.component_state("core") == ComponentState.DISABLED
    assert "routing" not in app.runtime.initialized_components()
    assert "core" not in app.runtime.initialized_components()

    app.stop()


def test_disabled_subsystem_services_are_not_started(tmp_path):
    config = MINIMAL_CONFIG + """\
components:
  communication:
    enabled: false
"""

    app = CoreApplication(
        config_path=_write_config(tmp_path, config)
    )

    app.start()

    assert app.services.get("communication").status == ServiceStatus.REGISTERED
    assert app.services.get("routing").status == ServiceStatus.REGISTERED
    assert app.services.get("health").status == ServiceStatus.RUNNING

    app.stop()


def test_disabled_subsystem_has_no_health_check(tmp_path):
    config = MINIMAL_CONFIG + """\
components:
  communication:
    enabled: false
"""

    app = CoreApplication(
        config_path=_write_config(tmp_path, config)
    )

    app.start()

    result = app.health.get("communication")

    assert result.message == "Component not registered."

    overall = app.health.overall_status()

    for check in app.health.check_all():
        assert check.component_id != "communication"

    assert overall is not None

    app.stop()


def test_restart_preserves_disable_policy(tmp_path):
    config = MINIMAL_CONFIG + """\
components:
  communication:
    enabled: false
"""

    app = CoreApplication(
        config_path=_write_config(tmp_path, config)
    )

    app.start()
    app.restart()

    assert app.communication.is_running is False
    assert app.is_running is True

    app.stop()


def test_invalid_component_configuration_fails_safely(tmp_path):
    config = MINIMAL_CONFIG + """\
components:
  communication:
    enabled: "yes"
"""

    app = CoreApplication(
        config_path=_write_config(tmp_path, config)
    )

    with pytest.raises(ValueError):
        app.start()

    assert app.state == RuntimeState.STOPPED