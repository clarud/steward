"""Central logging setup for the Steward application."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

from steward.config import Settings


LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# Telegram puts the bot token in every API URL (https://api.telegram.org/bot<id>:<secret>/...).
_BOT_TOKEN = re.compile(r"bot\d+:[A-Za-z0-9_-]{20,}")
# Libraries that log full request URLs at INFO.
_URL_LOGGERS = ("httpx", "httpcore")


def redact_secrets(text: str) -> str:
    return _BOT_TOKEN.sub("bot<redacted>", text)


class RedactingFormatter(logging.Formatter):
    """Never write a Telegram bot token to a console or log file."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


def configure_logging(settings: Settings) -> None:
    """Configure console logging plus bounded local operational logs.

    Logs are operational metadata, not a source store: rotation prevents a
    long-running Telegram process from consuming unbounded disk space.
    """
    formatter = RedactingFormatter(LOG_FORMAT)
    logging.basicConfig(level=settings.log_level, format=LOG_FORMAT)
    for name in _URL_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    log_path = settings.data_dir / "logs" / "steward.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(settings.log_level)
    for handler in root.handlers:
        handler.setFormatter(formatter)
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
