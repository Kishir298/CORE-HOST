from pathlib import Path

import pytest

from core.configuration import (
    Configuration,
    ConfigurationLoader,
    ConfigurationManager,
    ConfigurationValidator,
)


def test_configuration_get():
    config = Configuration(
        data={
            "core": {
                "name": "C.O.R.E.",
                "version": "0.1",
            }
        }
    )

    assert config.get("core.name") == "C.O.R.E."
    assert config.get("core.version") == "0.1"
    assert config.get("missing.value") is None
    assert config.get("missing.value", "default") == "default"


def test_configuration_set():
    config = Configuration()

    config.set("core.name", "C.O.R.E.")
    config.set("communication.port", 5000)

    assert config.get("core.name") == "C.O.R.E."
    assert config.get("communication.port") == 5000


def test_configuration_has():
    config = Configuration(
        data={"core": {"name": "C.O.R.E."}}
    )

    assert config.has("core.name")
    assert not config.has("core.version")


def test_configuration_as_dict():
    data = {
        "core": {
            "name": "C.O.R.E.",
            "version": "0.1",
        }
    }

    config = Configuration(data=data)

    assert config.as_dict() == data


def test_configuration_loader(tmp_path: Path):
    config_file = tmp_path / "core.yaml"

    config_file.write_text(
        """
core:
  name: C.O.R.E.
  version: "0.1"

communication:
  enabled: true
""",
        encoding="utf-8",
    )

    loader = ConfigurationLoader()
    config = loader.load(config_file)

    assert config.get("core.name") == "C.O.R.E."
    assert config.get("core.version") == "0.1"
    assert config.get("communication.enabled") is True
    assert config.environment == "development"


def test_configuration_loader_environment(tmp_path: Path):
    config_file = tmp_path / "core.yaml"

    config_file.write_text(
        """
core:
  name: C.O.R.E.
  version: "0.1"
""",
        encoding="utf-8",
    )

    loader = ConfigurationLoader()
    config = loader.load(
        config_file,
        environment="production",
    )

    assert config.environment == "production"


def test_configuration_loader_missing_file(tmp_path: Path):
    loader = ConfigurationLoader()

    with pytest.raises(FileNotFoundError):
        loader.load(tmp_path / "missing.yaml")


def test_configuration_loader_invalid_root(tmp_path: Path):
    config_file = tmp_path / "invalid.yaml"

    config_file.write_text(
        "- invalid\n- configuration",
        encoding="utf-8",
    )

    loader = ConfigurationLoader()

    with pytest.raises(ValueError):
        loader.load(config_file)


def test_configuration_validator_valid():
    config = Configuration(
        data={
            "core": {
                "name": "C.O.R.E.",
                "version": "0.1",
            }
        },
        environment="development",
    )

    validator = ConfigurationValidator()
    validator.validate(config)


def test_configuration_validator_missing_values():
    config = Configuration(
        data={},
        environment="development",
    )

    validator = ConfigurationValidator()

    with pytest.raises(ValueError):
        validator.validate(config)


def test_configuration_manager(tmp_path: Path):
    config_file = tmp_path / "core.yaml"

    config_file.write_text(
        """
core:
  name: C.O.R.E.
  version: "0.1"
""",
        encoding="utf-8",
    )

    manager = ConfigurationManager()

    manager.load(config_file)

    assert manager.get("core.name") == "C.O.R.E."
    assert manager.get("core.version") == "0.1"

    manager.set("core.name", "C.O.R.E. Engine")

    assert manager.get("core.name") == "C.O.R.E. Engine"


def test_configuration_manager_requires_load():
    manager = ConfigurationManager()

    with pytest.raises(RuntimeError):
        manager.get("core.name")


def test_configuration_manager_validation(tmp_path: Path):
    config_file = tmp_path / "core.yaml"

    config_file.write_text(
        """
core:
  name: C.O.R.E.
  version: "0.1"
""",
        encoding="utf-8",
    )

    manager = ConfigurationManager()
    manager.load(config_file)

    manager.validate()
