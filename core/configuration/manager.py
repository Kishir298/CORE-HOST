from pathlib import Path
from threading import RLock
from typing import Any

from .loader import ConfigurationLoader
from .models import Configuration
from .validator import ConfigurationValidator


class ConfigurationManager:
    """
    Public operational interface for C.O.R.E. configuration.

    ConfigurationManager owns the currently active configuration and
    coordinates loading, validation, updates, and lifecycle state.
    """

    def __init__(
        self,
        loader: ConfigurationLoader | None = None,
        validator: ConfigurationValidator | None = None,
    ) -> None:
        self._loader = loader or ConfigurationLoader()
        self._validator = validator or ConfigurationValidator()
        self._configuration: Configuration | None = None

        self._lock = RLock()
        self._active = True

        self._load_count = 0
        self._validation_count = 0

    def start(self) -> None:
        """Start the configuration manager."""

        with self._lock:
            self._active = True

    def stop(self) -> None:
        """Stop the configuration manager."""

        with self._lock:
            self._active = False

    @property
    def is_running(self) -> bool:
        """Return whether the configuration manager is active."""

        with self._lock:
            return self._active

    def load(
        self,
        path: str | Path,
        environment: str = "development",
    ) -> Configuration:
        """Load and validate configuration from a YAML file."""

        with self._lock:
            self._require_active()

        configuration = self._loader.load(
            path,
            environment=environment,
        )

        self._validator.validate(configuration)

        with self._lock:
            self._configuration = configuration
            self._load_count += 1
            self._validation_count += 1

        return configuration

    def load_environment(
        self,
        prefix: str = "CORE_",
    ) -> dict[str, Any]:
        """Load matching environment variables into the active configuration."""

        with self._lock:
            self._require_active()
            if self._configuration is None:
                # No file loaded yet — start from an empty configuration so
                # environment overrides can still drive component policy.
                self._configuration = Configuration(
                    data={},
                    environment="development",
                )

            return self._configuration.load_environment(prefix=prefix)

    def set(self, path: str, value: Any) -> None:
        """Set a configuration value."""

        with self._lock:
            self._require_active()
            configuration = self._require_configuration()
            configuration.set(path, value)

    def get(
        self,
        path: str,
        default: Any = None,
    ) -> Any:
        """Get a configuration value."""

        with self._lock:
            self._require_active()
            configuration = self._require_configuration()
            return configuration.get(path, default)

    def has(self, path: str) -> bool:
        """Check whether a configuration value exists."""

        with self._lock:
            self._require_active()
            configuration = self._require_configuration()
            return configuration.has(path)

    def validate(self) -> bool:
        """
        Validate the currently loaded configuration.

        Returns True when validation succeeds. Validation errors raised by
        the validator are intentionally allowed to propagate.
        """

        with self._lock:
            self._require_active()
            configuration = self._require_configuration()

        self._validator.validate(configuration)

        with self._lock:
            self._validation_count += 1

        return True

    def current(self) -> Configuration:
        """Return the currently loaded configuration."""

        with self._lock:
            self._require_active()
            return self._require_configuration()

    def snapshot(self) -> dict[str, Any]:
        """Return a copy of the currently loaded configuration."""

        with self._lock:
            self._require_active()
            configuration = self._require_configuration()
            return configuration.as_dict()

    def clear(self) -> None:
        """Remove the currently loaded configuration."""

        with self._lock:
            self._require_active()
            self._configuration = None

    def load_count(self) -> int:
        """Return the number of successful configuration loads."""

        with self._lock:
            return self._load_count

    def validation_count(self) -> int:
        """Return the number of successful validation operations."""

        with self._lock:
            return self._validation_count

    def _require_active(self) -> None:
        """Ensure configuration operations are currently allowed."""

        if not self._active:
            raise RuntimeError(
                "Configuration manager is not running."
            )

    def _require_configuration(self) -> Configuration:
        """Return the active configuration or raise."""

        if self._configuration is None:
            raise RuntimeError(
                "No configuration has been loaded."
            )

        return self._configuration