import logging

from core.logging import CoreLogger


def test_logger_initializes():
    logger = CoreLogger("test")

    assert logger is not None


def test_logger_levels():
    logger = CoreLogger("test_levels")

    logger.debug("debug")
    logger.info("info")
    logger.warning("warning")
    logger.error("error")
    logger.critical("critical")


def test_logger_set_level():
    logger = CoreLogger("test_set_level")

    logger.set_level(logging.DEBUG)

    assert logger._logger.level == logging.DEBUG


def test_logger_has_handler():
    logger = CoreLogger("test_handler")

    assert len(logger._logger.handlers) > 0
