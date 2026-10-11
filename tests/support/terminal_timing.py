import logging

import pytest


@pytest.fixture
def terminal_stage_logs(caplog):
    """Capture the isolated process sink without changing ordinary app loggers."""
    logger = logging.getLogger("app.platform.terminal_timing")
    previous_level, previous_propagate = logger.level, logger.propagate
    previous_handlers = tuple(logger.handlers)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(caplog.handler)
    try:
        yield
    finally:
        logger.removeHandler(caplog.handler)
        for handler in tuple(logger.handlers):
            if handler not in previous_handlers:
                logger.removeHandler(handler)
                handler.close()
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
