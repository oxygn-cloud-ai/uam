"""Logging configuration for uam."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path.home() / ".uam"


def setup_logging() -> None:
    """Configure Python logging for uam.

    - Reads UAM_LOG_LEVEL env var (default: WARNING)
    - Creates ~/.uam/ dir if needed
    - Adds RotatingFileHandler to ~/.uam/uam.log
    - Format: %(asctime)s %(levelname)s %(name)s %(message)s
    """
    level_name = os.environ.get("UAM_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("uam")
    logger.setLevel(level)

    # Avoid adding duplicate handlers on repeated calls
    for h in logger.handlers[:]:
        if isinstance(h, RotatingFileHandler):
            logger.removeHandler(h)

    handler = RotatingFileHandler(
        LOG_DIR / "uam.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
    )
    handler.setLevel(level)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def redact_headers(headers: dict) -> dict:
    """Return a copy of headers with sensitive values replaced by [REDACTED]."""
    sensitive = {"x-api-key", "authorization"}
    result = {}
    for key, value in headers.items():
        if key.lower() in sensitive:
            result[key] = "[REDACTED]"
        else:
            result[key] = value
    return result
