"""Central logging setup for the Steward application."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from steward.config import Settings


LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(settings: Settings) -> None:
    """Configure console logging plus bounded local operational logs.

    Logs are operational metadata, not a source store: rotation prevents a
    long-running Telegram process from consuming unbounded disk space.
    """
    formatter = logging.Formatter(LOG_FORMAT)
    logging.basicConfig(
        level=settings.log_level,
        format=LOG_FORMAT,
    )
    log_path = settings.data_dir / "logs" / "steward.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(settings.log_level)
    resolved = log_path.resolve()
    for handler in root.handlers:
        if getattr(handler, "_steward_log_path", None) == resolved:
            handler.setLevel(settings.log_level)
            return
    file_handler = RotatingFileHandler(
        resolved, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler._steward_log_path = resolved  # type: ignore[attr-defined]
    file_handler.setLevel(settings.log_level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
