import logging
import sys


class CoreLogger:
    """Centralized logging system for C.O.R.E."""

    def __init__(
        self,
        name: str = "core",
        level: int = logging.INFO,
    ) -> None:
        self._logger = logging.getLogger(name)
        self._logger.setLevel(level)
        self._logger.propagate = False

        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setLevel(level)

            formatter = logging.Formatter(
                "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
                datefmt="%H:%M:%S",
            )

            handler.setFormatter(formatter)
            self._logger.addHandler(handler)

    def debug(self, message: str) -> None:
        self._logger.debug(message)

    def info(self, message: str) -> None:
        self._logger.info(message)

    def warning(self, message: str) -> None:
        self._logger.warning(message)

    def error(self, message: str) -> None:
        self._logger.error(message)

    def critical(self, message: str) -> None:
        self._logger.critical(message)

    def set_level(self, level: int) -> None:
        self._logger.setLevel(level)

        for handler in self._logger.handlers:
            handler.setLevel(level)
