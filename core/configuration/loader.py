from pathlib import Path
from typing import Any

import yaml

from .models import Configuration


class ConfigurationLoader:
    """
    Loads C.O.R.E. configuration from YAML files.

    The loader is deliberately responsible only for reading and
    constructing Configuration objects. Validation remains the
    responsibility of ConfigurationValidator.
    """

    SUPPORTED_SUFFIXES = {".yaml", ".yml"}

    def load(
        self,
        path: str | Path,
        environment: str = "development",
    ) -> Configuration:
        """Load a configuration file."""

        config_path = Path(path)

        self._validate_environment(environment)
        self._validate_path(config_path)

        with config_path.open("r", encoding="utf-8") as file:
            data: Any = yaml.safe_load(file)

        if data is None:
            data = {}

        if not isinstance(data, dict):
            raise ValueError(
                "Configuration root must be a mapping."
            )

        return Configuration(
            data=data,
            environment=environment,
        )

    def load_text(
        self,
        content: str,
        environment: str = "development",
    ) -> Configuration:
        """Load configuration directly from YAML text."""

        if not isinstance(content, str):
            raise TypeError(
                "Configuration content must be a string."
            )

        self._validate_environment(environment)

        data: Any = yaml.safe_load(content)

        if data is None:
            data = {}

        if not isinstance(data, dict):
            raise ValueError(
                "Configuration root must be a mapping."
            )

        return Configuration(
            data=data,
            environment=environment,
        )

    def exists(self, path: str | Path) -> bool:
        """Return whether a configuration file exists."""

        return Path(path).is_file()

    def supported(self, path: str | Path) -> bool:
        """Return whether the file uses a supported YAML extension."""

        return Path(path).suffix.lower() in self.SUPPORTED_SUFFIXES

    def _validate_path(self, config_path: Path) -> None:
        """Validate a configuration file path."""

        if not config_path.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {config_path}"
            )

        if not config_path.is_file():
            raise ValueError(
                f"Configuration path is not a file: {config_path}"
            )

        if not self.supported(config_path):
            raise ValueError(
                "Unsupported configuration file format: "
                f"{config_path.suffix or '<none>'}"
            )

    @staticmethod
    def _validate_environment(environment: str) -> None:
        """Validate a configuration environment name."""

        if not isinstance(environment, str):
            raise TypeError(
                "Configuration environment must be a string."
            )

        if not environment.strip():
            raise ValueError(
                "Configuration environment cannot be empty."
            )