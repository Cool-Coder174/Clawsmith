"""Logging setup — configures Rich-based console + file logging for ClawSmith.

Call ``setup_logging()`` once at startup (the CLI does this automatically).
Use ``get_logger(name)`` anywhere else to obtain a child logger.  Log level
defaults to INFO and can be overridden with the ``LOG_LEVEL`` env variable.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from rich.logging import RichHandler

_REPO_ROOT = Path(__file__).parent.parent


def setup_logging(
    log_level: str | None = None,
    log_file: Path | None = None,
) -> logging.Logger:
    """Configure and return the root ``clawsmith`` logger.

    * Console output uses :class:`rich.logging.RichHandler` for colour and tracebacks.
    * File output goes to *log_file* (default ``logs/clawsmith.log``).
    """
    level = (log_level or os.environ.get("LOG_LEVEL", "INFO")).upper()

    if log_file is None:
        log_file = _REPO_ROOT / "logs" / "clawsmith.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger("clawsmith")
    root_logger.setLevel(level)

    if not root_logger.handlers:
        console_handler = RichHandler(
            show_path=False,
            rich_tracebacks=True,
            markup=True,
        )
        console_handler.setLevel(level)
        root_logger.addHandler(console_handler)

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root_logger.addHandler(file_handler)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``clawsmith`` namespace."""
    return logging.getLogger(f"clawsmith.{name}")


logger = setup_logging()
