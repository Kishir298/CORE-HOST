import pytest

from core.configuration import Configuration, ConfigurationValidator


def _config(data):
    return Configuration(data=data, environment="development")


def test_components_section_with_bool_enabled_is_valid():
    config = _config(
        {
            "core": {"name": "C.O.R.E.", "version": "0.2.0"},
            "components": {"communication": {"enabled": True}},
        }
    )

    assert ConfigurationValidator().is_valid(config) is True


def test_missing_components_section_is_valid():
    config = _config({"core": {"name": "C.O.R.E.", "version": "0.2.0"}})

    assert ConfigurationValidator().is_valid(config) is True


def test_components_must_be_dictionary():
    config = _config(
        {
            "core": {"name": "C.O.R.E.", "version": "0.2.0"},
            "components": "enabled",
        }
    )

    with pytest.raises(ValueError) as excinfo:
        ConfigurationValidator().validate(config)

    assert "components must be a dictionary" in str(excinfo.value)


def test_component_settings_must_be_dictionary():
    config = _config(
        {
            "core": {"name": "C.O.R.E.", "version": "0.2.0"},
            "components": {"communication": False},
        }
    )

    with pytest.raises(ValueError) as excinfo:
        ConfigurationValidator().validate(config)

    assert "components.communication must be a dictionary" in str(excinfo.value)


def test_component_enabled_must_be_boolean():
    config = _config(
        {
            "core": {"name": "C.O.R.E.", "version": "0.2.0"},
            "components": {"communication": {"enabled": "yes"}},
        }
    )

    with pytest.raises(ValueError) as excinfo:
        ConfigurationValidator().validate(config)

    assert "components.communication.enabled must be a boolean" in str(
        excinfo.value
    )


def test_component_names_must_be_non_empty_strings():
    config = _config(
        {
            "core": {"name": "C.O.R.E.", "version": "0.2.0"},
            "components": {"": {"enabled": True}},
        }
    )

    with pytest.raises(ValueError) as excinfo:
        ConfigurationValidator().validate(config)

    assert "non-empty strings" in str(excinfo.value)