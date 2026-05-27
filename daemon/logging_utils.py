"""
Shared logging helpers for Ravens Perch.
"""
import logging
from typing import Optional


LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def resolve_log_level(level_name: Optional[str], default: str = "INFO") -> str:
    """Normalize a configured log level name."""
    candidate = str(level_name or default).upper()
    if candidate in LOG_LEVELS:
        return candidate
    return default.upper() if default.upper() in LOG_LEVELS else "INFO"


def apply_log_level(level_name: Optional[str], default: str = "INFO") -> str:
    """Apply a log level to existing root handlers and return the normalized name."""
    normalized = resolve_log_level(level_name, default)
    level = LOG_LEVELS[normalized]

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    for handler in root_logger.handlers:
        handler.setLevel(level)

    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    return normalized
