from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "spatial_engine"


def log_path() -> Path:
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"])
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return (base / "SpatialAnalysis" / "logs" / "engine.log").resolve()


def configure_logging() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    destination = log_path()
    current = getattr(logger, "_spatial_log_path", None)
    if current == str(destination):
        return logger
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    destination.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler = RotatingFileHandler(
        destination,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stderr_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger._spatial_log_path = str(destination)  # type: ignore[attr-defined]
    return logger
