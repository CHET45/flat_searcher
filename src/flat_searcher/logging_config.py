"""Logging setup."""

from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(level: int = logging.INFO, log_file: Path | None = None) -> None:
    """Configure console logging and, when provided, an append-only log file."""

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s [%(threadName)s]: %(message)s",
        handlers=handlers,
        force=True,
    )
