"""Loguru + stdlib logging configuration with JWT redaction on every sink."""

from __future__ import annotations

import logging
import sys
from typing import Any

from loguru import logger

from src.kuvera_client import JWT_PATTERN, TokenRedactionFilter

REDACTED = "[REDACTED]"


def redact(text: str) -> str:
    return JWT_PATTERN.sub(REDACTED, text)


def loguru_redaction_filter(record: dict[str, Any]) -> bool:
    """Loguru sink filter: scrub JWT-shaped strings from the formatted message and extras."""
    record["message"] = redact(str(record["message"]))
    for key, value in list(record["extra"].items()):
        if isinstance(value, str):
            record["extra"][key] = redact(value)
    return True


def configure_logging(level: str = "INFO") -> None:
    """Route loguru to stderr through the redaction filter and protect stdlib loggers too."""
    level = level.upper()
    logger.remove()
    logger.add(sys.stderr, level=level, filter=loguru_redaction_filter, backtrace=False)

    root = logging.getLogger()
    if not any(isinstance(f, TokenRedactionFilter) for h in root.handlers for f in h.filters):
        handler = logging.StreamHandler(sys.stderr)
        handler.addFilter(TokenRedactionFilter())
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    root.setLevel(level)
    # httpx logs every request URL at INFO; keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
