from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Configuration:
    """
    Represents the complete C.O.R.E. configuration.

    Configuration values are stored as nested dictionaries so components
    can use paths such as:

        core.name
        communication.port
        services.example.enabled
    """

    data: dict[str, Any] = field(default_factory=dict)
    environment: str = "development"

    def __post_init__(self) -> None:
        if not isinstance(self.data, dict):
            raise TypeError("Configuration data must be a dictionary.")

        if not isinstance(self.environment, str):
            raise TypeError(
                "Configuration environment must be a string."
            )

        if not self.environment.strip():
            raise ValueError(
                "Configuration environment cannot be empty."
            )

        self.data = deepcopy(self.data)

    def get(
        self,
        path: str,
        default: Any = None,
    ) -> Any:
        """Retrieve a configuration value using a dot-separated path."""

        keys = self._parse_path(path)
        current: Any = self.data

        for key in keys:
            if not isinstance(current, dict) or key not in current:
                return default

            current = current[key]

        return current

    def require(self, path: str) -> Any:
        """Retrieve a required configuration value."""

        sentinel = object()
        value = self.get(path, sentinel)

        if value is sentinel:
            raise KeyError(
                f"Configuration path not found: {path}"
            )

        return value

    def set(
        self,
        path: str,
        value: Any,
    ) -> None:
        """Set a configuration value using a dot-separated path."""

        keys = self._parse_path(path)
        current = self.data

        for key in keys[:-1]:
            existing = current.get(key)

            if not isinstance(existing, dict):
                existing = {}
                current[key] = existing

            current = existing

        current[keys[-1]] = value

    def has(self, path: str) -> bool:
        """Check whether a configuration path exists."""

        sentinel = object()
        return self.get(path, sentinel) is not sentinel

    def remove(self, path: str) -> Any:
        """Remove and return a configuration value."""

        keys = self._parse_path(path)
        current = self.data

        for key in keys[:-1]:
            if not isinstance(current, dict) or key not in current:
                raise KeyError(
                    f"Configuration path not found: {path}"
                )

            current = current[key]

        final_key = keys[-1]

        if not isinstance(current, dict) or final_key not in current:
            raise KeyError(
                f"Configuration path not found: {path}"
            )

        return current.pop(final_key)

    def keys(self) -> list[str]:
        """Return top-level configuration keys."""

        return list(self.data.keys())

    def as_dict(self) -> dict[str, Any]:
        """Return a deep copy of the configuration data."""

        return deepcopy(self.data)

    def merge(
        self,
        values: dict[str, Any],
    ) -> None:
        """Deep-merge another configuration mapping into this one."""

        if not isinstance(values, dict):
            raise TypeError(
                "Configuration merge value must be a dictionary."
            )

        self._merge_dicts(self.data, values)

    def clear(self) -> None:
        """Remove all configuration values."""

        self.data.clear()

    def is_empty(self) -> bool:
        """Return whether the configuration contains no values."""

        return not self.data

    def load_environment(
        self,
        prefix: str = "CORE_",
    ) -> dict[str, Any]:
        """
        Load matching environment variables.

        Environment names are converted to lowercase dotted keys.

        Example:
            CORE_DATABASE_HOST -> database.host
            CORE_COMPONENTS__COMMUNICATION__ENABLED -> components.communication.enabled
        """

        if not prefix:
            raise ValueError("Environment prefix cannot be empty.")

        loaded: dict[str, Any] = {}

        for environment_key, value in os.environ.items():
            if not environment_key.startswith(prefix):
                continue

            config_key = environment_key[len(prefix):].lower()

            if not config_key:
                continue

            # Support both single-underscore and double-underscore separators:
            # CORE_DATABASE_HOST -> database.host
            # CORE_COMPONENTS__COMMUNICATION__ENABLED -> components.communication.enabled
            if "__" in config_key:
                config_key = config_key.replace("__", ".")
            else:
                config_key = config_key.replace("_", ".")

            # Coerce common string representations to typed values so
            # boolean flags like CORE_COMPONENTS__COMMUNICATION__ENABLED=false
            # correctly disable components.
            coerced: Any = value
            lowered = value.strip().lower()
            if lowered == "true":
                coerced = True
            elif lowered == "false":
                coerced = False
            elif lowered.isdigit() or (
                lowered.startswith("-") and lowered[1:].isdigit()
            ):
                try:
                    coerced = int(value.strip())
                except ValueError:
                    coerced = value
            else:
                # Attempt float for numeric-like strings without losing string intent
                try:
                    if "." in value.strip():
                        maybe_float = float(value.strip())
                        # Only coerce if the original string looks like a number
                        if str(maybe_float) == value.strip() or value.strip().replace(".", "", 1).lstrip("-").isdigit():
                            coerced = maybe_float
                except ValueError:
                    pass

            loaded[config_key] = coerced  # type: ignore[assignment]
            self.set(config_key, coerced)

        return loaded

    @staticmethod
    def _parse_path(path: str) -> list[str]:
        """Validate and split a dot-separated configuration path."""

        if not isinstance(path, str):
            raise TypeError(
                "Configuration path must be a string."
            )

        if not path.strip():
            raise ValueError(
                "Configuration path cannot be empty."
            )

        keys = path.split(".")

        if any(not key.strip() for key in keys):
            raise ValueError(
                f"Invalid configuration path: {path}"
            )

        return keys

    @classmethod
    def _merge_dicts(
        cls,
        target: dict[str, Any],
        source: dict[str, Any],
    ) -> None:
        """Recursively merge source into target."""

        for key, value in source.items():
            if (
                key in target
                and isinstance(target[key], dict)
                and isinstance(value, dict)
            ):
                cls._merge_dicts(target[key], value)
            else:
                target[key] = deepcopy(value)